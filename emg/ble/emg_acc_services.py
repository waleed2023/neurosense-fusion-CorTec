import asyncio
from typing import Optional, Callable, Awaitable
from emg.broadcaster import Broadcaster
from emg.proto import emg_pb2
from .gatt_core import GattService, NotifyChar, WriteChar, RpcStream


# UUIDs for EMG BLE service and characteristics
EMG_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1000"
EMG_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1001"  # Write (SubscribeRequest)
EMG_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1002"  # Notify (EmgSample stream)

# UUIDs for ACC BLE service and characteristics
ACC_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2000"
ACC_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2001"  # Write (AccelSubscribeRequest)
ACC_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2002"  # Notify (AccelSample stream)


async def _emg_stream_loop(
    send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    """
    Internal EMG streaming loop.

    - Decodes SubscribeRequest from 'req_bytes'.
    - Subscribes to the EMG Broadcaster.
    - Optionally throttles the outgoing sample rate (max_rate_hz).
    - Serializes EmgSample messages as Protobuf and sends them via 'send_bytes'.
    """
    req = emg_pb2.SubscribeRequest()
    if req_bytes:
        try:
            req.ParseFromString(req_bytes)
        except Exception as exc:
            # Invalid payload: log and continue with defaults
            print(f"[emg] Warning: failed to parse EMG SubscribeRequest: {exc!r}")

    max_rate_hz = req.max_rate_hz or 0
    interval = 1.0 / max_rate_hz if max_rate_hz > 0 else 0.0

    queue = await broadcaster.subscribe()
    try:
        loop = asyncio.get_running_loop()
        last_ts = 0.0

        while True:
            sample: emg_pb2.EmgSample = await queue.get()

            # Optional rate limiting similar to the gRPC server implementation
            if interval > 0.0:
                now = loop.time()
                if (now - last_ts) < interval:
                    continue
                last_ts = now

            await send_bytes(sample.SerializeToString())

    except asyncio.CancelledError:
        # Stream canceled by RpcStream.stop()
        raise
    finally:
        await broadcaster.unsubscribe(queue)


async def _accel_stream_loop(
    send_bytes: Callable[[bytes], Awaitable[None]],
    broadcaster: Broadcaster,
    req_bytes: bytes,
) -> None:
    """
    Internal accelerometer streaming loop.

    - Decodes AccelSubscribeRequest from 'req_bytes'.
    - Subscribes to the accelerometer Broadcaster.
    - Optionally throttles the outgoing sample rate (max_rate_hz).
    - Serializes AccelSample messages as Protobuf and sends them via 'send_bytes'.
    """
    req = emg_pb2.AccelSubscribeRequest()
    if req_bytes:
        try:
            req.ParseFromString(req_bytes)
        except Exception as exc:
            print(f"[emg] Warning: failed to parse ACC SubscribeRequest: {exc!r}")

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

            await send_bytes(sample.SerializeToString())

    except asyncio.CancelledError:
        raise
    finally:
        await broadcaster.unsubscribe(queue)


def build_emg_service(bus, emg_broadcaster: Broadcaster) -> GattService:
    """
    Build the EMG BLE service in a ProtoBLE-style mapping:

      - Service UUID:  EMG_SVC
      - Param UUID:    EMG_PARAM (Write, takes SubscribeRequest)
      - Return UUID:   EMG_RET   (Notify, streams EmgSample)

    The client is expected to:
      1. Write a SubscribeRequest to EMG_PARAM.
      2. Subscribe to notifications on EMG_RET.
      3. Decode each notification as EmgSample.
    """
    svc = GattService("/app/emg", EMG_SVC)

    # Notify characteristic for outgoing EMG samples
    ret_char = NotifyChar("/app/emg/ret", EMG_RET, svc)

    # RpcStream to manage the EMG streaming loop
    stream: Optional[RpcStream] = None

    def on_param(data: bytes) -> None:
        """
        Called whenever the client writes to EMG_PARAM.

        'data' is expected to be a serialized SubscribeRequest.
        We start (or restart) the EMG stream with these parameters.
        """
        nonlocal stream

        # Stop any previously running stream for this client.
        if stream is not None:
            stream.stop()

        async def producer() -> None:
            await _emg_stream_loop(
                send_bytes=send,
                broadcaster=emg_broadcaster,
                req_bytes=data,
            )

        stream = RpcStream(producer)
        stream.start()

    # Write characteristic for SubscribeRequest
    param_char = WriteChar("/app/emg/param", EMG_PARAM, svc, on_param)

    svc.chars += [param_char, ret_char]
    return svc


def build_acc_service(bus, acc_broadcaster: Broadcaster) -> GattService:
    """
    Build the accelerometer BLE service in a ProtoBLE-style mapping:

      - Service UUID:  ACC_SVC
      - Param UUID:    ACC_PARAM (Write, takes AccelSubscribeRequest)
      - Return UUID:   ACC_RET   (Notify, streams AccelSample)

    The client is expected to:
      1. Write an AccelSubscribeRequest to ACC_PARAM.
      2. Subscribe to notifications on ACC_RET.
      3. Decode each notification as AccelSample.
    """
    svc = GattService("/app/acc", ACC_SVC)

    # Notify characteristic for outgoing accelerometer samples
    ret_char = NotifyChar("/app/acc/ret", ACC_RET, svc)

    # RpcStream to manage the accelerometer streaming loop
    stream: Optional[RpcStream] = None

    def on_param(data: bytes) -> None:
        """
        Called whenever the client writes to ACC_PARAM.

        'data' is expected to be a serialized AccelSubscribeRequest.
        We start (or restart) the accelerometer stream with these parameters.
        """
        nonlocal stream

        if stream is not None:
            stream.stop()

        async def producer() -> None:
            await _accel_stream_loop(
                send_bytes=send,
                broadcaster=acc_broadcaster,
                req_bytes=data,
            )

        stream = RpcStream(producer)
        stream.start()

    # Write characteristic for AccelSubscribeRequest
    param_char = WriteChar("/app/acc/param", ACC_PARAM, svc, on_param)

    svc.chars += [param_char, ret_char]
    return svc
