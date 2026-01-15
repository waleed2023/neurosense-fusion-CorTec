# emg/fusion/fusion_manager.py
from __future__ import annotations

import asyncio
from typing import Optional, List

from app.fusion.rms_mag import OnlineRMSWindow, OnlineMagWindow
from app.manager.broadcaster import Broadcaster
from app.measurment.timing_mark_logger import TimingMarkLogger
from app.proto import sensor_pb2, fusion_pb2
from app.manager.config import (
    SENSOR_METRICS_LOG_FILE,
    WINDOW_MS,
    STEP_MS,
    MAX_DELAY_MS,
    now_ns,
    ns_to_us,
)


class FusionManager:
    """
    Fusion clock is driven by HAL timestamps (ts_us) only.
    - Starts as soon as *one* sensor delivers samples.
    - If one sensor has no samples in the window -> its output is 0 and valid=false.
    """

    def __init__(
        self,
        emg_broadcaster: Broadcaster,
        acc_broadcaster: Broadcaster,
        fusion_broadcaster: Broadcaster,
        *,
        window_ms: int = WINDOW_MS,
        step_ms: int = STEP_MS,
        max_delay_ms: int = MAX_DELAY_MS,
        min_emg_samples: int = 1,
        min_acc_samples: int = 1,
        max_steps_per_loop: int = 5000,
    ):
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster

        self._window_us = int(window_ms) * 1000
        self._step_us = max(1, int(step_ms) * 1000)
        self._max_delay_us = max(0, int(max_delay_ms) * 1000)

        self._min_emg_samples = int(min_emg_samples)
        self._min_acc_samples = int(min_acc_samples)
        self._max_steps_per_loop = int(max_steps_per_loop)

        self._task: Optional[asyncio.Task] = None
        self._seq: int = 0

        self._debug_emg_read: int = 0
        self._debug_acc_read: int = 0

        # Online windows (we use ts_us units consistently)
        self._emg_rms_win = OnlineRMSWindow(self._window_us)
        self._acc_mag_win = OnlineMagWindow(self._window_us)

        # Timing marks
        self._marklog = TimingMarkLogger(log_file=SENSOR_METRICS_LOG_FILE)
        self._BLE_fusion_idx = 0
        self._ACC_fusion_idx = 0
        self.Fusion_idx = 0

        # HAL-driven clock state
        self._first_ts_us: Optional[int] = None
        self._latest_emg_ts_us: Optional[int] = None
        self._latest_acc_ts_us: Optional[int] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fusion-manager")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    @property
    def debug_emg_read(self) -> int:
        return self._debug_emg_read

    @property
    def debug_acc_read(self) -> int:
        return self._debug_acc_read

    def _mark(self, point: str, idx: int) -> None:
        self._marklog.mark(point, idx, ns_to_us(now_ns()))

    @staticmethod
    def _partition_by_ts_us(buf: List, ts_end_us: int):
        ready, rest = [], []
        for s in buf:
            (ready if s.ts_us <= ts_end_us else rest).append(s)
        return ready, rest

    async def _drain_emg(self, q: asyncio.Queue, buf: List[sensor_pb2.EmgSample]) -> None:
        while True:
            try:
                s: sensor_pb2.EmgSample = q.get_nowait()
            except asyncio.QueueEmpty:
                return

            self._BLE_fusion_idx += 1
            self._mark("BLEManager_Fusion_in", self._BLE_fusion_idx)

            self._debug_emg_read += 1
            buf.append(s)

            ts = int(getattr(s, "ts_us", 0))
            if ts > 0:
                self._latest_emg_ts_us = ts if self._latest_emg_ts_us is None else max(self._latest_emg_ts_us, ts)
                if self._first_ts_us is None:
                    self._first_ts_us = ts
                else:
                    self._first_ts_us = min(self._first_ts_us, ts)

    async def _drain_acc(self, q: asyncio.Queue, buf: List[sensor_pb2.AccelSample]) -> None:
        while True:
            try:
                s: sensor_pb2.AccelSample = q.get_nowait()
            except asyncio.QueueEmpty:
                return

            self._ACC_fusion_idx += 1
            self._mark("ACCManager_Fusion_in", self._ACC_fusion_idx)

            self._debug_acc_read += 1
            buf.append(s)

            ts = int(getattr(s, "ts_us", 0))
            if ts > 0:
                self._latest_acc_ts_us = ts if self._latest_acc_ts_us is None else max(self._latest_acc_ts_us, ts)
                if self._first_ts_us is None:
                    self._first_ts_us = ts
                else:
                    self._first_ts_us = min(self._first_ts_us, ts)

    async def _run(self) -> None:
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        emg_buf: List[sensor_pb2.EmgSample] = []
        acc_buf: List[sensor_pb2.AccelSample] = []

        next_tick_us: Optional[int] = None

        try:
            while True:
                # 1) drain input queues
                await self._drain_emg(emg_q, emg_buf)
                await self._drain_acc(acc_q, acc_buf)

                # 2) start fusion clock once first sample exists
                if next_tick_us is None and self._first_ts_us is not None:
                    base_tick = int(self._first_ts_us) + self._max_delay_us
                    # ceil-align to step grid
                    next_tick_us = ((base_tick + self._step_us - 1) // self._step_us) * self._step_us

                # 3) if still no sensor data -> idle
                if next_tick_us is None:
                    await asyncio.sleep(0.01)
                    continue

                # 4) drive clock by newest HAL timestamp (watermark)
                watermark_us: Optional[int] = None
                if self._latest_emg_ts_us is not None:
                    watermark_us = self._latest_emg_ts_us
                if self._latest_acc_ts_us is not None:
                    watermark_us = watermark_us if watermark_us is not None else self._latest_acc_ts_us
                    watermark_us = max(watermark_us, self._latest_acc_ts_us)

                if watermark_us is None:
                    await asyncio.sleep(0.01)
                    continue

                allowed_tick_us = int(watermark_us) + self._max_delay_us

                steps = 0
                while next_tick_us <= allowed_tick_us and steps < self._max_steps_per_loop:
                    win_end_us = int(next_tick_us) - self._max_delay_us
                    win_start_us = win_end_us - self._window_us

                    # EMG
                    if emg_buf:
                        ready, emg_buf = self._partition_by_ts_us(emg_buf, win_end_us)
                        for s in ready:
                            self._emg_rms_win.add_sample(int(s.ts_us), float(s.value))

                    # ACC
                    if acc_buf:
                        ready, acc_buf = self._partition_by_ts_us(acc_buf, win_end_us)
                        for s in ready:
                            self._acc_mag_win.add_sample(int(s.ts_us), float(abs(s.mag)))

                    # slide windows
                    self._emg_rms_win.drop_older_than(win_start_us)
                    self._acc_mag_win.drop_older_than(win_start_us)

                    fused = self._compute_fused_sample(ts_us=win_end_us)
                    await self._fusion_b.publish(fused)

                    self.Fusion_idx += 1
                    self._mark("FusionManager_out", self.Fusion_idx)

                    next_tick_us += self._step_us
                    steps += 1

                # 5) wait a bit for new sensor data (keeps loop responsive)
                await asyncio.sleep(0.001)

        finally:
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)

    def _compute_fused_sample(self, *, ts_us: int) -> fusion_pb2.FusedSample:
        emg_rms, emg_count = self._emg_rms_win.get_rms()
        move_level, move_var, acc_count = self._acc_mag_win.get_stats()

        emg_valid = emg_count >= self._min_emg_samples
        acc_valid = acc_count >= self._min_acc_samples

        emg_level = (emg_rms / 1000.0) if emg_valid else 0.0
        if not acc_valid:
            move_level = 0.0
            move_var = 0.0

        self._seq += 1
        return fusion_pb2.FusedSample(
            ts_us=int(ts_us),
            seq=int(self._seq),
            emg_level=float(emg_level),
            emg_valid=bool(emg_valid),
            emg_count=int(emg_count),
            move_level=float(move_level),
            move_var=float(move_var),
            acc_valid=bool(acc_valid),
            acc_count=int(acc_count),
        )
