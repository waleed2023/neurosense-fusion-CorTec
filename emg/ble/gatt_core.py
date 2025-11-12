#!/usr/bin/env python3
"""
BlueZ GATT core (dbus-next) — compact and production‑ready.

What it does:
  • Exposes a minimal GATT app with ObjectManager that BlueZ can introspect.
  • Provides GattService / NotifyChar / WriteChar primitives used by emg_acc_services.
  • Registers a minimal LE advertisement and keeps it in sync with connection state:
      - Stop advertising when the client calls StartNotify (real data session).
      - Resume advertising automatically on disconnect.
  • No kernel driver resets or adapter bouncing here — only clean D‑Bus ops.

API:
  class GattService(path, uuid, primary=True)
  class NotifyChar(path, uuid, service|service_path, on_start_notify=None)
  class WriteChar(path, uuid, service|service_path, on_write: async (bytes)->None)
  class RpcStream(producer_async)
  class BleGattApp(app_path="/org/bluez/protoble", adapter_path="/org/bluez/hci0",
                   local_name="NS-ProtoBLE", service_uuids=list)
      await run(bus, [services...])
      await stop()
      get_stop_adv_hook() -> callable to wire into NotifyChar.set_on_start_notify

Notes:
  • All @method annotations use D‑Bus signature strings ("" for void) per dbus-next.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from dbus_next.aio import MessageBus
from dbus_next import Variant
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, method, dbus_property

# ---- BlueZ interfaces / constants ----
BLUEZ = "org.bluez"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
OBJM_IFACE = "org.freedesktop.DBus.ObjectManager"
ADAPTER_IFACE = "org.bluez.Adapter1"
GATT_MGR_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHAR_IFACE = "org.bluez.GattCharacteristic1"
LE_ADV_IFACE = "org.bluez.LEAdvertisement1"
LE_ADV_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
DEVICE_IFACE = "org.bluez.Device1"

DEFAULT_APP_PATH = "/org/bluez/protoble"
DEFAULT_ADV_PATH = f"{DEFAULT_APP_PATH}/advertisement0"
DEFAULT_ADAPTER = "/org/bluez/hci0"

# ---------------------------------------------------------------------------
#  Minimal LE Advertisement (LEAdvertisement1)
# ---------------------------------------------------------------------------
class BleAdvertisement(ServiceInterface):
    def __init__(
        self,
        path: str,
        local_name: str,
        service_uuids: Iterable[str],
        appearance: Optional[int] = None,
        includes: Optional[List[str]] = None,
    ) -> None:
        super().__init__(LE_ADV_IFACE)
        self.path = path
        self.local_name = local_name
        self.service_uuids = list(service_uuids)
        self.appearance = appearance
        self.includes = includes or ["tx-power"]

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":
        return self.local_name

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return self.service_uuids

    @dbus_property(access=PropertyAccess.READ)
    def Includes(self) -> "as":
        return self.includes

    @dbus_property(access=PropertyAccess.READ)
    def Appearance(self) -> "q":
        return self.appearance or 0

    @method()
    def Release(self) -> "":
        # BlueZ calls this when it frees the advertisement
        pass

# ---------------------------------------------------------------------------
#  Minimal GATT Service (GattService1)
# ---------------------------------------------------------------------------
class GattService(ServiceInterface):
    def __init__(self, path: str, uuid: str, primary: bool = True) -> None:
        super().__init__(GATT_SERVICE_IFACE)
        self.path = path
        self.uuid = uuid
        self.primary = primary
        self.chars: List[ServiceInterface] = []  # characteristics to export

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return bool(self.primary)

    @dbus_property(access=PropertyAccess.READ)
    def Device(self) -> "o":
        # Not a remote service -> return root path
        return "/"

    @dbus_property(access=PropertyAccess.READ)
    def Includes(self) -> "ao":
        return []

# ---------------------------------------------------------------------------
#  Characteristics
# ---------------------------------------------------------------------------
class NotifyChar(ServiceInterface):
    """Notify + Read characteristic with StartNotify hook to stop advertising."""

    def __init__(
        self,
        path: str,
        uuid: str,
        service: GattService | str,
        on_start_notify: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(GATT_CHAR_IFACE)
        self.path = path
        self.uuid = uuid
        self.service_path = service if isinstance(service, str) else service.path
        self.flags: List[str] = ["read", "notify"]
        self._value: bytes = b""
        self._notifying: bool = False
        self._on_start_notify = on_start_notify

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return self.service_path

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return self.flags

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":
        return self._value

    @method()
    def ReadValue(self, _options: "a{sv}") -> "ay":
        return self._value

    @method()
    def StartNotify(self) -> "":
        self._notifying = True
        if self._on_start_notify:
            try:
                self._on_start_notify()
            except Exception as ex:
                print(f"[emg] StartNotify hook failed: {ex}")

    @method()
    def StopNotify(self) -> "":
        self._notifying = False

    async def notify(self, data: bytes) -> None:
        self._value = data
        if self._notifying:
            self.emit_properties_changed({"Value": self._value}, [])

    def set_on_start_notify(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_start_notify = cb


class WriteChar(ServiceInterface):
    """Write-only characteristic that dispatches to an async callback."""

    def __init__(
        self,
        path: str,
        uuid: str,
        service: GattService | str,
        on_write: Optional[Callable[[bytes], Awaitable[None]]] = None,
    ) -> None:
        super().__init__(GATT_CHAR_IFACE)
        self.path = path
        self.uuid = uuid
        self.service_path = service if isinstance(service, str) else service.path
        self.flags: List[str] = ["write-without-response", "write"]
        self._on_write = on_write

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return self.service_path

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return self.flags

    @method()
    def WriteValue(self, value: "ay", _options: "a{sv}") -> "":
        if self._on_write is None:
            return
        try:
            asyncio.create_task(self._on_write(bytes(value)))
        except Exception as ex:
            print(f"[emg] WriteValue dispatch failed: {ex}")

# ---------------------------------------------------------------------------
#  Tiny helper to run an async producer in background (used by emg_acc_services)
# ---------------------------------------------------------------------------
class RpcStream:
    def __init__(self, producer: Callable[[Callable[[bytes], Awaitable[None]]], Awaitable[None]]):
        self._producer = producer
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        # Many producers ignore the callback argument; provide a no-op anyway
        self._task = loop.create_task(self._producer(lambda _b: asyncio.sleep(0)))

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

# ---------------------------------------------------------------------------
#  ObjectManager for the GATT app (BlueZ calls GetManagedObjects at register)
# ---------------------------------------------------------------------------
class AppObjectManager(ServiceInterface):
    """Exposes the GATT tree to BlueZ via org.freedesktop.DBus.ObjectManager."""

    def __init__(self, app: "BleGattApp", path: str) -> None:
        super().__init__(OBJM_IFACE)
        self.path = path
        self._app = app

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        managed: Dict[str, Dict[str, Dict[str, Variant]]] = {}

        # Services
        for svc in self._app._gatt_services:
            managed[svc.path] = {
                GATT_SERVICE_IFACE: {
                    "UUID": Variant("s", svc.uuid),
                    "Primary": Variant("b", bool(svc.primary)),
                    "Device": Variant("o", "/"),
                    "Includes": Variant("ao", []),
                }
            }

        # Characteristics
        for ch in self._app._gatt_chars:
            managed[ch.path] = {
                GATT_CHAR_IFACE: {
                    "UUID": Variant("s", ch.uuid),
                    "Service": Variant("o", ch.service_path),
                    "Flags": Variant("as", ch.flags),
                }
            }

        return managed

        

# ---------------------------------------------------------------------------
#  GATT App: export services, manage advertising, resume on disconnect
# ---------------------------------------------------------------------------
class BleGattApp:
    def __init__(
        self,
        app_path: str = DEFAULT_APP_PATH,
        adapter_path: Optional[str] = None,
        local_name: str = "NS-ProtoBLE",
        service_uuids: Optional[List[str]] = None,
    ) -> None:
        self.app_path = app_path
        self._adapter_path = adapter_path or DEFAULT_ADAPTER
        self.local_name = local_name
        self.service_uuids = service_uuids or []

        self.bus: Optional[MessageBus] = None
        self._exported_paths: List[str] = []

        self._adv: Optional[BleAdvertisement] = None
        self._registered_adv: bool = False

        # Registries for ObjectManager
        self._gatt_services: List[GattService] = []
        self._gatt_chars: List[ServiceInterface] = []
        self._obj_manager: Optional[AppObjectManager] = None

    async def _stop_discovery_if_needed(self):
        node = await self.bus.introspect("org.bluez", self._adapter_path)
        adapter_obj = self.bus.get_proxy_object("org.bluez", self._adapter_path, node)
        props = adapter_obj.get_interface("org.freedesktop.DBus.Properties")
        val = await props.call_get("org.bluez.Adapter1", "Discovering")
        discovering = bool(getattr(val, "value", val))
        if discovering:
            adapter = adapter_obj.get_interface("org.bluez.Adapter1")
            try:
                await adapter.call_stop_discovery()
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"[emg] stop_discovery failed: {e}")


    async def run(self, bus: MessageBus, services: Iterable[ServiceInterface]) -> None:
        """Export services, register advertisement, install disconnect watcher."""
        self.bus = bus

        # Resolve adapter and managers
        node = await self.bus.introspect(BLUEZ, self._adapter_path)
        adapter_obj = self.bus.get_proxy_object(BLUEZ, self._adapter_path, node)
        gatt_mgr = adapter_obj.get_interface(GATT_MGR_IFACE)
        adv_mgr = adapter_obj.get_interface(LE_ADV_MANAGER_IFACE)

        # Export ObjectManager at app root
        self._obj_manager = AppObjectManager(self, self.app_path)
        self.bus.export(self.app_path, self._obj_manager)
        self._exported_paths.append(self.app_path)

        # Export services and their characteristics; also track them
        for svc in services:
            self._export(svc)
            if isinstance(svc, GattService):
                self._gatt_services.append(svc)
            for ch in getattr(svc, "chars", []) or []:
                self._export(ch)
                self._gatt_chars.append(ch)

        # Now BlueZ can introspect the tree
        await gatt_mgr.call_register_application(self.app_path, {})

        # Create & export advertisement, then register it
        self._adv = BleAdvertisement(DEFAULT_ADV_PATH, self.local_name, self.service_uuids)
        self._export(self._adv)
        await self._stop_discovery_if_needed(self.bus, self._adapter_path)
        await adv_mgr.call_register_advertisement(self._adv.path, {})
        self._registered_adv = True
        print(f"[emg] Advertising as '{self.local_name}' (services={self.service_uuids})")

        # Install watcher to resume ads on disconnect
        self._install_disconnect_watcher()
        print("[emg] Device connection watcher installed")

    async def stop(self) -> None:
        if not self.bus:
            return
        try:
            if self._registered_adv and self._adv is not None:
                node = await self.bus.introspect(BLUEZ, self._adapter_path)
                adapter_obj = self.bus.get_proxy_object(BLUEZ, self._adapter_path, node)
                adv_mgr = adapter_obj.get_interface(LE_ADV_MANAGER_IFACE)
                try:
                    await adv_mgr.call_unregister_advertisement(self._adv.path)
                except Exception:
                    pass
        finally:
            self._registered_adv = False
            for path in reversed(self._exported_paths):
                try:
                    self.bus.unexport(path)
                except Exception:
                    pass
            self._exported_paths.clear()

    # Hook to wire into EMG Notify characteristic
    def get_stop_adv_hook(self) -> Callable[[], None]:
        def stop_now():
            async def _do():
                if not (self.bus and self._adv and self._registered_adv):
                    return
                try:
                    node = await self.bus.introspect(BLUEZ, self._adapter_path)
                    adapter_obj = self.bus.get_proxy_object(BLUEZ, self._adapter_path, node)
                    adv_mgr = adapter_obj.get_interface(LE_ADV_MANAGER_IFACE)
                    await adv_mgr.call_unregister_advertisement(self._adv.path)
                    self._registered_adv = False
                    print("[emg] StartNotify -> advertising stopped")
                except Exception as ex:
                    self._registered_adv = False
                    print(f"[emg] stop_now: UnregisterAdvertisement failed: {ex}")
            asyncio.create_task(_do())
        return stop_now

    # -------------------------- Internals ----------------------------
    def _export(self, obj: ServiceInterface) -> None:
        if not self.bus:
            raise RuntimeError("BleGattApp.run() must be called first")
        path = getattr(obj, "path", None)
        if not path:
            raise ValueError(f"ServiceInterface {obj} missing .path")
        self.bus.export(path, obj)
        self._exported_paths.append(path)

    def _install_disconnect_watcher(self) -> None:
        assert self.bus is not None

        def _is_device_signal(msg) -> bool:
            return (
                msg.member == "PropertiesChanged"
                and msg.interface == DBUS_PROP_IFACE
                and msg.path and msg.path.startswith("/org/bluez/hci")
                and len(msg.body) >= 3
                and msg.body[0] == DEVICE_IFACE
            )

        async def _on_device_props(_path: str, changed: Dict[str, Any]) -> None:
            if "Connected" not in changed:
                return
            val = changed["Connected"]
            connected = getattr(val, "value", val)
            if connected is True:
                return  # StartNotify already stopped ads deterministically
            if not self._registered_adv and self._adv is not None:
                try:
                    node = await self.bus.introspect(BLUEZ, self._adapter_path)
                    adapter_obj = self.bus.get_proxy_object(BLUEZ, self._adapter_path, node)
                    adv_mgr = adapter_obj.get_interface(LE_ADV_MANAGER_IFACE)
                    await adv_mgr.call_register_advertisement(self._adv.path, {})
                    self._registered_adv = True
                    print("[emg] Disconnected -> advertising resumed")
                except Exception as ex:
                    print(f"[emg] watcher: failed to re-register advertisement: {ex}")

        def _signal_handler(msg):
            if not _is_device_signal(msg):
                return
            asyncio.create_task(_on_device_props(msg.path, msg.body[1]))

        self.bus.add_message_handler(_signal_handler)
