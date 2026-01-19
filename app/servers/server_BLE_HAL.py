#!/usr/bin/env python3
"""BLE HAL service (Bleak central) + gRPC streaming.

Runs on the Raspberry Pi as an independent systemd service.

Responsibilities:
  - Connect to EMG device via BLEManager (HAL).
  - Publish EmgSample to a local Broadcaster.
  - Expose gRPC endpoint emg.Emg/StreamEmg to stream EmgSample to clients
    (e.g., the fusion server).
"""

import asyncio
import os
import signal
import sys
import grpc

from app.manager.broadcaster import Broadcaster
from app.manager.ble_manager import BLEManager
from app.manager.config import LOG_PREFIX, GRPC_HOST, MAX_RATE_HZ_EMG
from app.proto import sensor_pb2, sensor_pb2_grpc



# gRPC Servicer for EMG streaming
class EmgGrpcServicer(sensor_pb2_grpc.EmgServicer):
    """Streams EmgSample objects from a Broadcaster."""

    def __init__(self, broadcaster: Broadcaster):
        self._buffer = broadcaster

    async def StreamEmg(self, request: sensor_pb2.SubscribeRequest, context: grpc.aio.ServicerContext):
        # context: grpc.aio.ServicerContext is used as signature
        q = await self._buffer.subscribe()
        try:
            # max_rate_hz from client request in case the client(my case flutter/dart app) wants to limit rate, otherwise use MAX_RATE_HZ_EMG
            max_rate = int(getattr(request, "max_rate_hz", 0))
            if max_rate <= 0:
                max_rate = MAX_RATE_HZ_EMG # with ARN Corp M40 factory data rate is 10hz
            interval = 0.0 if max_rate <= 0 else (1.0 / float(max_rate)) # throttle as "max samples per second"
            # example: if max_rate = 100Hz, interval = 0.01s
            # may_rate = 0 -> send all samples as they come
            # throttling hier is drop samples if they come too fast
            loop = asyncio.get_running_loop()
            last = 0.0

            while True:
                sample: sensor_pb2.EmgSample = await q.get()

                if interval > 0.0:
                    now = loop.time()
                    if (now - last) < interval: # too eyarly than interval (ex. 0.01s for 100Hz)
                        continue # drop sample
                    last = now

                yield sample

        except asyncio.CancelledError:
            raise
        finally:
            await self._buffer.unsubscribe(q)


# Set up and start the gRPC server
async def _serve_grpc(broadcaster: Broadcaster, *, host: str, port: int) -> grpc.aio.Server:
    # max_send_message_length and max_receive_message_length increased to 4MB
    # max_receive_message_length is important for high-rate streaming, also 4MB
    server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", 4 * 1024 * 1024),
            ("grpc.max_receive_message_length", 4 * 1024 * 1024),
        ]
    )
    sensor_pb2_grpc.add_EmgServicer_to_server(EmgGrpcServicer(broadcaster), server)
    bind = f"{host}:{port}"
    server.add_insecure_port(bind)
    await server.start()
    sys.stdout.write(f"{LOG_PREFIX} EMG gRPC listening on {bind}\n")
    return server


async def main_async():
    emg_buffer = Broadcaster()
    ble_mgr = BLEManager(emg_buffer)

    grpc_port = int(os.getenv("GRPC_PORT_EMG", 50051))
    grpc_host = os.getenv("GRPC_HOST", GRPC_HOST)

    grpc_server = await _serve_grpc(emg_buffer, host=grpc_host, port=grpc_port)

    ble_task = asyncio.create_task(ble_mgr.run(), name="ble-manager")

    stop = asyncio.Event()

    def _sig(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _sig)
        except NotImplementedError:
            signal.signal(s, lambda *_: stop.set())

    await stop.wait()

    sys.stdout.write(f"{LOG_PREFIX} Stop signal, shutting down BLE HAL...\n")
    await ble_mgr.stop()
    ble_task.cancel()
    try:
        await ble_task
    except asyncio.CancelledError:
        pass

    await grpc_server.stop(grace=2.0)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
