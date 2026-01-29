# emg/ble_manager.py
from __future__ import annotations

import sys
import struct
import asyncio
import random
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
    now_ns,
    ns_to_us,
    EMG_RX_QUEUE_MAX,
    EMG_RX_DROP_OLDEST_ON_FULL,
)
from ..proto import sensor_pb2
from app.measurment.timing_mark_logger import TimingMarkLogger





class BLEManager:
    """
    Reads EMG values via BLE (Bleak) and publishes EmgSample to the Broadcaster.
    Uses Unix epoch timestamps in µs (ts_us).
    """

    def __init__(self, broadcaster):
        self.broadcaster = broadcaster
        self._client: Optional[BleakClient] = None
        self._battery: int = 255
        self._stop = asyncio.Event()

        # cache for resolved MAC; once we know it, we don't scan again
        self._resolved_mac: Optional[str] = TARGET_MAC or None

        # last EMG timestamp (µs) for idle reconnect
        self._last_emg_us: int = 0
        self._idle_timeout_us: int = int(EMG_IDLE_TIMEOUT_MS) * 1000  # ms -> µs

        # timing marks
        self._marklog = TimingMarkLogger(log_file=SENSOR_METRICS_LOG_FILE)
        self._hal_idx = 0

        # queue + worker state (store (idx, sample) to match OUT to ENTRY)
        self._emg_rx_q: asyncio.Queue[tuple[int, sensor_pb2.EmgSample]] = asyncio.Queue(
            maxsize=EMG_RX_QUEUE_MAX
        )
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._publisher_task: Optional[asyncio.Task] = None

    async def stop(self):
        self._stop.set()

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
        - if TARGET_MAC configured or already cached, use it directly (no scan)
        - otherwise scan once by name and cache the result
        """
        if self._resolved_mac:
            return self._resolved_mac

        if TARGET_MAC:
            self._resolved_mac = TARGET_MAC
            sys.stdout.write(f"{LOG_PREFIX} using configured MAC {self._resolved_mac}\n")
            return self._resolved_mac

        sys.stdout.write(f"{LOG_PREFIX} scanning for '{TARGET_NAME}' .\n")
        devs = await BleakScanner.discover(timeout=timeout)
        target_lower = (TARGET_NAME or "").lower()

        for dev in devs:
            name = (dev.name or "").lower()
            if target_lower and target_lower in name:
                self._resolved_mac = dev.address
                sys.stdout.write(f"{LOG_PREFIX} found {TARGET_NAME} at {self._resolved_mac}\n")
                break

        if not self._resolved_mac:
            sys.stderr.write(f"{LOG_PREFIX} target '{TARGET_NAME}' not found\n")

        return self._resolved_mac

    def _try_enqueue_emg(self, item: tuple[int, sensor_pb2.EmgSample]) -> None:
        try:
            self._emg_rx_q.put_nowait(item)
        except asyncio.QueueFull:
            if EMG_RX_DROP_OLDEST_ON_FULL:
                try:
                    _ = self._emg_rx_q.get_nowait()  # drop one oldest
                except asyncio.QueueEmpty:
                    return
                try:
                    self._emg_rx_q.put_nowait(item)
                except asyncio.QueueFull:
                    return
            # else: drop newest (do nothing)

    async def _publisher_worker(self) -> None:
        while not self._stop.is_set():
            try:
                idx, sample = await asyncio.wait_for(self._emg_rx_q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            try:
                await self.broadcaster.publish(sample)
                self._marklog.mark("EMG_HAL_OUT", idx, ns_to_us(now_ns()))
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

            t_ns = now_ns()
            ts_us = ns_to_us(t_ns)  # Unix epoch in µs

            self._hal_idx += 1
            idx = self._hal_idx
            self._marklog.mark("EMG_HAL_DATA_WITHOUT_SCALE", idx, ts_us)
            self._marklog.mark("EMG_HAL_ENTRY", idx, ts_us)
            self._last_emg_us = ts_us

            sample = sensor_pb2.EmgSample(
                value=v,
                ts_us=ts_us,
                battery=self._battery,
            )

            # enqueue (thread-safe)
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._try_enqueue_emg, (idx, sample))
            else:
                self._try_enqueue_emg((idx, sample))

        except Exception as e:
            sys.stderr.write(f"{LOG_PREFIX} on_emg error: {e}\n")

    async def run(self):
        # capture loop + start publisher worker once
        self._loop = asyncio.get_running_loop()
        if not self._publisher_task or self._publisher_task.done():
            self._publisher_task = asyncio.create_task(self._publisher_worker())

        backoff = 1.0
        while not self._stop.is_set():
            try:
                mac = await self._resolve_mac()
                if not mac:
                    await asyncio.sleep(backoff + random.uniform(0, 0.5))
                    backoff = min(backoff * 2, 10)
                    continue

                sys.stdout.write(f"{LOG_PREFIX} connecting to {mac} .\n")
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
                    self._last_emg_us = ns_to_us(now_ns())

                    while client.is_connected and not self._stop.is_set():
                        await asyncio.sleep(0.5)

                        if self._idle_timeout_us > 0 and self._last_emg_us:
                            now_us = ns_to_us(now_ns())
                            if (now_us - self._last_emg_us) > self._idle_timeout_us:
                                sys.stdout.write(
                                    f"{LOG_PREFIX} no EMG for {EMG_IDLE_TIMEOUT_MS}ms → reconnect\n"
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

        if self._publisher_task and not self._publisher_task.done():
            self._publisher_task.cancel()
