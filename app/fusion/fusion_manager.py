# emg/fusion/fusion_manager.py
from __future__ import annotations

import asyncio
from collections import deque
from typing import Optional, Deque

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




# ---- Watermark-driven fusion --------------------
#
# We do NOT use "wall-clock time" here. We only trust HAL timestamps (ts_us)
# coming from sensors. The fusion loop runs on a fixed tick grid (STEP),
# but it is allowed to advance only up to what we have actually *seen*.
#
# Definitions:
#   watermark_us  = the newest (largest) HAL timestamp we have received so far
#                   from ANY sensor (EMG or ACC). Think of it as: "how far the
#                   stream has progressed".
#
#   max_delay_us  = safety margin for late packets. We intentionally compute
#                   outputs in the past by this amount, so late samples can still
#                   arrive before we finalize a tick.
#
#   allowed_tick_us = watermark_us + max_delay_us
#       -> we are allowed to generate ticks up to this time on the tick grid.
#
# For each tick (next_tick_us), we compute features for:
#   win_end_us   = next_tick_us - max_delay_us
#   win_start_us = win_end_us - window_us
#
# So every output is computed from data in [win_start_us .. win_end_us]
# and the output timestamp we publish is ts_us = win_end_us.
#
# Why does this work?
#   If a sample is late by <= max_delay, it can still arrive before the tick that
#   "covers" its timestamp is processed.
#
# Concrete example (with my numbers):
#   STEP        = 1 ms
#   WINDOW      = 200 ms
#   MAX_DELAY   = 100 ms
#
# Suppose the newest sample we have received so far has ts_us = 1500 ms:
#   watermark_us = 1500 ms
#   allowed_tick_us = 1500 + 100 = 1600 ms
#
# This means we may process ticks up to next_tick_us = 1600 ms.
#
# What output does tick=1600ms produce?
#   win_end_us   = 1600 - 100 = 1500 ms   <-- equals watermark (safe!)
#   win_start_us = 1500 - 200 = 1300 ms
#   => compute features from samples in [1300 .. 1500] ms
#   => publish fused sample with ts_us = 1500 ms
#
# What if a sample with ts_us=1490ms arrives "late"?
#   - If it arrives before we reach tick=1590ms (win_end=1490ms), it is included.
#   - If it arrives later than the 100ms margin (too late), we have already
#     finalized windows that end at 1490ms, so it won't affect past outputs.
#
# In short:
#   watermark_us controls how far we can safely go.
#   max_delay_us gives late packets time to arrive.
#   window_us defines how much history we use per output.
# ------------------------------------------------------------------------------


