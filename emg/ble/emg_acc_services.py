import asyncio
from typing import Callable, Awaitable, Optional
from emg.broadcaster import Broadcaster
from emg.proto import emg_pb2
from .gatt_core import GattService, NotifyChar, WriteChar, RpcStream

# UUIDs for EMG service (BLE)
EMG_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1000"
EMG_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1001"
EMG_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1002"

# UUIDs for ACC service (BLE)
ACC_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2000"
ACC_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2001"
ACC_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2002"


async def _emg_loop(
    send: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    """
    Reads EMG samples from the broadcaster and sends them as protobuf bytes
    via the send() function (→ NotifyChar).
    The behavior is modeled after EmgService.StreamEmg.
    """
    # Parse request (SubscribeRequest) if present
    req = emg_pb2.SubscribeRequest()
    if req_bytes:
        try:
            req.ParseFromString(req_bytes)
        except Exception:
            # Invalid payload → continue with default request
            pass


    max_rate_hz = req.max_rate_hz or 0
    interval = 1.0 / max_rate_hz if max_rate_hz > 0 else 0.0

    queue = await broadcaster.subscribe()
    try:
        loop = asyncio.get_running_loop()
        last_ts = 0.0

        while True:
            sample: emg_pb2.EmgSample = await queue.get()

            # optional throttling like in gRPC server
            if interval > 0.0:
                now = loop.time()
                if (now - last_ts) < interval:
                    continue
                last_ts = now

            await send(sample.SerializeToString())

    except asyncio.CancelledError:
        # Task was cancelled (e.g. new subscription) → propagate upwards
        raise
    finally:
        await broadcaster.unsubscribe(queue)


async def _accel_loop(
    send: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    """
    Reads Accel samples from the broadcaster and sends them as protobuf bytes.
    Analogous to AccelService.StreamAccel.
    """
    req = emg_pb2.AccelSubscribeRequest()
    if req_bytes:
        try:
            req.ParseFromString(req_bytes)
        except Exception:
            pass

    max_rate_hz = req.max_rate_hz or 0
    interval = 1.0 / max_rate_hz if max_rate_hz > 0 else 0.0

    queue = await broadcaster.subscribe()
    try:
        loop = asyncio.get_running_loop()
        last_ts = 0.0

        while True:
            sample: emg_pb2.AccelSample = await queue.get()

            if interval > 0.0:
                now = loop.time()
                if (now - last_ts) < interval:
                    continue
                last_ts = now

            await send(sample.SerializeToString())

    except asyncio.CancelledError:
        raise
    finally:
        await broadcaster.unsubscribe(queue)


def build_emg_service(bus, emg_broadcaster: Broadcaster) -> GattService:
    """
    Baut den EMG-BLE-Service im ProtoBLE-Stil:

    - Service-UUID: EMG_SVC
    - Write-Characteristic (Param):  EMG_PARAM, nimmt SubscribeRequest (Protobuf)
    - Notify-Characteristic (Return): EMG_RET, sendet EmgSample (Protobuf-Stream)
    """
    svc = GattService("/app/emg", EMG_SVC)
    ret_char = NotifyChar(bus, "/app/emg/ret", EMG_RET, svc)

    stream: Optional[RpcStream] = None

    def on_param(data: bytes) -> None:
        nonlocal stream

        # Stop previous stream if any
        if stream is not None:
            stream.stop()

        async def producer() -> None:
            await _emg_loop(lambda b: ret_char.notify(b), emg_broadcaster, data)

        stream = RpcStream(producer)
        stream.start()

    param_char = WriteChar(bus, "/app/emg/param", EMG_PARAM, svc, on_param)
    svc.chars += [param_char, ret_char]
    return svc


def build_acc_service(bus, acc_broadcaster: Broadcaster) -> GattService:
    """
    Builds the ACC BLE service in ProtoBLE style:

    - Service UUID: ACC_SVC
    - Write Characteristic (Param):  ACC_PARAM, accepts AccelSubscribeRequest (protobuf)
    - Notify Characteristic (Return): ACC_RET, sends AccelSample (protobuf stream)
    """
    svc = GattService("/app/acc", ACC_SVC)
    ret_char = NotifyChar(bus, "/app/acc/ret", ACC_RET, svc)

    stream: Optional[RpcStream] = None

    def on_param(data: bytes) -> None:
        nonlocal stream

        if stream is not None:
            stream.stop()

        async def producer() -> None:
            await _accel_loop(lambda b: ret_char.notify(b), acc_broadcaster, data)

        stream = RpcStream(producer)
        stream.start()

    param_char = WriteChar(bus, "/app/acc/param", ACC_PARAM, svc, on_param)
    svc.chars += [param_char, ret_char]
    return svc
