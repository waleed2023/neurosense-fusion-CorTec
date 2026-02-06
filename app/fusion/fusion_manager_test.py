from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from typing import Optional, Deque

from app.fusion.rms_mag import OnlineRMSWindow, OnlineMagWindow
from app.manager.broadcaster import Broadcaster
from app.manager.config import (
    DISABLE_MARKS,
    SENSOR_METRICS_LOG_FILE,
    WINDOW_MS,
    STEP_MS,
    MAX_DELAY_MS,
    now_ns,
    ns_to_us,
)
from app.measurment.timing_mark_logger import TimingMarkLogger
from app.proto import sensor_pb2, fusion_pb2

# DeadlineMeter (live output)
from app.tests.client_emu import DeadlineMeter

MEASURE_ONLY = os.getenv("MEASURE_ONLY", "0") == "1"


class FusionManagerFeatureTest:
    """
    Feature-Fusion (Sliding Window + Tick Grid) mit:
      - dummy-analogen Timing-Marks (CSV)
      - DeadlineMeter (live avg_rate)

    Wichtige Marks:
      - Fusion_Feature_ENTER        : wall-time vor compute (pro Output-Tick)
      - Fusion_Feature_Exit         : wall-time nach compute (pro Output-Tick)
      - Fusion_Feature_OUT_TS       : ts_us (logische Output-Zeit)
      - Fusion_Feature_Publish_Exit : wall-time nach publish (nur wenn MEASURE_ONLY=0)
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
        hold_timeout_ms: int = 0,  # 0 = hold forever
    ) -> None:
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

        # Online windows
        self._emg_rms_win = OnlineRMSWindow(self._window_us)
        self._acc_mag_win = OnlineMagWindow(self._window_us)

        # Timing marks (CSV)
        self._marklog = None if DISABLE_MARKS else TimingMarkLogger(log_file=SENSOR_METRICS_LOG_FILE)

        # input counters (nur für Marks)
        self._emg_in_idx: int = 0
        self._acc_in_idx: int = 0

        # HAL clock state
        self._first_ts_us: Optional[int] = None
        self._latest_emg_ts_us: Optional[int] = None
        self._latest_acc_ts_us: Optional[int] = None
        self._clock_started: bool = False

        # sample-and-hold
        self._hold_timeout_us = max(0, int(hold_timeout_ms)) * 1000
        self._last_emg_level: float = 0.0
        self._last_emg_ok_ts_us: Optional[int] = None
        self._last_move_level: float = 0.0
        self._last_move_var: float = 0.0
        self._last_acc_ok_ts_us: Optional[int] = None

        # DeadlineMeter: target = 1e6/step_us (bei step=1ms => 1000Hz)
        target_hz = 1_000_000.0 / float(self._step_us)
        self._algo_meter = DeadlineMeter("FEATURE_ALGO_ONLY", target_hz=target_hz, log_every_s=1.0)

        # Optional: publish overhead separat (nur sinnvoll wenn MEASURE_ONLY=0)
        self._pub_meter = DeadlineMeter("FEATURE_PUBLISH", target_hz=target_hz, log_every_s=1.0)

    # ---------------- marks ----------------
    def _mark_wall(self, point: str, idx: int) -> None:
        if DISABLE_MARKS or self._marklog is None:
            return
        self._marklog.mark(point, idx, ns_to_us(now_ns()))

    def _mark_ts(self, point: str, idx: int, ts_us: int) -> None:
        if DISABLE_MARKS or self._marklog is None:
            return
        self._marklog.mark(point, idx, int(ts_us))

    # ---------------- lifecycle ----------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fusion-feature-test")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    # ---------------- helpers ----------------
    @staticmethod
    def _pop_ready_by_ts_us(buf: Deque, ts_end_us: int) -> list:
        ready = []
        while buf and int(getattr(buf[0], "ts_us", 0)) <= ts_end_us:
            ready.append(buf.popleft())
        return ready

    def _update_clock_state(self, ts_us: int, *, is_emg: bool) -> None:
        if ts_us <= 0:
            return

        if is_emg:
            self._latest_emg_ts_us = ts_us if self._latest_emg_ts_us is None else max(self._latest_emg_ts_us, ts_us)
        else:
            self._latest_acc_ts_us = ts_us if self._latest_acc_ts_us is None else max(self._latest_acc_ts_us, ts_us)

        if not self._clock_started:
            if self._first_ts_us is None:
                self._first_ts_us = ts_us
            else:
                self._first_ts_us = min(self._first_ts_us, ts_us)

    def _get_watermark_us(self) -> Optional[int]:
        wm = None
        if self._latest_emg_ts_us is not None:
            wm = self._latest_emg_ts_us
        if self._latest_acc_ts_us is not None:
            wm = self._latest_acc_ts_us if wm is None else max(wm, self._latest_acc_ts_us)
        return wm

    def _drain_emg(self, queue: asyncio.Queue, buf: Deque[sensor_pb2.EmgSample]) -> int:
        drained = 0
        while True:
            try:
                s: sensor_pb2.EmgSample = queue.get_nowait()
            except asyncio.QueueEmpty:
                return drained

            drained += 1
            buf.append(s)

            self._emg_in_idx += 1
            self._mark_wall("EMG_Feature_Fusion_in", self._emg_in_idx)

            self._update_clock_state(int(getattr(s, "ts_us", 0)), is_emg=True)

    def _drain_acc(self, queue: asyncio.Queue, buf: Deque[sensor_pb2.AccelSample]) -> int:
        drained = 0
        while True:
            try:
                s: sensor_pb2.AccelSample = queue.get_nowait()
            except asyncio.QueueEmpty:
                return drained

            drained += 1
            buf.append(s)

            self._acc_in_idx += 1
            self._mark_wall("Fusion_Feature_Accel_ENTRY", self._acc_in_idx)

            self._update_clock_state(int(getattr(s, "ts_us", 0)), is_emg=False)

    async def _run(self) -> None:
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        emg_buf: Deque[sensor_pb2.EmgSample] = deque()
        acc_buf: Deque[sensor_pb2.AccelSample] = deque()

        next_tick_us: Optional[int] = None

        try:
            while True:
                drained_emg = self._drain_emg(emg_q, emg_buf)
                drained_acc = self._drain_acc(acc_q, acc_buf)

                if next_tick_us is None and self._first_ts_us is not None:
                    base_tick = int(self._first_ts_us) + self._max_delay_us
                    next_tick_us = ((base_tick + self._step_us - 1) // self._step_us) * self._step_us
                    self._clock_started = True

                if next_tick_us is None:
                    await asyncio.sleep(0.01)
                    continue

                watermark_us = self._get_watermark_us()
                if watermark_us is None:
                    await asyncio.sleep(0.01)
                    continue

                allowed_tick_us = int(watermark_us) + self._max_delay_us

                steps = 0
                progressed = False

                while next_tick_us <= allowed_tick_us and steps < self._max_steps_per_loop:
                    win_end_us = int(next_tick_us) - self._max_delay_us
                    win_start_us = win_end_us - self._window_us

                    # move samples into windows (<= win_end_us)
                    if emg_buf:
                        ready = self._pop_ready_by_ts_us(emg_buf, win_end_us)
                        for s in ready:
                            self._emg_rms_win.add_sample(int(s.ts_us), float(s.value))

                    if acc_buf:
                        ready = self._pop_ready_by_ts_us(acc_buf, win_end_us)
                        for s in ready:
                            self._acc_mag_win.add_sample(int(s.ts_us), float(abs(s.mag)))

                    # slide window
                    self._emg_rms_win.drop_older_than(win_start_us)
                    self._acc_mag_win.drop_older_than(win_start_us)

                    # ----- dummy-analog ENTER/EXIT -----
                    seq_next = self._seq + 1
                    self._mark_wall("Fusion_Feature_ENTER", seq_next)

                    t0 = time.perf_counter_ns()
                    fused = self._compute_fused_sample(ts_us=win_end_us)
                    t1 = time.perf_counter_ns()

                    # DeadlineMeter: live effective rate (algo-only compute)
                    self._algo_meter.tick(t0, t1, produced_count=1)

                    self._mark_ts("Fusion_Feature_OUT_TS", int(fused.seq), int(fused.ts_us))
                    self._mark_wall("Fusion_Feature_Exit", seq_next)

                    if not MEASURE_ONLY:
                        p0 = time.perf_counter_ns()
                        await self._fusion_b.publish(fused)
                        p1 = time.perf_counter_ns()
                        self._pub_meter.tick(p0, p1, produced_count=1)
                        self._mark_wall("Fusion_Feature_Publish_Exit", int(fused.seq))

                    next_tick_us += self._step_us
                    steps += 1
                    progressed = True

                if progressed:
                    await asyncio.sleep(0)
                else:
                    if drained_emg == 0 and drained_acc == 0:
                        await asyncio.sleep(0.005)
                    else:
                        await asyncio.sleep(0.001)

        finally:
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)

    def _compute_fused_sample(self, *, ts_us: int) -> fusion_pb2.FusedSample:
        emg_rms, emg_count = self._emg_rms_win.get_rms()
        move_level, move_var, acc_count = self._acc_mag_win.get_stats()

        emg_has_enough = emg_count >= self._min_emg_samples
        acc_has_enough = acc_count >= self._min_acc_samples

        # EMG: compute or hold
        if emg_has_enough:
            emg_level = float(emg_rms) / 1000.0
            self._last_emg_level = emg_level
            self._last_emg_ok_ts_us = int(ts_us)
            emg_valid = True
        else:
            can_hold = self._last_emg_ok_ts_us is not None
            if can_hold and self._hold_timeout_us > 0:
                can_hold = (int(ts_us) - int(self._last_emg_ok_ts_us)) <= self._hold_timeout_us
            if can_hold:
                emg_level = float(self._last_emg_level)
                emg_valid = True
            else:
                emg_level = 0.0
                emg_valid = False

        # ACC: compute or hold
        if acc_has_enough:
            move_level = float(move_level)
            move_var = float(move_var)
            self._last_move_level = move_level
            self._last_move_var = move_var
            self._last_acc_ok_ts_us = int(ts_us)
            acc_valid = True
        else:
            can_hold = self._last_acc_ok_ts_us is not None
            if can_hold and self._hold_timeout_us > 0:
                can_hold = (int(ts_us) - int(self._last_acc_ok_ts_us)) <= self._hold_timeout_us
            if can_hold:
                move_level = float(self._last_move_level)
                move_var = float(self._last_move_var)
                acc_valid = True
            else:
                move_level = 0.0
                move_var = 0.0
                acc_valid = False

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


# Drop-in alias, falls der Server "FusionManager" importiert
FusionManager = FusionManagerFeatureTest
