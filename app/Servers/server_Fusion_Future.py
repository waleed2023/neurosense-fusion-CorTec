#!/usr/bin/env python3
"""Fusion server: gRPC clients (EMG+ACC) + FusionManager + BLE ProtoBLE (GATT/Adv).

Runs on the Raspberry Pi as a third service.

Responsibilities:
  - Connect to the two HAL services via gRPC streams:
      * server_BLE_HAL  -> app.manager/StreamEmg
      * server_ACC_HAL  -> app.manager/StreamAccel
  - Feed received samples into local Broadcasters.
  - Run FusionManager to compute FusedSample and publish to fusion broadcaster.
  - Expose BLE GATT service (Fusion) + advertisement via BlueZ D-Bus, similar to server_main.py.
"""

import asyncio
import os
import signal
import sys
import grpc

from dbus_next.aio import MessageBus
from dbus_next import BusType

from app.manager.broadcaster import Broadcaster
from app.manager.config import LOG_PREFIX, MAX_RATE_HZ_EMG, MAX_RATE_HZ_ACC, FUSION_RET
from app.fusion.fusion_manager import FusionManager
from app.servers.gatt_core import BleGattApp
from app.servers.fusion_service import build_fusion_service
from app.proto import sensor_pb2, sensor_pb2_grpc



# Pull EMG samples from gRPC HAL service into local broadcaster
async def _grpc_pull_emg(*, stop_evt: asyncio.Event, broadcaster: Broadcaster, host: str, port: int):
    backoff = 1.0
    target = f"{host}:{port}"
    req = sensor_pb2.SubscribeRequest(max_rate_hz=int(os.getenv("FUSION_REQ_RATE_HZ_EMG", MAX_RATE_HZ_EMG))) # MAX_RATE_HZ_EMG for maximal data rate from HAL, same for ACC

    while not stop_evt.is_set():
        channel = None
        try:
            sys.stdout.write(f"{LOG_PREFIX} [fusion] connecting EMG gRPC -> {target}\n")
            channel = grpc.aio.insecure_channel(target)
            stub = sensor_pb2_grpc.EmgStub(channel)

            async for sample in stub.StreamEmg(req):
                await broadcaster.publish(sample)
                if stop_evt.is_set():
                    break

            backoff = 1.0

        except asyncio.CancelledError:
            raise
        except Exception as e:
            sys.stdout.write(f"{LOG_PREFIX} [fusion] EMG gRPC error: {e}\n")
            await asyncio.sleep(backoff) # exponential backoff on reconnect incase of failure
            backoff = min(backoff * 2.0, 10.0)
        finally:
            if channel is not None:
                try:
                    await channel.close()
                except Exception:
                    pass


async def _grpc_pull_acc(*, stop_evt: asyncio.Event, broadcaster: Broadcaster, host: str, port: int):
    backoff = 1.0
    target = f"{host}:{port}"
    req = sensor_pb2.AccelSubscribeRequest(max_rate_hz=int(os.getenv("FUSION_REQ_RATE_HZ_ACC", MAX_RATE_HZ_ACC)))

    while not stop_evt.is_set():
        channel = None
        try:
            sys.stdout.write(f"{LOG_PREFIX} [fusion] connecting ACC gRPC -> {target}\n")
            channel = grpc.aio.insecure_channel(target)
            stub = sensor_pb2_grpc.AccelStub(channel)

            async for sample in stub.StreamAccel(req):
                await broadcaster.publish(sample)
                if stop_evt.is_set():
                    break

            backoff = 1.0

        except asyncio.CancelledError:
            raise
        except Exception as e:
            sys.stdout.write(f"{LOG_PREFIX} [fusion] ACC gRPC error: {e}\n")
            await asyncio.sleep(backoff) # exponential backoff on reconnect
            backoff = min(backoff * 2.0, 10.0)
        finally:
            if channel is not None:
                try:
                    await channel.close()
                except Exception:
                    pass


async def main_async():
    # 1) Local Broadcasters (input + output)
    emg_b = Broadcaster()
    acc_b = Broadcaster()
    fusion_b = Broadcaster()

    # 2) gRPC upstream locations (HAL services)
    emg_host = os.getenv("EMG_GRPC_HOST", "127.0.0.1")
    acc_host = os.getenv("ACC_GRPC_HOST", "127.0.0.1")
    emg_port = int(os.getenv("GRPC_PORT_EMG", 50051))
    acc_port = int(os.getenv("GRPC_PORT_ACC", 50052))

    stop_evt = asyncio.Event()

    # 3) Pull from HAL services (gRPC) into local broadcasters
    emg_pull_task = asyncio.create_task(
        _grpc_pull_emg(stop_evt=stop_evt, broadcaster=emg_b, host=emg_host, port=emg_port),
        name="grpc-pull-emg",
    )
    acc_pull_task = asyncio.create_task(
        _grpc_pull_acc(stop_evt=stop_evt, broadcaster=acc_b, host=acc_host, port=acc_port),
        name="grpc-pull-acc",
    )

    # 4) Start fusion
    fusion_mgr = FusionManager(emg_b, acc_b, fusion_b)
    fusion_mgr.start()

    # 5) Setup BLE GATT + advertisement (BlueZ D-Bus)
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    fusion_svc = build_fusion_service(bus, fusion_b)
    # Create GATT application with Fusion service saved in /app, advertised as NS-ProtoBLE to find on Tablet :)
    app = BleGattApp(
        app_path="/app",
        local_name=os.getenv("BLE_LOCAL_NAME", "NS-ProtoBLE"),
        service_uuids=[fusion_svc.uuid],
    )

    # Stop advertising once a client starts notifications; resume on stop/disconnect
    stop_adv = app.get_stop_adv_hook()
    for ch in getattr(fusion_svc, "chars", []):
        if getattr(ch, "uuid", "") == FUSION_RET and hasattr(ch, "set_on_start_notify"):
            ch.set_on_start_notify(stop_adv)
            if hasattr(ch, "set_on_stop_notify"):
                ch.set_on_stop_notify(lambda: asyncio.create_task(app.resume_advertising()))
            sys.stdout.write(f"{LOG_PREFIX} [fusion] Hooks wired to FUSION_RET (notify)\n")
            break

    await app.run(bus, [fusion_svc]) # send advertisement + register GATT service with BlueZ, implemented in gatt_core.py
    sys.stdout.write(f"{LOG_PREFIX} [fusion] BLE ProtoBLE server running as '{app.local_name}'\n")

    # 6) Stop handling
    def _sig(*_):
        stop_evt.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _sig)
        except NotImplementedError:
            signal.signal(s, lambda *_: stop_evt.set())

    await stop_evt.wait()

    sys.stdout.write(f"{LOG_PREFIX} [fusion] Stop signal, cleaning up...\n")

    # Stop BLE first
    await app.stop()

    # Stop fusion
    await fusion_mgr.stop()

    # Stop gRPC pullers
    for t in (emg_pull_task, acc_pull_task):
        t.cancel()
    try:
        await asyncio.gather(emg_pull_task, acc_pull_task)
    except asyncio.CancelledError:
        pass


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
