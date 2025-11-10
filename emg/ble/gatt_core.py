#!/usr/bin/env python3
"""
Core GATT helper for BlueZ via dbus-next.

This module wires together:
  - D-Bus system bus connection
  - Bluetooth adapter discovery and preparation
  - GATT application (services + characteristics)
  - LE advertising with auto‑recovery (stop discovery, power‑cycle adapter, retry)

Design goals:
  * Keep the GATT objects minimal and compliant with BlueZ expectations.
  * Keep the advertising packet small (name + first service UUID only).
  * Register the advertisement once and keep it for the lifetime of the process.
  * Provide a clean stop() for systemd shutdowns to avoid stale state.

All comments from the original file are preserved in spirit and expanded where helpful.
"""

import asyncio
from typing import Callable, Awaitable, List, Optional, Dict, Any

from dbus_next.errors import DBusError
from dbus_next.aio import MessageBus
from dbus_next import Variant, BusType
from dbus_next.service import ServiceInterface, method, dbus_property
from dbus_next.constants import PropertyAccess

# ---- BlueZ / D-Bus constants ----
BLUEZ                   = "org.bluez"
ADAPTER_IFACE           = "org.bluez.Adapter1"
GATT_MANAGER_IFACE      = "org.bluez.GattManager1"
LE_ADV_MANAGER_IFACE    = "org.bluez.LEAdvertisingManager1"
GATT_SERVICE_IFACE      = "org.bluez.GattService1"
GATT_CHAR_IFACE         = "org.bluez.GattCharacteristic1"
PROPERTIES_IFACE        = "org.freedesktop.DBus.Properties"
OBJ_MANAGER_IFACE       = "org.freedesktop.DBus.ObjectManager"
ADVERTISING_IFACE       = "org.bluez.LEAdvertisement1"

# ---- Small helpers ----

class RpcStream:
    """
    Small helper to manage a long-running async producer task.

    It is used for streaming loops (e.g. EMG / ACC) so they can be
    started and cancelled cleanly when the client subscribes/unsubscribes.
    """

    def __init__(self, coro_factory: Callable[[], Awaitable[None]]):
        self._coro_factory = coro_factory
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Start (or restart) the underlying async task."""
        self.stop()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._coro_factory())

    def stop(self) -> None:
        """Cancel the running task if any."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None


# ---- GATT primitives ----

class GattService(ServiceInterface):
    """
    Minimal implementation of org.bluez.GattService1 for a local GATT service.
    """

    def __init__(self, path: str, uuid: str, primary: bool = True):
        super().__init__(GATT_SERVICE_IFACE)
        self.path = path
        self.uuid = uuid
        self.primary = primary
        self.chars: List[ServiceInterface] = []

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return self.primary

    @dbus_property(access=PropertyAccess.READ)
    def Includes(self) -> "ao":
        # No included services.
        return []


class NotifyChar(ServiceInterface):
    """
    Characteristic that supports 'read' and 'notify'.

    The application calls `await notify(bytes)` to push a new value.
    If a client has called StartNotify, we emit a PropertiesChanged
    signal on the 'Value' property so BlueZ will send a notification.
    """

    def __init__(self, path: str, uuid: str, service: GattService):
        super().__init__(GATT_CHAR_IFACE)
        self.path = path
        self.uuid = uuid
        self.service = service
        self.flags: List[str] = ["read", "notify"]
        # BlueZ / dbus-next expect 'ay' as Python 'bytes'
        self._value: bytes = b""
        self._notifying: bool = False

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return self.service.path

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return self.flags

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":
        # Return raw bytes as required for 'ay'
        return self._value

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":
        """
        Basic ReadValue implementation.
        Returns the last value that was notified.
        """
        return self._value

    @method()
    def StartNotify(self) -> None:
        """
        Called by the client when it subscribes for notifications.
        """
        self._notifying = True

    @method()
    def StopNotify(self) -> None:
        """
        Called by the client when it unsubscribes from notifications.
        """
        self._notifying = False

    async def notify(self, data: bytes) -> None:
        """
        Update the characteristic value and emit a PropertiesChanged signal
        if notifications are currently enabled.
        """
        # Store as bytes (dbus-next will wrap this for signature 'ay')
        self._value = data
        if self._notifying:
            self.emit_properties_changed({"Value": self._value}, [])


