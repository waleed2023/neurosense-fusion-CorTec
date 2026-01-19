import asyncio
from typing import Optional, Callable, Awaitable

from app.manager.broadcaster import Broadcaster
from app.measurment.bandwidth import BandwidthTracker
from app.proto import fusion_pb2
from .gatt_core import GattService, NotifyChar, WriteChar, RpcStream
from app.proto import fusion_dummy_pb2 as fusion_minimal_pb2
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
    req = fusion_pb2.FusionSubscribeRequest()
    try:
        req.ParseFromString(req_bytes)
    except Exception:
        req.max_rate_hz = MAX_RATE_HZ_FUSION

    max_rate = int(getattr(req, "max_rate_hz", 0))
    if max_rate <= 0:
        interval_us = 0  # no throttling
    else:
        interval_us = int(1_000_000 / float(max_rate))

    last_sent_ts_us: Optional[int] = None

    q: asyncio.Queue = await broadcaster.subscribe()
    tracker = BandwidthTracker("FUSION-HAL->App", log_interval_s=20.0)

    try:
        while True:
            sample: fusion_pb2.FusedSample = await q.get()

            ts_us = int(getattr(sample, "ts_us", 0))

            # If ts_us missing/zero, fall back to sending (don't block)
            if interval_us > 0 and ts_us > 0 and last_sent_ts_us is not None:
                dt = ts_us - last_sent_ts_us

                # handle timestamp reset/backwards
                if dt < 0:
                    last_sent_ts_us = ts_us
                elif dt < interval_us:
                    continue

            if ts_us > 0:
                last_sent_ts_us = ts_us

            payload = sample.SerializeToString()
            tracker.add(len(payload))
            await send_bytes(payload)

    finally:
        await broadcaster.unsubscribe(q)


def build_fusion_service(bus, fusion_broadcaster: Broadcaster) -> GattService:
    """
    BLE GATT service:
      - client writes FusionSubscribeRequest to FUSION_PARAM
      - device notifies FusedSample bytes on FUSION_RET
    """
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

            await _fusion_stream_loop(
                send_bytes=send,
                broadcaster=fusion_broadcaster,
                req_bytes=data,
            )

        stream = RpcStream(producer)
        stream.start()

    param_char = WriteChar("/app/fusion/param", FUSION_PARAM, svc, on_param)
    svc.chars += [param_char, ret_char]
    return svc



########################### dummy fusion service ###########################



async def _fusion_minimal_stream_loop(
    *, send_bytes, broadcaster: Broadcaster, req_bytes: bytes
) -> None:
    """
    Stream FusedMinimalSample objects.
    Throttling is driven by sample.ts_us (HAL timestamps), NOT loop.time().
    """
    req = fusion_minimal_pb2.FusionMinimalSubscribeRequest()
    try:
        req.ParseFromString(req_bytes)
    except Exception:
        req.max_rate_hz = MAX_RATE_HZ_FUSION

    max_rate = int(getattr(req, "max_rate_hz", 0))
    if max_rate <= 0:
        interval_us = 0  # no throttling
    else:
        interval_us = int(1_000_000 / float(max_rate))

    last_sent_ts_us: Optional[int] = None

    q: asyncio.Queue = await broadcaster.subscribe()
    tracker = BandwidthTracker("FUSION-MIN-HAL->App", log_interval_s=20.0)

    try:
        while True:
            sample: fusion_minimal_pb2.FusedMinimalSample = await q.get()
            ts_us = int(getattr(sample, "ts_us", 0))

            if interval_us > 0 and ts_us > 0 and last_sent_ts_us is not None:
                dt = ts_us - last_sent_ts_us
                if dt < 0:
                    last_sent_ts_us = ts_us
                elif dt < interval_us:
                    continue

            if ts_us > 0:
                last_sent_ts_us = ts_us

            payload = sample.SerializeToString()
            tracker.add(len(payload))
            await send_bytes(payload)
    finally:
        await broadcaster.unsubscribe(q)


def build_fusion_minimal_service(bus, fusion_broadcaster: Broadcaster) -> GattService:
    svc = GattService("/app/fusion", FUSION_SVC, primary=True)
    ret_char = NotifyChar("/app/fusion/ret", FUSION_RET, svc)

    stream: Optional[RpcStream] = None

    async def on_param(data: bytes) -> None:
        nonlocal stream
        if stream:
            stream.stop()

        async def producer(_send) -> None:
            async def send(b: bytes) -> None:
                await ret_char.notify(b)

            await _fusion_minimal_stream_loop(send_bytes=send, broadcaster=fusion_broadcaster, req_bytes=data)

        stream = RpcStream(producer)
        stream.start()

    param_char = WriteChar("/app/fusion/param", FUSION_PARAM, svc, on_param)
    svc.chars += [param_char, ret_char]
    return svc
