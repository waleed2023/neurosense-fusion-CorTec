#!/usr/bin/env python3
"""ACC HAL service (I2C) + gRPC streaming.

Runs on the Raspberry Pi as an independent systemd service.

Responsibilities:
  - Poll accelerometer via AccelManager (HAL).
  - Publish samples to a local Broadcaster.
  - Expose gRPC endpoint emg.Accel/StreamAccel to stream AccelSample to clients
    (e.g., the fusion server).
"""

import asyncio
import os
import signal
import sys
import grpc

from emg.broadcaster import Broadcaster
from emg.accel_manager import AccelManager
from emg.config import LOG_PREFIX, GRPC_HOST, MAX_RATE_HZ_ACC
from emg.proto import emg_pb2, emg_pb2_grpc




# Helper to read integer env vars with defaults
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default

# gRPC Servicer for Accel streaming
class AccelGrpcServicer(emg_pb2_grpc.AccelServicer):
    """Streams AccelSample objects from a Broadcaster."""

    def __init__(self, broadcaster: Broadcaster):
        self._b = broadcaster
    # grpc method
    async def StreamAccel(
        self,
        request: emg_pb2.AccelSubscribeRequest,
        context: grpc.aio.ServicerContext,
    ):
        q = await self._b.subscribe()
        try:
            # Throttle as "max samples per second" (drop if too fast)
            # max_rate_hz from client request in case the client(my case flutter/dart app) wants to limit rate, otherwise use MAX_RATE_HZ_ACC
            max_rate = int(getattr(request, "max_rate_hz", 0) or 0)
            if max_rate <= 0:
                max_rate = MAX_RATE_HZ_ACC
            interval = 0.0 if max_rate <= 0 else (1.0 / float(max_rate))
            loop = asyncio.get_running_loop()
            last = 0.0

            while True:
                sample: emg_pb2.AccelSample = await q.get() # get sample from broadcaster

                if interval > 0.0:
                    now = loop.time()
                    if (now - last) < interval:
                        continue  # drop to enforce max_rate_hz, in my case i set the sensor to 1660HZ
                    last = now

                yield sample

        except asyncio.CancelledError:
            raise
        finally:
            await self._b.unsubscribe(q)

# Set up and start the gRPC server
async def _serve_grpc(broadcaster: Broadcaster, *, host: str, port: int) -> grpc.aio.Server:
    # max_send_message_length and max_receive_message_length increased to 4MB
    # max_receive_message_length increased to 4MB
    # good for high-frequency data streaming
    # in my case it ok to have it
    # data will send by yielding samples one by one
    server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", 4 * 1024 * 1024),
            ("grpc.max_receive_message_length", 4 * 1024 * 1024),
        ]
    )
    emg_pb2_grpc.add_AccelServicer_to_server(AccelGrpcServicer(broadcaster), server)
    bind = f"{host}:{port}"
    server.add_insecure_port(bind)
    await server.start()
    sys.stdout.write(f"{LOG_PREFIX} ACC gRPC listening on {bind}\n")
    return server


async def main_async():
    acc_b = Broadcaster()
    acc_mgr = AccelManager(acc_b)

    # Ports: keep separated from EMG/Fusion
    grpc_port = _env_int("GRPC_PORT_ACC", 50052)
    grpc_host = os.getenv("GRPC_HOST", GRPC_HOST)

    grpc_server = await _serve_grpc(acc_b, host=grpc_host, port=grpc_port)

    acc_task = asyncio.create_task(acc_mgr.run(), name="accel-manager")

    stop = asyncio.Event()

    def _sig(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    # Compatibility for Windows
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _sig)
        except NotImplementedError:
            signal.signal(s, lambda *_: stop.set())

    await stop.wait()

    sys.stdout.write(f"{LOG_PREFIX} Stop signal, shutting down ACC HAL...\n")
    await acc_mgr.stop()
    acc_task.cancel()
    try:
        await acc_task
    except asyncio.CancelledError:
        pass

    await grpc_server.stop(grace=2.0) # Graceful shutdown


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()