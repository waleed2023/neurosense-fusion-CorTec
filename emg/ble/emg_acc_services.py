import asyncio
import sys
import time
from typing import Optional, Callable, Awaitable
from emg.broadcaster import Broadcaster
from emg.measurment.bandwidth import BandwidthTracker
from emg.proto import emg_pb2, fusion_pb2
from .gatt_core import GattService, NotifyChar, WriteChar, RpcStream
from emg.config import EMG_SVC, EMG_PARAM, EMG_RET, ACC_SVC, ACC_PARAM, ACC_RET, FUSION_SVC, FUSION_PARAM, FUSION_RET, MAX_RATE_HZ_ACC, MAX_RATE_HZ_EMG, MAX_RATE_HZ_FUSION, LOG_PREFIX, SENSOR_METRICS_LOG_FILE
from emg.measurment.jitter_tracker import JitterTracker
from emg.measurment.delay_tracker import DelayTracker




def now_ms() -> int:
    return int(time.time() * 1000)


# --------- Streaming-Loops (lesen vom Broadcaster, senden via Notify) ---------

async def _emg_stream_loop(
    *, send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    req = emg_pb2.SubscribeRequest()
    try:
        req.ParseFromString(req_bytes)
    except Exception:
        req.max_rate_hz = MAX_RATE_HZ_EMG # fallback max rate
    interval_s = 1.0 / max(1, req.max_rate_hz)
    last = 0.0
    loop = asyncio.get_running_loop()
    emg_jitter = JitterTracker(nominal_period_ms=1000.0 / float(req.max_rate_hz), name="EMG_HAL_OUT")  # jitter tracker for outgoing EMG stream
    emg_delay = DelayTracker(name="EMG_HAL_DELAY")  # delay tracker for outgoing EMG stream
    emg_sensor_delay = DelayTracker(
        name="EMG_SENSOR_HAL_DELAY",
        log_file=SENSOR_METRICS_LOG_FILE,
    )
    tracker = BandwidthTracker("EMG-HAL->App", log_interval_s=20.0)
    q: asyncio.Queue = await broadcaster.subscribe()
    try:
        while True:
            sample: emg_pb2.EmgSample = await q.get()
            now = loop.time()
            if now - last >= interval_s:
                last = now
                # Track bandwidth usage
                payload = sample.SerializeToString()
                tracker.add(len(payload))

                # delay and jitter tracking
                send_ts_ms = now_ms()
                emg_jitter.add(send_ts_ms)
                age_ms = send_ts_ms - sample.ts_ms
                emg_delay.add(age_ms, ts_ms=send_ts_ms)
                emg_sensor_delay.add(age_ms, ts_ms=send_ts_ms)

                await send_bytes(payload)
    except asyncio.CancelledError:
        sys.stdout.write(f"{LOG_PREFIX} EMG-Streaming stopped\n")
        raise
    finally:
        await broadcaster.unsubscribe(q)
        #emg_jitter._log_jitter_summary()
        #emg_delay.log_summary()


async def _acc_stream_loop(
    *, send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    req = emg_pb2.AccelSubscribeRequest()
    try:
        req.ParseFromString(req_bytes)
    except Exception:
        req.max_rate_hz = MAX_RATE_HZ_ACC
    interval_s = 1.0 / max(1, req.max_rate_hz)
    last = 0.0
    loop = asyncio.get_running_loop()
    acc_jitter = JitterTracker(nominal_period_ms=1000.0 / float(req.max_rate_hz), name="ACC_HAL_OUT")  # jitter tracker for outgoing Accel stream
    acc_delay = DelayTracker(name="ACC_HAL_DELAY")  # delay tracker for outgoing Accel stream
    acc_sensor_delay = DelayTracker(
        name="ACC_SENSOR_HAL_DELAY",
        log_file=SENSOR_METRICS_LOG_FILE,
    )
    tracker = BandwidthTracker("ACC-HAL->App", log_interval_s=20.0)
    q: asyncio.Queue = await broadcaster.subscribe()
    try:
        while True:
            sample: emg_pb2.AccelSample = await q.get()


            now = loop.time()
            if now - last >= interval_s:
                last = now
                # Track bandwidth usage
                payload = sample.SerializeToString()
                tracker.add(len(payload))

                send_ts_ms = now_ms()
                acc_jitter.add(send_ts_ms)
                age_ms = send_ts_ms - sample.ts_ms
                acc_delay.add(age_ms, ts_ms=send_ts_ms)
                acc_sensor_delay.add(age_ms, ts_ms=send_ts_ms)

                await send_bytes(payload)
    except asyncio.CancelledError:
        sys.stdout.write(f"{LOG_PREFIX} Accel-Streaming stopped\n")
        raise
    finally:
        await broadcaster.unsubscribe(q)
        #acc_jitter._log_jitter_summary()
        #acc_delay.log_summary()

async def _fusion_stream_loop(
    *,
    send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    """
    Reads FusedSample objects from the fusion broadcaster and sends them
    via send_bytes(), optionally throttled by max_rate_hz in the request.
    """
    req = fusion_pb2.FusionSubscribeRequest()
    try:
        req.ParseFromString(req_bytes)
    except Exception:
        # Fallback: default rate if client sends garbage or nothing
        req.max_rate_hz = MAX_RATE_HZ_FUSION

    max_rate = max(1, req.max_rate_hz)
    interval_s = 1.0 / float(max_rate)

    loop = asyncio.get_running_loop()
    last = 0.0
    fusion_jitter = JitterTracker(nominal_period_ms=1000.0 / float(req.max_rate_hz), name="FUSION_HAL_OUT")  # jitter tracker for outgoing Fusion stream
    fusion_delay = DelayTracker(name="FUSION_HAL_DELAY")  # delay tracker for outgoing Fusion stream

    q: asyncio.Queue = await broadcaster.subscribe()
    tracker = BandwidthTracker("FUSION-HAL->App", log_interval_s=20.0)
    try:
        while True:
            sample: fusion_pb2.FusedSample = await q.get()

            
            now = loop.time()
            if (now - last) < interval_s:
                # drop this sample to enforce max_rate_hz
                continue
            last = now
            # Track bandwidth usage
            payload = sample.SerializeToString()
            tracker.add(len(payload))

            send_ts = now_ms()
            fusion_jitter.add(send_ts)

            # Delay: wie alt ist das Fusions-Fenster?
            # sample.ts_ms = window_end aus FusionManager
            age_ms = send_ts - sample.ts_ms
            fusion_delay.add(age_ms, ts_ms=send_ts)
            await send_bytes(payload)
    finally:
        await broadcaster.unsubscribe(q)
        #fusion_jitter._log_jitter_summary()
        #fusion_delay.log_summary()


# ------------------------------- Services ------------------------------------

def build_emg_service(bus, emg_broadcaster: Broadcaster) -> GattService:
    svc = GattService("/app/emg", EMG_SVC)
    ret_char = NotifyChar("/app/emg/ret", EMG_RET, svc) # Notify (EmgSample)
    stream: Optional[RpcStream] = None                  # current RpcStream

    async def on_param(data: bytes) -> None:
        """ Handle SubscribeRequest from client → (re)start stream."""
        nonlocal stream
        if stream:
            stream.stop()
        async def producer(_send):
            async def send(b: bytes):  # Adapter auf ret_char.notify
                await ret_char.notify(b)
            await _emg_stream_loop(send_bytes=send, broadcaster=emg_broadcaster, req_bytes=data)
        stream = RpcStream(producer)
        stream.start()

    param_char = WriteChar("/app/emg/param", EMG_PARAM, svc, on_param)
    svc.chars += [param_char, ret_char]
    return svc

def build_acc_service(bus, acc_broadcaster: Broadcaster) -> GattService:
    svc = GattService("/app/acc", ACC_SVC)
    ret_char = NotifyChar("/app/acc/ret", ACC_RET, svc)
    stream: Optional[RpcStream] = None

    async def on_param(data: bytes) -> None:
        nonlocal stream
        if stream:
            stream.stop()
        async def producer(_send):
            async def send(b: bytes):
                await ret_char.notify(b)
            await _acc_stream_loop(send_bytes=send, broadcaster=acc_broadcaster, req_bytes=data)
        stream = RpcStream(producer)
        stream.start()

    param_char = WriteChar("/app/acc/param", ACC_PARAM, svc, on_param)
    svc.chars += [param_char, ret_char]
    return svc

def build_fusion_service(bus, fusion_broadcaster: Broadcaster) -> GattService:
    """
    Build a BLE GATT service that exposes the Fusion stream:

      - Write FusionSubscribeRequest (max_rate_hz) to FUSION_PARAM
      - Receive FusedSample notifications on FUSION_RET
    """
    svc = GattService("/app/fusion", FUSION_SVC, primary=True)

    # Notify characteristic: sends FusedSample bytes to the client
    ret_char = NotifyChar("/app/fusion/ret", FUSION_RET, svc)

    stream: Optional[RpcStream] = None

    async def on_param(data: bytes) -> None:
        """
        Called when the client writes a FusionSubscribeRequest to FUSION_PARAM.
        Starts (or restarts) the streaming loop with the new parameters.
        """
        nonlocal stream
        if stream:
            stream.stop()

        async def producer(_send: Callable[[bytes], Awaitable[None]]) -> None:
            async def send(b: bytes) -> None:
                await ret_char.notify(b)

            await _fusion_stream_loop(
                send_bytes=send,
                broadcaster=fusion_broadcaster,
                req_bytes=data,
            )

        stream = RpcStream(producer)
        stream.start()

    # Write characteristic for subscribe request (FusionSubscribeRequest)
    param_char = WriteChar("/app/fusion/param", FUSION_PARAM, svc, on_param)

    svc.chars += [param_char, ret_char]
    return svc
