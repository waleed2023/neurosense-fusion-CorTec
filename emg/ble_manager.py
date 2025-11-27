import sys
import time
import struct
import asyncio
from typing import Optional
from bleak import BleakClient, BleakScanner
from .config import ANALOG_CHAR, BATTERY_CHAR, TARGET_MAC, TARGET_NAME, VALUE_MAX, LOG_PREFIX, EMG_IDLE_TIMEOUT_MS
from .proto import emg_pb2
from dbus_next.aio import MessageBus
from dbus_next import Variant, BusType
from .measurment.jitter_tracker import JitterTracker
import os
import random

def now_ms() -> int:
    return int(time.time() * 1000)



class BLEManager:
    """
    read EMG Values via BLE (Bleak) and publish EmgSample to the Broadcaster.
    """
    def __init__(self, broadcaster):
        self.broadcaster = broadcaster
        self._client: Optional[BleakClient] = None
        self._battery: int = 255
        self._stop = asyncio.Event()
        # cache for resolved MAC; once we know it, we don't scan again
        self._resolved_mac: Optional[str] = TARGET_MAC or None
        # cache for last EMG timestamp, used for reconnect on idle
        self._last_emg_ms = 0
        self._idle_timeout_ms = EMG_IDLE_TIMEOUT_MS  # default 5s
        self._jitter = JitterTracker(nominal_period_ms=100.0, name="EMG") # nominal 10 Hz


    async def stop(self):
        self._stop.set()
        try:
            if self._client and self._client.is_connected:
                await self._client.disconnect()
        except Exception:
            sys.stderr.write(f"{LOG_PREFIX} BLEManager stop error\n")
            pass
        self._jitter._log_jitter_summary()


    async def _resolve_mac(self, timeout: float = 3.0) -> Optional[str]:
        """
        Resolve target MAC once.
        - if TARGET_MAC is configured or already cached, use it directly (no scan)
        - otherwise scan once by name and cache the result
        """
        # this is the max time we wait for a scan
        # set it after trying default parameter to avoid mypy error
        # already cached → no scan
        if self._resolved_mac:
            return self._resolved_mac

        # env/config given → just use it and remember
        if TARGET_MAC:
            self._resolved_mac = TARGET_MAC
            sys.stdout.write(f"{LOG_PREFIX} using configured MAC {self._resolved_mac}\n")
            return self._resolved_mac

        # no MAC known yet → one short scan by name
        sys.stdout.write(f"{LOG_PREFIX} scanning for '{TARGET_NAME}' ...\n")
        devs = await BleakScanner.discover(timeout=timeout)
        target_lower = TARGET_NAME.lower()

        for dev in devs:
            name = (dev.name or "").lower()
            if target_lower in name:
                self._resolved_mac = dev.address
                sys.stdout.write(f"{LOG_PREFIX} found {TARGET_NAME} at {self._resolved_mac}\n")
                break

        if not self._resolved_mac:
            sys.stderr.write(f"{LOG_PREFIX} target '{TARGET_NAME}' not found\n")

        return self._resolved_mac

    def _on_emg(self, _handle: int, data: bytearray):
        try:
            if len(data) < 2:
                return
            v = struct.unpack_from("<H", data, 0)[0]
            if v > VALUE_MAX:
                v = VALUE_MAX

            # get current timestamp once and use it everywhere
            now = now_ms()
            self._last_emg_ms = now

            sample = emg_pb2.EmgSample(
                value=v,
                ts_ms=now,
                battery=self._battery,
            )
            asyncio.get_event_loop().create_task(
                self.broadcaster.publish(sample)
            )

            # --- Jitter measure (Sensor -> HAL) ---
            self._jitter.add(now)

        except Exception as e:
            sys.stderr.write(f"{LOG_PREFIX} on_emg error: {e}\n")


    
    async def run(self):
        backoff = 1.0 # for reconnect device
        while not self._stop.is_set():
            try:
                mac = await self._resolve_mac()
                if not mac:
                    await asyncio.sleep(backoff + random.uniform(0, 0.5))
                    backoff = min(backoff * 2, 10)
                    continue

                sys.stdout.write(f"{LOG_PREFIX} connecting to {mac} ...\n")
                async with BleakClient(mac, timeout=15.0) as client:
                    self._client = client
                    self._battery = 255

                    try:
                        b = await client.read_gatt_char(BATTERY_CHAR)
                        if b and len(b) >= 1:
                            self._battery = int(b[0])
                        sys.stdout.write(f"{LOG_PREFIX} Battery {self._battery}%\n")
                    except Exception:
                        sys.stderr.write(f"{LOG_PREFIX} Battery read error\n")
                        pass

                    await client.start_notify(ANALOG_CHAR, self._on_emg)
                    sys.stdout.write(f"{LOG_PREFIX} EMG Notify active\n")
                    backoff = 1.0
                    self._last_emg_ms = now_ms()
                    while client.is_connected and not self._stop.is_set():
                        await asyncio.sleep(0.5)
                        if self._idle_timeout_ms > 0 and self._last_emg_ms:
                            if (now_ms() - self._last_emg_ms) > self._idle_timeout_ms:
                                sys.stdout.write(f"{LOG_PREFIX} no EMG for {self._idle_timeout_ms}ms → reconnect\n")
                                await client.disconnect()   # breaks loop; outer while will retry
                                break
                    try:
                        await client.stop_notify(ANALOG_CHAR)
                    except Exception:
                        sys.stderr.write(f"{LOG_PREFIX} EMG Notify stop error\n")
                        pass

            except asyncio.CancelledError:
                sys.stdout.write(f"{LOG_PREFIX} BLE: Cancelled\n")
                break
            except Exception as e:
                sys.stdout.write(f"{LOG_PREFIX} BLE: {e}\n")
                await asyncio.sleep(backoff + random.uniform(0, 0.5))
                backoff = min(backoff * 2, 10)
            finally:
                self._client = None
