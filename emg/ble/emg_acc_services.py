import asyncio
from typing import Optional, Callable, Awaitable
from emg.broadcaster import Broadcaster
from emg.proto import emg_pb2  # generated from emg.proto
from .gatt_core import GattService, NotifyChar, WriteChar, RpcStream

# UUIDs
EMG_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1000"
EMG_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1001"  # Write (SubscribeRequest)
EMG_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1002"  # Notify (EmgSample)

ACC_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2000"
ACC_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2001"  # Write (AccelSubscribeRequest)
ACC_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2002"  # Notify (AccelSample)

# ---- Helpers ---------------------------------------------------------------

def _rate_to_interval_ms(req_hz: int, default_ms: int) -> int:
    if req_hz and req_hz > 0:
        ms = int(1000 / req_hz)
        return max(ms, 1)
    return default_ms

async def _emg_stream_loop(
    *,
    send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    """Bridged stream EMG: Broadcaster -> Protobuf -> Notify(send_bytes)."""
    req = emg_pb2.SubscribeRequest()
    try:
        req.ParseFromString(req_bytes)
    except Exception:
        # Fallback auf Default-Rate, wenn Request fehlerhaft
        req.max_rate_hz = 200

    interval = _rate_to_interval_ms(req.max_rate_hz, default_ms=5)  # 200 Hz default
    last_ts = 0

    q: asyncio.Queue = await broadcaster.subscribe()
    try:
        while True:
            sample = await q.get()  # erwartet emg_pb2.EmgSample
            now = sample.ts_ms
            if interval > 1:
                if (now - last_ts) < interval:
                    continue
                last_ts = now
            await send_bytes(sample.SerializeToString())
    except asyncio.CancelledError:
        raise
    finally:
        await broadcaster.unsubscribe(q)

async def _accel_stream_loop(
    *,
    send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    """Bridged stream ACC: Broadcaster -> Protobuf -> Notify(send_bytes)."""
    req = emg_pb2.AccelSubscribeRequest()
    try:
        req.ParseFromString(req_bytes)
    except Exception:
        req.max_rate_hz = 100

    interval = _rate_to_interval_ms(req.max_rate_hz, default_ms=10)  # 100 Hz default
    last_ts = 0

    q: asyncio.Queue = await broadcaster.subscribe()
    try:
        while True:
            sample = await q.get()  # erwartet emg_pb2.AccelSample
            now = sample.ts_ms
            if interval > 1:
                if (now - last_ts) < interval:
                    continue
                last_ts = now
            await send_bytes(sample.SerializeToString())
    except asyncio.CancelledError:
        raise
    finally:
        await broadcaster.unsubscribe(q)

# ---- EMG Service -----------------------------------------------------------

def build_emg_service(bus, emg_broadcaster: Broadcaster) -> GattService:
    svc = GattService("/app/emg", EMG_SVC, primary=True)

    # Letzte SubscribeRequest-Bytes pro Verbindung (überschrieben bei neuem Write)
    last_req: dict[str, bytes] = {}
    # Laufender RpcStream pro Verbindung (StartNotify -> start, StopNotify -> stop)
    streams: dict[str, RpcStream] = {}

    # Wird von NotifyChar bei StartNotify/StopNotify aufgerufen.
    async def on_start_notify(client_id: str, send: Callable[[bytes], Awaitable[None]]):
        # Wähle den zuletzt geschriebenen Request dieses Clients; wenn keiner: default.
        req_bytes = last_req.get(client_id, emg_pb2.SubscribeRequest(max_rate_hz=200).SerializeToString())

        async def producer():
            await _emg_stream_loop(send_bytes=send, broadcaster=emg_broadcaster, req_bytes=req_bytes)

        # Vorherige Session des Clients stoppen
        if client_id in streams:
            streams[client_id].stop()
        stream = RpcStream(producer)  # RpcStream kapselt Task-Lifecycle
        streams[client_id] = stream
        stream.start()

    async def on_stop_notify(client_id: str):
        st = streams.pop(client_id, None)
        if st:
            st.stop()

    ret_char = NotifyChar(
        path="/app/emg/ret",
        uuid=EMG_RET,
        service=svc,
        on_start_notify=on_start_notify,
        on_stop_notify=on_stop_notify,
    )

    # Param-Write: speichere Request pro Client und (optional) restarte Stream
    async def on_param(client_id: str, data: bytes, restart: bool):
        last_req[client_id] = data or emg_pb2.SubscribeRequest(max_rate_hz=200).SerializeToString()
        if restart and client_id in streams:
            # Stream neu aufsetzen mit neuer Rate
            st = streams.pop(client_id)
            st.stop()
            async def producer():
                await _emg_stream_loop(send_bytes=ret_char.sender(client_id),  # send() dieses Clients
                                       broadcaster=emg_broadcaster,
                                       req_bytes=last_req[client_id])
            ns = RpcStream(producer)
            streams[client_id] = ns
            ns.start()

    param_char = WriteChar(
        path="/app/emg/param",
        uuid=EMG_PARAM,
        service=svc,
        on_write=on_param,  # Signatur siehe gatt_core: (client_id, bytes, restart_hint)
    )

    svc.chars += [param_char, ret_char]
    return svc

# ---- ACC Service -----------------------------------------------------------

def build_acc_service(bus, acc_broadcaster: Broadcaster) -> GattService:
    svc = GattService("/app/acc", ACC_SVC, primary=True)

    last_req: dict[str, bytes] = {}
    streams: dict[str, RpcStream] = {}

    async def on_start_notify(client_id: str, send: Callable[[bytes], Awaitable[None]]):
        req_bytes = last_req.get(client_id, emg_pb2.AccelSubscribeRequest(max_rate_hz=100).SerializeToString())

        async def producer():
            await _accel_stream_loop(send_bytes=send, broadcaster=acc_broadcaster, req_bytes=req_bytes)

        if client_id in streams:
            streams[client_id].stop()
        stream = RpcStream(producer)
        streams[client_id] = stream
        stream.start()

    async def on_stop_notify(client_id: str):
        st = streams.pop(client_id, None)
        if st:
            st.stop()

    ret_char = NotifyChar(
        path="/app/acc/ret",
        uuid=ACC_RET,
        service=svc,
        on_start_notify=on_start_notify,
        on_stop_notify=on_stop_notify,
    )

    async def on_param(client_id: str, data: bytes, restart: bool):
        last_req[client_id] = data or emg_pb2.AccelSubscribeRequest(max_rate_hz=100).SerializeToString()
        if restart and client_id in streams:
            st = streams.pop(client_id)
            st.stop()
            async def producer():
                await _accel_stream_loop(send_bytes=ret_char.sender(client_id),
                                         broadcaster=acc_broadcaster,
                                         req_bytes=last_req[client_id])
            ns = RpcStream(producer)
            streams[client_id] = ns
            ns.start()

    param_char = WriteChar(
        path="/app/acc/param",
        uuid=ACC_PARAM,
        service=svc,
        on_write=on_param,
    )

    svc.chars += [param_char, ret_char]
    return svc