class WriteChar(ServiceInterface):
    """
    Characteristic that supports 'write' / 'write-without-response'.

    When a client writes to this characteristic, the provided callback
    is invoked with the raw bytes.
    """

    def __init__(
        self,
        path: str,
        uuid: str,
        service: GattService,
        on_write: Callable[[bytes], None],
        flags: Optional[List[str]] = None,
    ):
        super().__init__(GATT_CHAR_IFACE)
        self.path = path
        self.uuid = uuid
        self.service = service
        self._on_write = on_write
        # Allow overriding flags, but provide sensible defaults.
        self.flags = flags or ["write", "write-without-response"]

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
    def WriteValue(self, value: "ay", options: "a{sv}") -> None:
        """
        Called by the client when it writes to this characteristic.
        """
        data = bytes(value)
        self._on_write(data)

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":
        """
        Optional ReadValue implementation. For a pure write-only
        characteristic we can just return an empty value.
        """
        return []


class BleAdvertisement(ServiceInterface):
    """
    Minimal LE Advertisement object implementing org.bluez.LEAdvertisement1.
    BlueZ often prefers when you only set the bare minimum.
    """

    def __init__(self, path: str, local_name: str, service_uuids: List[str]):
        super().__init__(ADVERTISING_IFACE)
        self.path = path
        self._local_name = local_name
        self._service_uuids = service_uuids

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        # 'peripheral' = connectable advertisement.
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":
        return self._local_name

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return self._service_uuids

    @dbus_property(access=PropertyAccess.READ)
    def Includes(self) -> "as":
        # Erstmal nichts extra bewerben – keep it simple.
        return []

    @method()
    def Release(self) -> None:
        """
        Called by BlueZ when the advertisement is unregistered.
        """
        print("[emg] Advertisement released")


class GattApplication(ServiceInterface):
    """
    Simple application root implementing org.freedesktop.DBus.ObjectManager.

    BlueZ uses GetManagedObjects() to discover all services and characteristics
    that belong to this application under a given root path.
    """

    def __init__(self, path: str, services: List[GattService]):
        super().__init__(OBJ_MANAGER_IFACE)
        self.path = path
        self._services = services

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        """
        Return a dictionary describing all managed objects (services
        and characteristics) in this application.

        The structure is:
            {
              object_path: {
                interface_name: {
                  property_name: Variant(...)
                  ...
                },
                ...
              },
              ...
            }
        """
        managed: Dict[str, Dict[str, Dict[str, Variant]]] = {}

        for svc in self._services:
            # Service object description
            svc_entry: Dict[str, Dict[str, Variant]] = {}
            svc_props: Dict[str, Variant] = {
                "UUID": Variant("s", svc.uuid),
                "Primary": Variant("b", svc.primary),
                "Includes": Variant("ao", []),
            }
            svc_entry[GATT_SERVICE_IFACE] = svc_props
            managed[svc.path] = svc_entry

            # Characteristic objects for this service
            for ch in getattr(svc, "chars", []):
                ch_entry: Dict[str, Dict[str, Variant]] = {}
                # Only describe GATT characteristics here.
                if isinstance(ch, ServiceInterface) and ch.introspection.name == GATT_CHAR_IFACE:
                    flags = getattr(ch, "flags", [])
                    ch_props: Dict[str, Variant] = {
                        "UUID": Variant("s", getattr(ch, "uuid", "")),
                        "Service": Variant("o", svc.path),
                        "Flags": Variant("as", flags),
                    }
                    ch_entry[GATT_CHAR_IFACE] = ch_props
                    managed[ch.path] = ch_entry

        return managed


# ---- High level app that owns bus, adapter, services, advertisement ----

