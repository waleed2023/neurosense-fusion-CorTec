import sys
import time
import struct
import asyncio
from typing import Optional
from bleak import BleakClient, BleakScanner
from .config import ANALOG_CHAR, BATTERY_CHAR, TARGET_MAC, TARGET_NAME, VALUE_MAX, LOG_PREFIX
from .proto import emg_pb2

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
        self._resolved_mac: Optional[str] = TARGET_MAC or None

    async def stop(self):
        self._stop.set()
        try:
            if self._client and self._client.is_connected:
                await self._client.disconnect()
        except Exception:
            pass

    async def _resolve_mac(self, timeout: float = 6.0) -> Optional[str]:
        if TARGET_MAC:
            return TARGET_MAC
        devs = await BleakScanner.discover(timeout=timeout)
        for d in devs:
            name = (d.name or "").lower()
            if TARGET_NAME.lower() in name:
                return d.address
        return None

    def _on_emg(self, _handle: int, data: bytearray):
        try:
            if len(data) < 2:
                return
            v = struct.unpack_from("<H", data, 0)[0]
            if v > VALUE_MAX:
                v = VALUE_MAX
            sample = emg_pb2.EmgSample(value=v, ts_ms=now_ms(), battery=self._battery)
            asyncio.get_event_loop().create_task(self.broadcaster.publish(sample))
        except Exception as e:
            sys.stderr.write(f"{LOG_PREFIX} on_emg error: {e}\n")

    async def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                mac = await self._resolve_mac()
                if not mac:
                    await asyncio.sleep(backoff); backoff = min(backoff * 2, 10)
                    continue

                sys.stderr.write(f"{LOG_PREFIX} connected to {mac} ...\n")
                async with BleakClient(mac, timeout=15.0) as c:
                    self._client = c
                    self._battery = 255
                    try:
                        b = await c.read_gatt_char(BATTERY_CHAR)
                        if b and len(b) >= 1:
                            self._battery = int(b[0])
                        sys.stderr.write(f"{LOG_PREFIX} Battery {self._battery}%\n")
                    except Exception:
                        pass

                    await c.start_notify(ANALOG_CHAR, self._on_emg)
                    sys.stderr.write(f"{LOG_PREFIX} EMG Notify active\n")
                    backoff = 1.0

                    while c.is_connected and not self._stop.is_set():
                        await asyncio.sleep(0.5)

                    try:
                        await c.stop_notify(ANALOG_CHAR)
                    except Exception:
                        pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                sys.stderr.write(f"{LOG_PREFIX} BLE: {e}\n")
                await asyncio.sleep(backoff); backoff = min(backoff * 2, 10)
            finally:
                self._client = None
