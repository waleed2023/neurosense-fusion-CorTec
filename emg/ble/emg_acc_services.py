import asyncio
from typing import Optional, Callable, Awaitable
from emg.broadcaster import Broadcaster
from emg.proto import emg_pb2
from .gatt_core import GattService, NotifyChar, WriteChar, RpcStream

# UUIDs
EMG_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1000"
EMG_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1001"  # Write (SubscribeRequest)
EMG_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1002"  # Notify (EmgSample)

ACC_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2000"
ACC_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2001"  # Write (AccelSubscribeRequest)
ACC_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2002"  # Notify (AccelSample)

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
        req.max_rate_hz = 200
    # einfache Zeitdrossel anhand Zielrate
    interval_s = 1.0 / max(1, req.max_rate_hz)
    last = 0.0
    loop = asyncio.get_running_loop()

    q: asyncio.Queue = await broadcaster.subscribe()
    try:
        while True:
            sample: emg_pb2.EmgSample = await q.get()
            now = loop.time()
            if now - last >= interval_s:
                last = now
                await send_bytes(sample.SerializeToString())
    except asyncio.CancelledError:
        raise
    finally:
        await broadcaster.unsubscribe(q)

async def _acc_stream_loop(
    *, send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    req = emg_pb2.AccelSubscribeRequest()
    try:
        req.ParseFromString(req_bytes)
    except Exception:
        req.max_rate_hz = 100
    interval_s = 1.0 / max(1, req.max_rate_hz)
    last = 0.0
    loop = asyncio.get_running_loop()

    q: asyncio.Queue = await broadcaster.subscribe()
    try:
        while True:
            sample: emg_pb2.AccelSample = await q.get()
            now = loop.time()
            if now - last >= interval_s:
                last = now
                await send_bytes(sample.SerializeToString())
    except asyncio.CancelledError:
        raise
    finally:
        await broadcaster.unsubscribe(q)

# ------------------------------- Services ------------------------------------

def build_emg_service(bus, emg_broadcaster: Broadcaster) -> GattService:
    svc = GattService("/app/emg", EMG_SVC)
    ret_char = NotifyChar("/app/emg/ret", EMG_RET, svc)   # KEIN on_stop_notify!
    stream: Optional[RpcStream] = None                    # ein globaler Stream

    async def on_param(data: bytes) -> None:
        """Client schreibt SubscribeRequest -> Stream (re)starten."""
        nonlocal stream
        if stream:
            stream.stop()
        async def producer():
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
        async def producer():
            async def send(b: bytes):
                await ret_char.notify(b)
            await _acc_stream_loop(send_bytes=send, broadcaster=acc_broadcaster, req_bytes=data)
        stream = RpcStream(producer)
        stream.start()

    param_char = WriteChar("/app/acc/param", ACC_PARAM, svc, on_param)
    svc.chars += [param_char, ret_char]
    return svc
