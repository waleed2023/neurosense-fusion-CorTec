#!/usr/bin/env python3
import asyncio
import signal
from emg.broadcaster import Broadcaster
from emg.ble_manager import BLEManager
from emg.accel_manager import AccelManager
from emg.config import LOG_PREFIX
from .gatt_core import BleGattApp
from .emg_acc_services import build_emg_service, build_acc_service
from dbus_next.aio import MessageBus
from dbus_next import BusType



async def main_async():
    # 1) Broadcaster for EMG and Accel
    emg_b = Broadcaster()
    acc_b = Broadcaster()

    # 2) Sensor-Manager (start reading sensors and publishing to Broadcaster)
    ble_mgr = BLEManager(emg_b)
    acc_mgr = AccelManager(acc_b)

    ble_task = asyncio.create_task(ble_mgr.run())
    acc_task = asyncio.create_task(acc_mgr.run())

    # 3) D-Bus GATT App (services need the bus)
    # The GATT app requires fully-built service objects which in turn need a connected
    # DBus MessageBus. To keep things simple we connect the bus below (after this block)
    # and then call build_emg_service(bus, emg_b) and build_acc_service(bus, acc_b).
    # Alternatively, BleGattApp could be adapted to accept a connected bus or to build
    # services inside its run() method. For now we build services after obtaining the bus
    # (see the code that follows).


    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    emg_svc = build_emg_service(bus, emg_b)
    acc_svc = build_acc_service(bus, acc_b)

    app = BleGattApp(local_name="NS-ProtoBLE", services=[emg_svc, acc_svc])
    await app.run()  # registers services & starts advertising

    print(f"{LOG_PREFIX} BLE ProtoBLE-Server running as 'NS-ProtoBLE'")

    # 4) wait for stop signal
    stop = asyncio.Event()

    def _sig(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, _sig)
    loop.add_signal_handler(signal.SIGTERM, _sig)

    await stop.wait()

    print(f"{LOG_PREFIX} Stop-Signal, cleaning up...")
    await ble_mgr.stop()
    await acc_mgr.stop()
    ble_task.cancel()
    acc_task.cancel()
    try:
        await asyncio.gather(ble_task, acc_task)
    except asyncio.CancelledError:
        pass

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
