import asyncio
import sys
import time
from typing import Optional, Callable, Awaitable
from emg.broadcaster import Broadcaster
from emg.measurment.bandwidth import BandwidthTracker
from emg.proto import emg_pb2, fusion_pb2
from .gatt_core import GattService, NotifyChar, WriteChar, RpcStream
from emg.config import EMG_SVC, EMG_PARAM, EMG_RET, ACC_SVC, ACC_PARAM, ACC_RET, FUSION_SVC, FUSION_PARAM, FUSION_RET, MAX_RATE_HZ_ACC, MAX_RATE_HZ_EMG, MAX_RATE_HZ_FUSION, LOG_PREFIX, SENSOR_METRICS_LOG_FILE



def now_ms() -> int:
    return int(time.time() * 1000)


# helpers for Fusion stream
async def _fusion_stream_loop(
    *,
    send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    """
    Reads FusedSample objects from the fusion broadcaster and sends them
    via send_bytes().
    throttle is defined by FusionSubscribeRequest.max_rate_hz in proto/fusion.proto, MAX_RATE_HZ_FUSION defined in config.py
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

            await send_bytes(payload)
    finally:
        await broadcaster.unsubscribe(q)


# Build Fusion Service
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
