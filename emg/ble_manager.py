import sys
import time
import struct
import asyncio
from typing import Optional
from bleak import BleakClient, BleakScanner
from .config import (
    ANALOG_CHAR,
    BATTERY_CHAR,
    TARGET_MAC,
    TARGET_NAME,
    VALUE_MAX,
    LOG_PREFIX,
    EMG_IDLE_TIMEOUT_MS,
    SENSOR_METRICS_LOG_FILE,
    MAX_RATE_HZ_EMG,
)
from .proto import emg_pb2
from dbus_next.aio import MessageBus
from dbus_next import Variant, BusType
from emg.measurment.timing_mark_logger import TimingMarkLogger
import os
import random


def now_ms() -> int:
    return int(time.time() * 1000)



EMG_RX_QUEUE_MAX = 2048
EMG_RX_DROP_OLDEST_ON_FULL = True


# --------------------- BLEManager ---------------------
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

        # mark logger + hal counter (für ENTRY/OUT Paare)
        self._marklog = TimingMarkLogger(log_file=SENSOR_METRICS_LOG_FILE)
        self._hal_idx = 0

        # queue + worker state (wir speichern (idx, sample), damit OUT zum ENTRY passt)
        self._emg_rx_q: asyncio.Queue[tuple[int, emg_pb2.EmgSample]] = asyncio.Queue(
            maxsize=EMG_RX_QUEUE_MAX
        )
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._publisher_task: Optional[asyncio.Task] = None




    async def stop(self):
        self._stop.set()

        # --- stop publisher worker ---
        if self._publisher_task and not self._publisher_task.done():
            self._publisher_task.cancel()

        try:
            if self._client and self._client.is_connected:
                await self._client.disconnect()
        except Exception:
            sys.stderr.write(f"{LOG_PREFIX} BLEManager stop error\n")

    async def _resolve_mac(self, timeout: float = 3.0) -> Optional[str]:
        """
        Resolve target MAC once.
        - if TARGET_MAC is configured or already cached, use it directly (no scan)
        - otherwise scan once by name and cache the result
        """
        if self._resolved_mac:
            return self._resolved_mac

        if TARGET_MAC:
            self._resolved_mac = TARGET_MAC
            sys.stdout.write(f"{LOG_PREFIX} using configured MAC {self._resolved_mac}\n")
            return self._resolved_mac

        sys.stdout.write(f"{LOG_PREFIX} scanning for '{TARGET_NAME}' ...\n")
        devs = await BleakScanner.discover(timeout=timeout)
        target_lower = TARGET_NAME.lower()

        for dev in devs:
            name = (dev.name or "").lower()
            if target_lower in name:
                self._resolved_mac = dev.address
                sys.stdout.write(
                    f"{LOG_PREFIX} found {TARGET_NAME} at {self._resolved_mac}\n"
                )
                break

        if not self._resolved_mac:
            sys.stderr.write(f"{LOG_PREFIX} target '{TARGET_NAME}' not found\n")

        return self._resolved_mac

    def _try_enqueue_emg(self, item: tuple[int, emg_pb2.EmgSample]) -> None:
        try:
            self._emg_rx_q.put_nowait(item)
        except asyncio.QueueFull:
            if EMG_RX_DROP_OLDEST_ON_FULL:
                try:
                    _ = self._emg_rx_q.get_nowait()  # drop one oldest (tuple)
                except asyncio.QueueEmpty:
                    return
                try:
                    self._emg_rx_q.put_nowait(item)
                except asyncio.QueueFull:
                    return
            # else: drop newest (do nothing)


    # --- single publisher worker (prevents task build-up) ---
    async def _publisher_worker(self) -> None:
        while not self._stop.is_set():
            try:
                idx, sample = await asyncio.wait_for(self._emg_rx_q.get(), timeout=0.5)

            except asyncio.TimeoutError:
                continue

            try:
                await self.broadcaster.publish(sample)
                self._marklog.mark("EMG_HAL_OUT", idx, now_ms())
            except Exception as e:
                sys.stderr.write(f"{LOG_PREFIX} EMG publish error: {e}\n")
            finally:
                try:
                    self._emg_rx_q.task_done()
                except Exception:
                    pass


    def _on_emg(self, _handle: int, data: bytearray):
        try:
            if len(data) < 2:
                return

            v = struct.unpack_from("<H", data, 0)[0]
            if v > VALUE_MAX:
                v = VALUE_MAX

            now = now_ms()
            self._hal_idx += 1
            idx = self._hal_idx
            self._marklog.mark("EMG_HAL_ENTRY", idx, now)
            self._last_emg_ms = now

            sample = emg_pb2.EmgSample(
                value=v,
                ts_ms=now,
                battery=self._battery,
            )

            # --- CHANGED: no create_task per sample; enqueue instead ---
            if self._loop is not None:
                # safe even if callback isn't on the event-loop thread
                self._loop.call_soon_threadsafe(self._try_enqueue_emg, (idx, sample))
            else:
                self._try_enqueue_emg((idx, sample))

        except Exception as e:
            sys.stderr.write(f"{LOG_PREFIX} on_emg error: {e}\n")

    async def run(self):
        # --- capture loop + start one worker once ---
        self._loop = asyncio.get_running_loop()
        if not self._publisher_task or self._publisher_task.done():
            self._publisher_task = asyncio.create_task(self._publisher_worker())

        backoff = 1.0  # for reconnect device
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

                    await client.start_notify(ANALOG_CHAR, self._on_emg)
                    sys.stdout.write(f"{LOG_PREFIX} EMG Notify active\n")
                    backoff = 1.0
                    self._last_emg_ms = now_ms()

                    while client.is_connected and not self._stop.is_set():
                        await asyncio.sleep(0.5)
                        if self._idle_timeout_ms > 0 and self._last_emg_ms:
                            if (now_ms() - self._last_emg_ms) > self._idle_timeout_ms:
                                sys.stdout.write(
                                    f"{LOG_PREFIX} no EMG for {self._idle_timeout_ms}ms → reconnect\n"
                                )
                                await client.disconnect()
                                break

                    try:
                        await client.stop_notify(ANALOG_CHAR)
                    except Exception:
                        sys.stderr.write(f"{LOG_PREFIX} EMG Notify stop error\n")

            except asyncio.CancelledError:
                sys.stdout.write(f"{LOG_PREFIX} BLE: Cancelled\n")
                break
            except Exception as e:
                sys.stdout.write(f"{LOG_PREFIX} BLE: {e}\n")
                await asyncio.sleep(backoff + random.uniform(0, 0.5))
                backoff = min(backoff * 2, 10)
            finally:
                self._client = None

        # --- cleanup worker on exit ---
        if self._publisher_task and not self._publisher_task.done():
            self._publisher_task.cancel()
        