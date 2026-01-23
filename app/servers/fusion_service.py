import asyncio
from typing import Optional, Callable, Awaitable

from app.manager.broadcaster import Broadcaster
from app.measurment.bandwidth import BandwidthTracker
from app.proto import fusion_pb2
from .gatt_core import GattService, NotifyChar, WriteChar, RpcStream
from app.proto import fusion_pb2
from app.proto import fusion_dummy_pb2

from app.manager.config import (
    FUSION_SVC,
    FUSION_PARAM,
    FUSION_RET,
    MAX_RATE_HZ_FUSION,
)


async def _fusion_stream_loop(
    *,
    send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    """
    Reads FusedSample objects from the fusion broadcaster and sends them via send_bytes().

    Throttling is driven by the sample timestamps (sample.ts_us), NOT by loop.time().
    This keeps correct timing even if fusion produces bursts (catch-up).
    """
    req = fusion_pb2.FusionSubscribeRequest() # create a FusionSubscribeRequest object
    try:
        req.ParseFromString(req_bytes) # parse the subscription request from bytes
    except Exception:
        req.max_rate_hz = MAX_RATE_HZ_FUSION # default max rate if parsing fails, this varible used to throttle the output if needed

    max_rate = int(getattr(req, "max_rate_hz", 0)) # in my case 833hz, not shure if the output send 833 samples per second (need to measure)
    if max_rate <= 0:
        interval_us = 0  # no throttling
    else:
        interval_us = int(1_000_000 / float(max_rate)) # in microseconds, e.g. 1200us for 833hz

    # last sent sample timestamp (ts_us)
    last_sent_ts_us: Optional[int] = None

    # subscribe to fusion broadcaster
    q: asyncio.Queue = await broadcaster.subscribe()
    tracker = BandwidthTracker("FUSION-HAL->App", log_interval_s=20.0) # bandwidth tracker

    try:
        while True:
            # get next sample
            sample: fusion_pb2.FusedSample = await q.get()
            # timestamp of the sample from Fusion
            ts_us = int(getattr(sample, "ts_us", 0))
            # If ts_us missing/zero, fall back to sending (don't block)
            if interval_us > 0 and ts_us > 0 and last_sent_ts_us is not None:
                dt = ts_us - last_sent_ts_us # time difference since last sent sample
                # if dt < interval_us, skip this sample (throttle)


                # handle timestamp reset/backwards
                if dt < 0:
                    last_sent_ts_us = ts_us
                elif dt < interval_us:
                    continue

            if ts_us > 0:
                last_sent_ts_us = ts_us
            # serialize and send the sample
            payload = sample.SerializeToString()
            tracker.add(len(payload)) # for bandwidth tracking
            await send_bytes(payload) # send the serialized sample to the client in bytes

    finally:
        await broadcaster.unsubscribe(q) # unsubscribe on exit


def build_fusion_service(bus, fusion_broadcaster: Broadcaster) -> GattService:
    """
    BLE GATT service:
      - client writes FusionSubscribeRequest to FUSION_PARAM
      - device notifies FusedSample bytes on FUSION_RET
    """
    svc = GattService("/app/fusion", FUSION_SVC, primary=True) # uuid=FUSION_SVC, configured as primary

    ret_char = NotifyChar("/app/fusion/ret", FUSION_RET, svc) # uuid=FUSION_RET characteristic for notifications
    stream: Optional[RpcStream] = None # RPC stream for handling requests

    # Callback when client writes to FUSION_PARAM
    async def on_param(data: bytes) -> None: 
        nonlocal stream # stop existing stream if any
        if stream:
            stream.stop()

        # Define producer function for the RPC stream
        async def producer(_send: Callable[[bytes], Awaitable[None]]) -> None:
            async def send(b: bytes) -> None:
                await ret_char.notify(b)
            # Start the fusion stream loop 
            await _fusion_stream_loop(
                send_bytes=send,
                broadcaster=fusion_broadcaster,
                req_bytes=data,
            )

        stream = RpcStream(producer) # create new RPC stream with the producer
        stream.start()

    param_char = WriteChar("/app/fusion/param", FUSION_PARAM, svc, on_param) # uuid=FUSION_PARAM characteristic for writes
    svc.chars += [param_char, ret_char] # add characteristics to the service
    return svc # return the constructed GATT service



########################### dummy fusion service ###########################

async def _fusion_minimal_stream_loop(
    *,
    send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster,
    req_bytes: bytes,
) -> None:
    """
    Batching: collects multiple FusedMinimalSample into one FusedMinimalPacket and notifies that.
    Flush rules:
      - flush when max_samples reached, OR
      - flush when buffer_age >= interval (wall-clock), OR
      - flush on timestamp reset/backwards (optional safety)
    """

    req = fusion_dummy_pb2.FusionMinimalSubscribeRequest()
    try:
        req.ParseFromString(req_bytes)
    except Exception:
        # sane defaults if parsing fails
        req.max_rate_hz = MAX_RATE_HZ_FUSION
        req.max_samples = 10

    packet_rate_hz = int(getattr(req, "max_rate_hz", 0))
    max_samples = int(getattr(req, "max_samples", 0)) or 10

    # interval = max time we keep a partially filled packet (latency cap)
    interval_s = 0.0 if packet_rate_hz <= 0 else (1.0 / float(packet_rate_hz))

    q: asyncio.Queue = await broadcaster.subscribe()
    tracker = BandwidthTracker("FUSION-MIN-PACK-HAL->App", log_interval_s=20.0)

    buf: list[fusion_dummy_pb2.FusedMinimalSample] = []
    packet_started_t: Optional[float] = None  # monotonic time
    last_ts_us: Optional[int] = None          # for backwards/reset detection (optional)

    loop = asyncio.get_running_loop()

    async def flush() -> None:
        nonlocal packet_started_t, last_ts_us
        if not buf:
            packet_started_t = None
            last_ts_us = None
            return

        pkt = fusion_dummy_pb2.FusedMinimalPacket()
        pkt.samples.extend(buf)     # avoids any aliasing surprises
        payload = pkt.SerializeToString()

        tracker.add(len(payload))
        await send_bytes(payload)

        buf.clear()
        packet_started_t = None
        last_ts_us = None

    try:
        while True:
            # 1) If buffer empty, block until first sample arrives
            if not buf:
                s = await q.get()
                ts_us = int(getattr(s, "ts_us", 0))

                buf.append(s)
                packet_started_t = loop.time()
                last_ts_us = ts_us if ts_us > 0 else None

            # 2) Drain whatever is already available WITHOUT await (big speedup)
            while len(buf) < max_samples:
                try:
                    s = q.get_nowait()
                except asyncio.QueueEmpty:
                    break

                ts_us = int(getattr(s, "ts_us", 0))

                # optional: if timestamps go backwards -> flush current packet first
                if ts_us > 0 and last_ts_us is not None and ts_us < last_ts_us:
                    await flush()
                    # start a new packet with current sample
                    buf.append(s)
                    packet_started_t = loop.time()
                    last_ts_us = ts_us
                    continue

                buf.append(s)
                if ts_us > 0:
                    last_ts_us = ts_us

                if len(buf) >= max_samples:
                    break

            # 3) Flush if size cap hit
            if len(buf) >= max_samples:
                await flush()
                continue

            # 4) Flush if latency cap hit (wall-clock)
            if interval_s > 0.0 and packet_started_t is not None:
                age = loop.time() - packet_started_t
                if age >= interval_s:
                    await flush()
                    continue

                # 5) Otherwise wait until:
                #    - next sample arrives, OR
                #    - remaining time to interval expires (then flush)
                remaining = interval_s - age
                try:
                    s = await asyncio.wait_for(q.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    await flush()
                    continue

                ts_us = int(getattr(s, "ts_us", 0))

                if ts_us > 0 and last_ts_us is not None and ts_us < last_ts_us:
                    await flush()
                    buf.append(s)
                    packet_started_t = loop.time()
                    last_ts_us = ts_us
                    continue

                buf.append(s)
                if ts_us > 0:
                    last_ts_us = ts_us

            else:
                # No interval configured -> just wait for next sample (size-based batching only)
                s = await q.get()
                ts_us = int(getattr(s, "ts_us", 0))

                if ts_us > 0 and last_ts_us is not None and ts_us < last_ts_us:
                    await flush()
                    buf.append(s)
                    packet_started_t = loop.time()
                    last_ts_us = ts_us
                    continue

                buf.append(s)
                if packet_started_t is None:
                    packet_started_t = loop.time()
                if ts_us > 0:
                    last_ts_us = ts_us

    finally:
        try:
            await flush()
        except Exception:
            pass
        await broadcaster.unsubscribe(q)


def build_fusion_minimal_service(bus, fusion_broadcaster: Broadcaster) -> GattService:
    svc = GattService("/app/fusion", FUSION_SVC, primary=True)

    ret_char = NotifyChar("/app/fusion/ret", FUSION_RET, svc)
    stream: Optional[RpcStream] = None

    async def on_param(data: bytes) -> None:
        nonlocal stream
        if stream:
            stream.stop()

        async def producer(_send: Callable[[bytes], Awaitable[None]]) -> None:
            async def send(b: bytes) -> None:
                await ret_char.notify(b)

            await _fusion_minimal_stream_loop(
                send_bytes=send,
                broadcaster=fusion_broadcaster,
                req_bytes=data,
            )

        stream = RpcStream(producer)
        stream.start()

    param_char = WriteChar("/app/fusion/param", FUSION_PARAM, svc, on_param)
    svc.chars += [param_char, ret_char]
    return svc
