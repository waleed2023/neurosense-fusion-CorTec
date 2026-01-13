# emg/fusion/fusion_manager.py

import asyncio
import time
from typing import Optional, List

from emg.fusion.rms_mag import OnlineRMSWindow, OnlineMagWindow
from emg.broadcaster import Broadcaster
from emg.measurment.timing_mark_logger import TimingMarkLogger
from emg.proto import emg_pb2, fusion_pb2
from emg.config import (
    SENSOR_METRICS_LOG_FILE,
    WINDOW_MS,
    STEP_MS,
    MAX_DELAY_MS,
    now_ns,
    ns_to_us,
)


def now_ms() -> int:
    """Wall clock in milliseconds since epoch."""
    return int(time.time() * 1000)


class FusionManager:
    """Compute fused samples from EMG + ACC using sliding windows."""

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
    ):
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster

        self._window_ms = int(window_ms)
        self._step_ms = int(step_ms)
        self._max_delay_ms = int(max_delay_ms)
        self._min_emg_samples = int(min_emg_samples)
        self._min_acc_samples = int(min_acc_samples)

        self._task: Optional[asyncio.Task] = None
        self._seq: int = 0

        # Debug counters # not used yet
        self._debug_emg_read: int = 0
        self._debug_acc_read: int = 0

        # Online windows for features
        self._emg_rms_win = OnlineRMSWindow(self._window_ms)
        self._acc_mag_win = OnlineMagWindow(self._window_ms)

        # Sample & hold state
        self._last_emg_level: float = 0.0
        self._has_emg_level: bool = False
        self._last_move_level: float = 0.0
        self._last_move_var: float = 0.0
        self._has_acc_level: bool = False

        # Timing marks (for jitter/latency analysis)
        self._marklog = TimingMarkLogger(log_file=SENSOR_METRICS_LOG_FILE)
        self._BLE_fusion_idx = 0
        self._ACC_fusion_idx = 0
        self.Fusion_idx = 0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Debug properties
    # ------------------------------------------------------------------ #

    @property
    def debug_emg_read(self) -> int:
        return self._debug_emg_read

    @property
    def debug_acc_read(self) -> int:
        return self._debug_acc_read

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _mark(self, point: str, idx: int) -> None:
        """Write timing point with a timestamp in µs."""
        self._marklog.mark(point, idx, ns_to_us(now_ns()))

    @staticmethod
    def _partition_by_ts(buf: List, ts_end: int):
        """Split buffer into (<= ts_end) and (> ts_end), preserving order."""
        ready, rest = [], []
        for s in buf:
            (ready if s.ts_ms <= ts_end else rest).append(s)
        return ready, rest

    async def _drain_emg(self, q: asyncio.Queue, buf: List[emg_pb2.EmgSample]) -> None:
        while True:
            try:
                s = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._BLE_fusion_idx += 1
            self._mark("BLEManager_Fusion_in", self._BLE_fusion_idx)
            self._debug_emg_read += 1
            buf.append(s)

    async def _drain_acc(self, q: asyncio.Queue, buf: List[emg_pb2.AccelSample]) -> None:
        while True:
            try:
                s = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._ACC_fusion_idx += 1
            self._mark("ACCManager_Fusion_in", self._ACC_fusion_idx)
            self._debug_acc_read += 1
            buf.append(s)

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    async def _run(self) -> None:
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        # Raw reorder buffers (not yet pushed into the online windows)
        emg_buf: List[emg_pb2.EmgSample] = []
        acc_buf: List[emg_pb2.AccelSample] = []

        next_tick_ms = now_ms()

        try:
            while True:
                # 1) Drain queues (non-blocking)
                await self._drain_emg(emg_q, emg_buf)
                await self._drain_acc(acc_q, acc_buf)

                # 2) Catch up fusion clock (step-based)
                now = now_ms()
                steps = 0
                max_steps = 5000

                while next_tick_ms <= now and steps < max_steps:
                    # Delay buffer: evaluate slightly in the past to absorb jitter
                    win_end = max(0, next_tick_ms - self._max_delay_ms)
                    win_start = max(0, win_end - self._window_ms)

                    # EMG -> RMS window
                    if emg_buf:
                        ready, emg_buf = self._partition_by_ts(emg_buf, win_end)
                        for s in ready:
                            self._emg_rms_win.add_sample(s.ts_ms, float(s.value))

                    # ACC -> magnitude window
                    if acc_buf:
                        ready, acc_buf = self._partition_by_ts(acc_buf, win_end)
                        for s in ready:
                            self._acc_mag_win.add_sample(s.ts_ms, float(abs(s.mag)))

                    # Slide windows (drop old)
                    self._emg_rms_win.drop_older_than(win_start)
                    self._acc_mag_win.drop_older_than(win_start)

                    # 3) Compute + publish fused sample
                    fused = self._compute_fused_sample(ts_ms=win_end)
                    if fused is not None:
                        await self._fusion_b.publish(fused)
                        self.Fusion_idx += 1
                        self._mark("FusionManager_out", self.Fusion_idx)

                    next_tick_ms += self._step_ms
                    steps += 1

                # 4) Sleep until next tick (cap keeps responsiveness)
                now2 = now_ms()
                sleep_s = max(0.0, (next_tick_ms - now2) / 1000.0)
                await asyncio.sleep(min(sleep_s, 0.01))
        finally:
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)

    # ------------------------------------------------------------------ #
    # Fusion logic
    # ------------------------------------------------------------------ #

    def _compute_fused_sample(self, *, ts_ms: int) -> Optional[fusion_pb2.FusedSample]:
        """
        Compute window-based EMG + ACC features and pack them into a FusedSample.
        - The features are computed over the current sliding window:
            [ts_ms - window_ms, ts_ms]
        (after applying the max_delay_ms shift in the main loop).

        - Output fields (what each value means):
            * ts_ms:
                Fusion timestamp (ms). Represents the end time of the evaluated window.
            * seq:
                Monotonic sequence number generated by the fusion manager (helps detect drops / order).
            * emg_level:
                EMG feature value (here: RMS over the window, scaled in the code, e.g., /1000).
            * emg_valid:
                True if EMG is considered valid for this output (enough samples in window or sample-and-hold used).
            * emg_count:
                Number of EMG samples that contributed to the RMS in the current window.
            * move_level:
                Motion feature value (mean of |mag| over the window).
            * move_var:
                Motion variability (variance of |mag| over the window; population variance E[x^2] - (E[x])^2).
            * acc_valid:
                True if ACC is considered valid for this output (enough samples in window or sample-and-hold used).
            * acc_count:
                Number of ACC samples that contributed to mean/variance in the current window.

        - Returns None if we do not have a valid value for both EMG and ACC yet
        (startup buffers may be empty; later we use sample-and-hold).
        """

        emg_rms, emg_count = self._emg_rms_win.get_rms()
        move_level, move_var, acc_count = self._acc_mag_win.get_stats()

        emg_has_new = emg_count >= self._min_emg_samples
        acc_has_new = acc_count >= self._min_acc_samples

        # EMG: sample & hold
        if emg_has_new:
            emg_level = emg_rms / 1000.0
            self._last_emg_level = emg_level
            self._has_emg_level = True
            emg_valid = True
        else:
            if self._has_emg_level:
                emg_level = self._last_emg_level
                emg_valid = True
            else:
                emg_level = 0.0
                emg_valid = False

        # ACC: sample & hold
        if acc_has_new:
            self._last_move_level = move_level
            self._last_move_var = move_var
            self._has_acc_level = True
            acc_valid = True
        else:
            if self._has_acc_level:
                move_level = self._last_move_level
                move_var = self._last_move_var
                acc_valid = True
            else:
                move_level = 0.0
                move_var = 0.0
                acc_valid = False

        if not emg_valid or not acc_valid:
            return None

        self._seq += 1
        return fusion_pb2.FusedSample(
            ts_ms=ts_ms,
            seq=self._seq,
            emg_level=emg_level,
            emg_valid=emg_valid,
            emg_count=emg_count,
            move_level=move_level,
            move_var=move_var,
            acc_valid=acc_valid,
            acc_count=acc_count,
        )
