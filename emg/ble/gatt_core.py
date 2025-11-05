#!/usr/bin/env python3
import asyncio
from typing import Callable, Awaitable, List, Optional
from dbus_next.aio import MessageBus
from dbus_next import Variant, BusType
from dbus_next.service import ServiceInterface, method, dbus_property
from dbus_next.constants import PropertyAccess

BLUEZ                   = "org.bluez"
ADAPTER_IFACE           = "org.bluez.Adapter1"
GATT_MANAGER_IFACE      = "org.bluez.GattManager1"
LE_ADV_MANAGER_IFACE    = "org.bluez.LEAdvertisingManager1"
GATT_SERVICE_IFACE      = "org.bluez.GattService1"
GATT_CHAR_IFACE         = "org.bluez.GattCharacteristic1"
PROPERTIES_IFACE        = "org.freedesktop.DBus.Properties"
ADVERTISING_IFACE       = "org.bluez.LEAdvertisement1"


class GattService(ServiceInterface):
    def __init__(self, path: str, uuid: str):
        super().__init__(GATT_SERVICE_IFACE)
        self.path = path
        self.uuid = uuid
        self.chars: List[ServiceInterface] = []

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def Characteristics(self) -> "ao":
        return [c.path for c in self.chars]


class NotifyChar(ServiceInterface):
    """
    Generic Notify-Characteristic (read + notify)
    """
    def __init__(self, bus, path, uuid: str, service: GattService, max_len: int = 180):
        super().__init__(GATT_CHAR_IFACE)
        self.bus = bus
        self.path = path
        self.uuid = uuid
        self.service = service
        self.flags = ["read", "notify"]
        self._value = bytearray()
        self.notifying = False
        self.subscribers = 0
        self.max_len = max_len

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return self.service.path

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return self.flags

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":
        return bytes(self._value)

    @method()
    def StartNotify(self):
        self.notifying = True
        self.subscribers += 1

    @method()
    def StopNotify(self):
        self.subscribers = max(0, self.subscribers - 1)
        self.notifying = self.subscribers > 0

    async def notify(self, data: bytes):
        if not self.notifying or self.subscribers == 0:
            return
        if len(data) > self.max_len:
            return
        self._value = bytearray(data)
        props = {"Value": Variant("ay", list(self._value))}
        await self.bus.call(
            message=self.bus._message(
                destination=None,
                path=self.path,
                interface=PROPERTIES_IFACE,
                member="PropertiesChanged",
                signature="sa{sv}as",
                body=[GATT_CHAR_IFACE, props, []],
            )
        )


class WriteChar(ServiceInterface):
    """
    Generic Write-Without-Response Characteristic.
    Calls a callback with the written bytes.
    """
    def __init__(
        self,
        bus,
        path,
        uuid: str,
        service: GattService,
        on_write: Callable[[bytes], None],
    ):
        super().__init__(GATT_CHAR_IFACE)
        self.bus = bus
        self.path = path
        self.uuid = uuid
        self.service = service
        self.flags = ["write-without-response"]
        self._on_write = on_write

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return self.service.path

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return self.flags

    @method()
    def WriteValue(self, value: "ay", options: "a{sv}"):
        data = bytes(value)
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(self._on_write, data)


class RpcStream:
    """
    Encapsulates a running async producer task (e.g., an EMG/ACC loop).
    """
    def __init__(self, coro_factory: Callable[[], Awaitable[None]]):
        self._coro_factory = coro_factory
        self._task: Optional[asyncio.Task] = None

    def start(self):
        self.stop()
        self._task = asyncio.create_task(self._coro_factory())

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()


class BleGattApp:
    """
    Manages the D-Bus connection, adapter, services, and advertising.
    Domain-specific services are supplied externally.
    """
    def __init__(self, local_name: str, services: List[GattService]):
        self.local_name = local_name
        self.services = services
        self.bus: Optional[MessageBus] = None

    async def run(self):
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # Adapter finden
        root = await self.bus.introspect(BLUEZ, "/")
        adapter_path = None
        for n in root.nodes:
            p = f"/org/bluez/{n}"
            try:
                node = await self.bus.introspect(BLUEZ, p)
            except Exception:
                continue
            for iface in node.interfaces:
                if iface.name == ADAPTER_IFACE:
                    adapter_path = p
                    break

        if not adapter_path:
            adapter_path = "/org/bluez/hci0"
            print(f"[emg] Using fallback Bluetooth adapter path: {adapter_path}")

        # Services export
        for svc in self.services:
            self.bus.export(svc.path, svc)
            for ch in svc.chars:
                self.bus.export(ch.path, ch)

        # GATT application registration
        obj = await self.bus.get_proxy_object(
            BLUEZ,
            adapter_path,
            await self.bus.introspect(BLUEZ, adapter_path),
        )
        gatt_mgr = obj.get_interface(GATT_MANAGER_IFACE)
        await gatt_mgr.call_register_application("/app", {})

        # Advertising (hey Bluetooth, im hier :=D )
        class Adv(ServiceInterface):
            def __init__(self, path, name, uuids):
                super().__init__(ADVERTISING_IFACE)
                self.path = path
                self.name = name
                self.uuids = uuids

            @dbus_property(access=PropertyAccess.READ)
            def Type(self) -> "s":
                return "peripheral"

            @dbus_property(access=PropertyAccess.READ)
            def ServiceUUIDs(self) -> "as":
                return self.uuids

            @dbus_property(access=PropertyAccess.READ)
            def LocalName(self) -> "s":
                return self.name

        uuids = [svc.uuid for svc in self.services]
        adv = Adv("/adv/protoble", self.local_name, uuids)
        self.bus.export(adv.path, adv)

        adv_mgr = obj.get_interface(LE_ADV_MANAGER_IFACE)
        await adv_mgr.call_register_advertisement(adv.path, {})

        return  # the caller will wait using its own event/signal handler