class BleGattApp:
    """
    High-level helper that wires together:
      - D-Bus system bus connection
      - Bluetooth adapter discovery
      - GATT application (services + characteristics)
      - LE advertising (with auto‑recovery)

    Usage:
        app = BleGattApp(local_name="NeuroSense", services=[emg_svc, acc_svc])
        await app.run()
    """

    def __init__(self, local_name: str, services: List[GattService]):
        self.local_name = local_name
        self.services = services
        self.bus: Optional[MessageBus] = None
        self._adapter_path: Optional[str] = None
        self._adv: Optional[BleAdvertisement] = None
        self._app_root: Optional[GattApplication] = None
        # Internal guards/state
        self._registered_adv: bool = False
        self._registering_lock = asyncio.Lock()

    async def _find_adapter_path(self) -> str:
        """
        Try to locate a Bluetooth adapter that implements org.bluez.Adapter1.

        If automatic discovery fails, fall back to '/org/bluez/hci0'
        (common on Raspberry Pi) and raise if that also fails.
        """
        assert self.bus is not None

        # Try to discover adapter via ObjectManager on '/'
        try:
            root = await self.bus.introspect(BLUEZ, "/")
        except Exception:
            root = None

        adapter_path: Optional[str] = None

        if root is not None:
            for n in root.nodes:
                candidate = f"/org/bluez/{n}"
                try:
                    node = await self.bus.introspect(BLUEZ, candidate)
                except Exception:
                    continue

                for iface in node.interfaces:
                    if iface.name == ADAPTER_IFACE:
                        adapter_path = candidate
                        break
                if adapter_path:
                    break

        # Fallback to hci0 if discovery did not find anything.
        if adapter_path is None:
            fallback = "/org/bluez/hci0"
            try:
                await self.bus.introspect(BLUEZ, fallback)
                print(f"[emg] Using fallback Bluetooth adapter path: {fallback}")
                adapter_path = fallback
            except Exception as e:
                raise RuntimeError(f"no Bluetooth Adapter found ({e})")

        return adapter_path

    async def _ensure_powered(self, adapter_obj) -> None:
        """Ensure the adapter is powered on (Adapter1.Powered = True)."""
        props_iface = adapter_obj.get_interface(PROPERTIES_IFACE)
        powered_variant = await props_iface.call_get(ADAPTER_IFACE, "Powered")
        if bool(powered_variant.value):
            return
        try:
            await props_iface.call_set(ADAPTER_IFACE, "Powered", Variant("b", True))
        except DBusError as e:
            # Einmal harte Recovery und Retry
            print(f"[emg] Power on failed ({e.type}: {e.text}) → bounce & retry")
            await self._bounce_adapter()
            await asyncio.sleep(0.8)
            # zweiter Versuch:
            await props_iface.call_set(ADAPTER_IFACE, "Powered", Variant("b", True))

    async def _stop_discovery_if_needed(self, adapter_obj) -> None:
        """Stop adapter discovery if active (BlueZ rejects adv while discovering)."""
        props_iface = adapter_obj.get_interface(PROPERTIES_IFACE)
        adapter_iface = adapter_obj.get_interface(ADAPTER_IFACE)
        discovering = bool((await props_iface.call_get(ADAPTER_IFACE, "Discovering")).value)
        if discovering:
            try:
                await adapter_iface.call_stop_discovery()
            except Exception:
                # Ignore; we'll still attempt registration
                pass

    async def _bounce_adapter(self) -> None:
        """Toggle Adapter1.Powered off→on to clear funky controller state."""
        assert self.bus is not None and self._adapter_path is not None
        node = await self.bus.introspect(BLUEZ, self._adapter_path)
        obj = self.bus.get_proxy_object(BLUEZ, self._adapter_path, node)
        props = obj.get_interface(PROPERTIES_IFACE)
        try:
            await props.call_set(ADAPTER_IFACE, "Powered", Variant("b", False))
            await asyncio.sleep(0.6)
            await props.call_set(ADAPTER_IFACE, "Powered", Variant("b", True))
            print("[emg] Adapter power-cycled")
        except Exception as e:
            print(f"[emg] Adapter bounce failed: {e}")

    async def _register_advertisement_with_recovery(self, adapter_obj) -> None:
        """
        Register advertisement; on failure, recover and retry once.
        This specifically targets BlueZ errors like Authentication Failed (0x05),
        Failed (0x03), and InProgress while discovery is active.
        """
        adv_mgr = adapter_obj.get_interface(LE_ADV_MANAGER_IFACE)

        # First try: stop discovery then register
        await self._stop_discovery_if_needed(adapter_obj)
        try:
            await adv_mgr.call_register_advertisement(self._adv.path, {})
            self._registered_adv = True
            return
        except DBusError as e:
            print(f"[emg] RegisterAdvertisement failed ({e.type}: {e.text}) → recover and retry")

        # Recovery: bounce adapter, wait, stop discovery again, retry once
        await self._bounce_adapter()
        await asyncio.sleep(0.8)  # small settle time after power-cycle
        await self._stop_discovery_if_needed(adapter_obj)

        try:
            await adv_mgr.call_register_advertisement(self._adv.path, {})
            self._registered_adv = True
            print("[emg] Advertisement registered after recovery")
        except DBusError as e:
            print(f"[emg] Retry RegisterAdvertisement failed ({e.type}: {e.text})")
            raise

    async def run(self) -> None:
        """
        Connect to the system bus, export services and advertisement,
        register the GATT application and start advertising (with auto‑recovery).
        """
        # Guard: only perform registration once per process
        async with self._registering_lock:
            if self._registered_adv:
                return

            # Connect to the system bus where BlueZ lives.
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

            # Find an adapter.
            self._adapter_path = await self._find_adapter_path()

            # Proxy objects for the adapter
            node = await self.bus.introspect(BLUEZ, self._adapter_path)
            adapter_obj = self.bus.get_proxy_object(BLUEZ, self._adapter_path, node)

            # Ensure powered on
            await self._ensure_powered(adapter_obj)

            # Export the application root (ObjectManager).
            self._app_root = GattApplication("/app", self.services)
            self.bus.export(self._app_root.path, self._app_root)

            # Export all services and characteristics.
            for svc in self.services:
                self.bus.export(svc.path, svc)
                for ch in svc.chars:
                    self.bus.export(ch.path, ch)

            # Register application with GattManager1.
            gatt_mgr = adapter_obj.get_interface(GATT_MANAGER_IFACE)
            await gatt_mgr.call_register_application(self._app_root.path, {})

            # Prepare and export LE advertisement.
            # IMPORTANT: Keep advertising packet small (max. 31 bytes),
            # therefore we only advertise the first service UUID.
            service_uuids = [self.services[0].uuid] if self.services else []
            adv_path = "/org/bluez/protoble/advertisement0"
            self._adv = BleAdvertisement(adv_path, self.local_name, service_uuids)
            self.bus.export(self._adv.path, self._adv)

            # Robust registration with auto-recovery
            await self._register_advertisement_with_recovery(adapter_obj)

            print(f"[emg] Advertising as '{self.local_name}' (services={service_uuids})")

    async def stop(self) -> None:
        """Unregister advertisement and unexport objects cleanly (optional but recommended)."""
        if not self.bus or not self._adapter_path:
            return
        try:
            node = await self.bus.introspect(BLUEZ, self._adapter_path)
            adapter_obj = self.bus.get_proxy_object(BLUEZ, self._adapter_path, node)
            adv_mgr = adapter_obj.get_interface(LE_ADV_MANAGER_IFACE)
            if self._adv and self._registered_adv:
                try:
                    await adv_mgr.call_unregister_advertisement(self._adv.path)
                except Exception:
                    pass
        finally:
            # Unexport objects (safe if already gone)
            if self._adv:
                try:
                    self.bus.unexport(self._adv.path)
                except Exception:
                    pass
            if self._app_root:
                try:
                    self.bus.unexport(self._app_root.path)
                except Exception:
                    pass
            for svc in (self.services or []):
                try:
                    self.bus.unexport(svc.path)
                except Exception:
                    pass
                for ch in getattr(svc, "chars", []):
                    try:
                        self.bus.unexport(ch.path)
                    except Exception:
                        pass
            self._registered_adv = False