class FusionManager:
    """
    Fusion Algorithm Manager with Sliding Windows and fixed steps.
    Fusion clock is driven by HAL timestamps (ts_us) only.

    Key behavior:
    - Starts as soon as *one* sensor delivers samples.
    - Drives output with a fixed tick grid (STEP_MS).
    - Uses a sliding time window (WINDOW_MS) to compute features.
    - If a sensor has too few samples in the current window:
        -> hold the last valid feature value (feature-level sample-and-hold).
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
    ):
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster

        self._window_us = int(window_ms) * 1000 # we need to work in microseconds because HAL timestamps are in µs
        self._step_us = max(1, int(step_ms) * 1000)
        self._max_delay_us = max(0, int(max_delay_ms) * 1000)

        self._min_emg_samples = int(min_emg_samples)
        self._min_acc_samples = int(min_acc_samples)
        self._max_steps_per_loop = int(max_steps_per_loop)

        self._task: Optional[asyncio.Task] = None
        self._seq: int = 0

        # Debug counters: how many samples we drained from each sensor
        self._debug_emg_read: int = 0
        self._debug_acc_read: int = 0

        # Online windows (all timestamps in microseconds)
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
        self._clock_started: bool = False

        # Hold-last-output (feature-level sample-and-hold)
        # If the current sliding window has too few samples, keep the last valid feature value.
        # hold_timeout_ms = 0 -> hold forever
        self._hold_timeout_us = max(0, int(hold_timeout_ms)) * 1000

        self._last_emg_level: float = 0.0
        self._last_emg_ok_ts_us: Optional[int] = None

        self._last_move_level: float = 0.0
        self._last_move_var: float = 0.0
        self._last_acc_ok_ts_us: Optional[int] = None

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
    # not used for now
    def debug_emg_read(self) -> int:
        return self._debug_emg_read

    @property
    # not used for now
    def debug_acc_read(self) -> int:
        return self._debug_acc_read

    # for timestamping
    def _mark_wall(self, point: str, idx: int) -> None:
        self._marklog.mark(point, idx, ns_to_us(now_ns()))

    def _mark_ts(self, point: str, idx: int, ts_us: int) -> None:
        self._marklog.mark(point, idx, int(ts_us))

    @staticmethod
    def _pop_ready_by_ts_us(buf: Deque, ts_end_us: int) -> list:
        """
        works as filter for all samples with ts_us <= ts_end_us.
        Efficiently pop samples from the left while ts_us <= ts_end_us.
        Assumes samples arrive late in timestamp order.
         example : buf = [s1(ts=100), s2(ts=150), s3(ts=200)], ts_end_us=150
         returns [s1, s2], buf becomes [s3]
        """
        ready = []
        while buf and int(getattr(buf[0], "ts_us", 0)) <= ts_end_us:
            ready.append(buf.popleft())
        return ready

    def _update_clock_state(self, ts_us: int, *, is_emg: bool) -> None:
        """
        Track:
        - first_ts_us: earliest timestamp seen (only needed until clock starts)
        - latest_emg_ts_us / latest_acc_ts_us: watermark sources
        """
        if ts_us <= 0: # invalid timestamp
            return

        if is_emg:
            self._latest_emg_ts_us = ts_us if self._latest_emg_ts_us is None else max(self._latest_emg_ts_us, ts_us)
        else:
            self._latest_acc_ts_us = ts_us if self._latest_acc_ts_us is None else max(self._latest_acc_ts_us, ts_us)

        # Only track the earliest timestamp until the fusion clock is started.
        if not self._clock_started:
            if self._first_ts_us is None:
                self._first_ts_us = ts_us
            else:
                self._first_ts_us = min(self._first_ts_us, ts_us)

    async def _collect_emg(self, queue: asyncio.Queue, buf: Deque[sensor_pb2.EmgSample]) -> int:
        """collect all available samples from EMG queue into buf."""
        drained = 0
        while True:
            try:
                sample: sensor_pb2.EmgSample = queue.get_nowait()
            except asyncio.QueueEmpty:
                return drained

            drained += 1
            self._BLE_fusion_idx += 1
            self._mark_wall("EMG_Feature_Fusion_in", self._BLE_fusion_idx)

            self._debug_emg_read += 1
            buf.append(sample)

            ts = int(getattr(sample, "ts_us", 0))
            self._update_clock_state(ts, is_emg=True)

    async def _collect_acc(self, queue: asyncio.Queue, buf: Deque[sensor_pb2.AccelSample]) -> int:
        """collect all available samples from ACC queue into buf."""
        drained = 0
        while True:
            try:
                sample: sensor_pb2.AccelSample = queue.get_nowait()
            except asyncio.QueueEmpty:
                return drained

            drained += 1
            self._ACC_fusion_idx += 1
            self._mark_wall("Fusion_Feature_Accel_ENTRY", self._ACC_fusion_idx)

            self._debug_acc_read += 1
            buf.append(sample)

            ts = int(getattr(sample, "ts_us", 0))
            self._update_clock_state(ts, is_emg=False)

    def _get_watermark_us(self) -> Optional[int]:
        """Watermark = newest HAL timestamp we have seen (from either sensor)."""
        wm = None
        if self._latest_emg_ts_us is not None:
            wm = self._latest_emg_ts_us
        if self._latest_acc_ts_us is not None:
            wm = self._latest_acc_ts_us if wm is None else max(wm, self._latest_acc_ts_us)
        return wm

    async def _run(self) -> None:
        # Subscribe to EMG and ACC broadcasters
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        # Buffers for incoming samples (deque for O(1) pops from the left)
        emg_buf: Deque[sensor_pb2.EmgSample] = deque()
        acc_buf: Deque[sensor_pb2.AccelSample] = deque()

        # next_tick_us is the timestamp of the next fusion step (aligned to step grid)
        next_tick_us: Optional[int] = None

        try:
            while True:
                # 1) drain queues
                drained_emg = await self._collect_emg(emg_q, emg_buf)
                drained_acc = await self._collect_acc(acc_q, acc_buf)

                # 2) start fusion clock once the first timestamp exists
                if next_tick_us is None and self._first_ts_us is not None:
                    base_tick = int(self._first_ts_us) + self._max_delay_us
                    # ceil-align to step grid (next multiple of step_us)
                    # example: first_ts_us=1050, step_us=200 -> next_tick_us=1200
                    next_tick_us = ((base_tick + self._step_us - 1) // self._step_us) * self._step_us
                    self._clock_started = True

                # 3) if still no sensor data -> idle
                if next_tick_us is None:
                    await asyncio.sleep(0.01)
                    continue

                # 4) drive fusion by watermark time (latest HAL time seen)
                watermark_us = self._get_watermark_us()
                if watermark_us is None:
                    await asyncio.sleep(0.01)
                    continue

                allowed_tick_us = int(watermark_us) + self._max_delay_us

                # 5) process ticks up to allowed_tick_us
                steps = 0
                progressed = False

                while next_tick_us <= allowed_tick_us and steps < self._max_steps_per_loop:
                    win_end_us = int(next_tick_us) - self._max_delay_us
                    win_start_us = win_end_us - self._window_us

                    # EMG: move all samples with ts_us <= win_end_us into the RMS window
                    if emg_buf:
                        ready = self._pop_ready_by_ts_us(emg_buf, win_end_us)
                        for s in ready:
                            self._emg_rms_win.add_sample(int(s.ts_us), float(s.value))

                    # ACC: move all samples with ts_us <= win_end_us into the magnitude window
                    if acc_buf:
                        ready = self._pop_ready_by_ts_us(acc_buf, win_end_us)
                        for s in ready:
                            self._acc_mag_win.add_sample(int(s.ts_us), float(abs(s.mag)))

                    # Slide windows: drop samples older than win_start_us
                    self._emg_rms_win.drop_older_than(win_start_us)
                    self._acc_mag_win.drop_older_than(win_start_us)

                    # Compute fused sample and publish
                    fused = self._compute_fused_sample(ts_us=win_end_us)
                    await self._fusion_b.publish(fused)
                    # timestampe for both 
                    self.Fusion_idx += 1
                    #self._mark_ts("Fusion_Feature_Algo_Time_Exit", self.Fusion_idx, int(fused.ts_us))          # Algorithmus-timestamp, prof that my output 1kHz is correct
                    self._mark_wall("Fusion_Feature_Exit", self.Fusion_idx)                       # After feature computation (wall-time), real cpu/os time after publishing 

                    next_tick_us += self._step_us
                    steps += 1
                    progressed = True

                # If we hit the step limit, we are lagging behind (backpressure protection).
                # You may want to log this as a warning in production.
                # if steps >= self._max_steps_per_loop and next_tick_us <= allowed_tick_us:
                #     print("[fusion] Warning: max_steps_per_loop reached, fusion is catching up slowly")

                # 6) adaptive sleep:
                # - if we progressed, yield quickly (keep latency low)
                # - if nothing happened, sleep a bit longer to reduce CPU load
                if progressed:
                    await asyncio.sleep(0)
                else:
                    # If no new data arrived AND we couldn't progress ticks, wait a bit
                    if drained_emg == 0 and drained_acc == 0:
                        await asyncio.sleep(0.005)
                    else:
                        await asyncio.sleep(0.001)

        finally:
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)

    def _compute_fused_sample(self, *, ts_us: int) -> fusion_pb2.FusedSample:
        """
        Compute features from the current sliding windows.
        If a window has too few samples, hold the last valid feature value (sample-and-hold).
        """
        emg_rms, emg_count = self._emg_rms_win.get_rms()
        move_level, move_var, acc_count = self._acc_mag_win.get_stats()

        # Determine whether the current window has enough samples
        emg_has_enough = emg_count >= self._min_emg_samples
        acc_has_enough = acc_count >= self._min_acc_samples

        # ----- EMG: compute or hold -----
        if emg_has_enough:
            emg_level = float(emg_rms) / 1000.0
            self._last_emg_level = emg_level
            self._last_emg_ok_ts_us = int(ts_us)
            emg_valid = True
        else:
            # Hold last valid value if available (and not stale if timeout enabled)
            can_hold = self._last_emg_ok_ts_us is not None
            if can_hold and self._hold_timeout_us > 0:
                can_hold = (int(ts_us) - int(self._last_emg_ok_ts_us)) <= self._hold_timeout_us

            if can_hold:
                emg_level = float(self._last_emg_level)
                emg_valid = True  # held-but-valid
            else:
                emg_level = 0.0
                emg_valid = False

        # ----- ACC: compute or hold -----
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
                acc_valid = True  # held-but-valid
            else:
                move_level = 0.0
                move_var = 0.0
                acc_valid = False

        # Note on counts:
        # - emg_count / acc_count always reflect the *current window*.
        # - If valid==True but count==0, the value was held (no fresh samples in the window).

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
