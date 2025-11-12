#!/usr/bin/env python3
"""
Simplified BlueZ GATT core using dbus-next.

Goals:
  • Keep only what we need: app registration, advertising, clean export/unexport.
  • Stop advertising as soon as the tablet *really* starts streaming (StartNotify hook).
  • Resume advertising automatically when the central disconnects.
  • No kernel module resets, no heavy adapter bouncing here.

Usage sketch (outside this file):
  - Build your EMG/ACC services/characteristics (e.g., in emg_acc_services.py).
  - Pass the Notify characteristic an on_start_notify callback from
    BleGattApp.get_stop_adv_hook(adapter_obj) *or* call set_on_start_notify() later.
  - Start BleGattApp.run(bus, services) once.

This module exports:
  • BleAdvertisement: minimal LEAdvertisement1
  • NotifyChar: a small characteristic helper with StartNotify hook
  • BleGattApp: manages D-Bus exports, advertisement, and disconnect watcher
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Iterable, List, Optional

from dbus_next.aio import MessageBus
from dbus_next import Variant
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, method, dbus_property

# ---- BlueZ interfaces / constants ----
BLUEZ = "org.bluez"
OBJM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
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

    # ---- D-Bus properties ----
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

    # ---- D-Bus methods ----
    @method()
    def Release(self) -> None:
        # BlueZ may call this on shutdown/unregister; no action needed.
        pass


# ---------------------------------------------------------------------------
#  Minimal Notify characteristic with StartNotify hook
# ---------------------------------------------------------------------------
class NotifyChar(ServiceInterface):
    """Simple characteristic that supports Read/Notify and fires a hook on StartNotify.

    Not a full generic implementation—only what this project needs.
    """

    def __init__(
        self,
        path: str,
        uuid: str,
        service_path: str,
        on_start_notify: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(GATT_CHAR_IFACE)
        self.path = path
        self.uuid = uuid
        self.service_path = service_path
        self.flags: List[str] = ["read", "notify"]
        self._value: bytes = b""
        self._notifying: bool = False
        self._on_start_notify = on_start_notify

    # ---- Properties ----
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

    # ---- Methods ----
    @method()
    def ReadValue(self, _options: "a{sv}") -> "ay":
        return self._value

    @method()
    def StartNotify(self) -> None:
        self._notifying = True
        if self._on_start_notify:
            try:
                self._on_start_notify()
            except Exception as ex:
                print(f"[emg] StartNotify hook failed: {ex}")

    @method()
    def StopNotify(self) -> None:
        self._notifying = False

    async def notify(self, data: bytes) -> None:
        self._value = data
        if self._notifying:
            self.emit_properties_changed({"Value": self._value}, [])

    # Utility to (re)wire the hook after construction
    def set_on_start_notify(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_start_notify = cb


# ---------------------------------------------------------------------------
#  GATT App: export services, manage advertising, react to disconnects
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

    # --------------------------- Public API ---------------------------
    async def run(self, bus: MessageBus, services: Iterable[ServiceInterface]) -> None:
        """Export services, register advertisement, install disconnect watcher.
        Call once.
        """
        self.bus = bus

        # 1) Resolve adapter and managers
        node = await self.bus.introspect(BLUEZ, self._adapter_path)
        adapter_obj = self.bus.get_proxy_object(BLUEZ, self._adapter_path, node)
        gatt_mgr = adapter_obj.get_interface(GATT_MGR_IFACE)
        adv_mgr = adapter_obj.get_interface(LE_ADV_MANAGER_IFACE)

        # 2) Export services and their children
        for s in services:
            self._export(s)

        # Register the application with BlueZ
        await gatt_mgr.call_register_application(self.app_path, {})

        # 3) Create & export advertisement, then register it
        self._adv = BleAdvertisement(
            DEFAULT_ADV_PATH, self.local_name, self.service_uuids
        )
        self._export(self._adv)
        await adv_mgr.call_register_advertisement(self._adv.path, {})
        self._registered_adv = True
        print(f"[emg] Advertising as '{self.local_name}' (services={self.service_uuids})")

        # 4) Install watcher to resume ads on disconnect
        self._install_disconnect_watcher()
        print("[emg] Device connection watcher installed")

    async def stop(self) -> None:
        if not self.bus:
            return
        try:
            # Try to unregister advertisement gracefully
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
            # Unexport everything we exported
            for path in reversed(self._exported_paths):
                try:
                    self.bus.unexport(path)
                except Exception:
                    pass
            self._exported_paths.clear()

    # Provide a hook to wire into your EMG Notify characteristic
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
        # Objects must have a stable .path attribute
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

        async def _on_device_props(path: str, changed: Dict[str, Any]) -> None:
            if "Connected" not in changed:
                return
            val = changed["Connected"]
            connected = getattr(val, "value", val)
            if connected is True:
                # Do nothing here—StartNotify hook already stopped ads deterministically.
                return

            # On disconnect: resume advertising if we have stopped it
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
            # Fire-and-forget to keep callback fast
            asyncio.create_task(_on_device_props(msg.path, msg.body[1]))

        self.bus.add_message_handler(_signal_handler)
