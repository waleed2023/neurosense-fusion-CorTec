#!/usr/bin/env python3
import sys
import asyncio
import signal
import grpc

from emg.config import GRPC_HOST, GRPC_PORT, LOG_PREFIX
from emg.broadcaster import Broadcaster
from emg.ble_manager import BLEManager
from emg.service import EmgService
from emg.accel_manager import AccelManager
from emg.accel_service import AccelService
from emg.proto import emg_pb2_grpc

async def serve():
    emg_b = Broadcaster()
    acc_b = Broadcaster()

    ble = BLEManager(emg_b)
    acc = AccelManager(acc_b)

    server = grpc.aio.server(options=[
        ('grpc.max_send_message_length', 4 * 1024 * 1024),
        ('grpc.max_receive_message_length', 4 * 1024 * 1024),
    ])
    emg_pb2_grpc.add_EmgServicer_to_server(EmgService(emg_b), server)
    emg_pb2_grpc.add_AccelServicer_to_server(AccelService(acc_b), server)

    bind_addr = f"{GRPC_HOST}:{GRPC_PORT}"
    server.add_insecure_port(bind_addr)  # TODO: TLS in Produktion
    await server.start()
    sys.stderr.write(f"{LOG_PREFIX} gRPC Server läuft auf {bind_addr}\n")

    ble_task  = asyncio.create_task(ble.run())
    acc_task  = asyncio.create_task(acc.run())

    stop_event = asyncio.Event()
    def _handle_signal():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    await stop_event.wait()
    sys.stderr.write(f"{LOG_PREFIX} Shutdown...\n")

    await ble.stop()
    await acc.stop()
    for t in (ble_task, acc_task):
        t.cancel()
        try:
            await t
        except Exception:
            pass

    await server.stop(grace=None)
    sys.stderr.write(f"{LOG_PREFIX} gRPC Server gestoppt.\n")

def main():
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
