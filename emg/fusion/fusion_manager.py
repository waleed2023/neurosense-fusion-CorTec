# emg/fusion/fusion_manager.py

import asyncio
import time
from typing import Optional, List, Tuple
from emg.measurment.jitter_tracker import JitterTracker

from emg.fusion.rms_mag import OnlineRMSWindow, OnlineMagWindow
from emg.broadcaster import Broadcaster
from emg.proto import emg_pb2, fusion_pb2
from emg.measurment.delay_tracker import DelayTracker
from emg.measurment.jitter_tracker import JitterTracker
from emg.config import WINDOW_MS, STEP_MS, MAX_DELAY_MS, MAX_RATE_HZ_EMG, MAX_RATE_HZ_ACC, MAX_RATE_HZ_FUSION, LOG_PREFIX


def now_ms() -> int:
    """Common wall-clock in ms, same as used in EMG/Accel managers."""
    return int(time.time() * 1000)


class FusionManager:
    def __init__(
        self,
        emg_broadcaster: Broadcaster,
        acc_broadcaster: Broadcaster,
        fusion_broadcaster: Broadcaster,
        *,
        window_ms: int = WINDOW_MS,
        step_ms: int = STEP_MS,          # Fusion-Clock step size
        max_delay_ms: int = MAX_DELAY_MS,
        min_emg_samples: int = 1,
        min_acc_samples: int = 1,
    ):
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster

        self._window_ms = window_ms
        self._max_delay_ms = max_delay_ms
        self._step_ms = step_ms

        # Minimum number of samples per window
        self._min_emg_samples = min_emg_samples
        self._min_acc_samples = min_acc_samples

        self._task: Optional[asyncio.Task] = None # Fusion task
        self._seq: int = 0

        # Debug counters
        self._debug_emg_read: int = 0
        self._debug_acc_read: int = 0

        # Online windows for features
        self._emg_rms_win = OnlineRMSWindow(window_ms)
        self._acc_mag_win = OnlineMagWindow(window_ms)

        # --- Sample & hold state ---
        self._last_emg_level: float = 0.0
        self._has_emg_level: bool = False

        self._last_move_level: float = 0.0
        self._last_move_var: float = 0.0
        self._has_acc_level: bool = False

        # ---jitter trackers---
        emg_nominal_ms = 1000.0 / float(MAX_RATE_HZ_EMG)   # z.B. 100 ms
        acc_nominal_ms = 1000.0 / float(MAX_RATE_HZ_ACC)   # z.B. ~1.25 ms bei 800 Hz

        self._emg_jitter_at_fusion = JitterTracker(
            nominal_period_ms=emg_nominal_ms,
            name="EMG@FUSION",
        )
        self._acc_jitter_at_fusion = JitterTracker(
            nominal_period_ms=acc_nominal_ms,
            name="ACC@FUSION",
        )
        # --- letzte Sensor-Eingangszeiten für dieses Fenster ---
        self._last_emg_input_ts: Optional[int] = None
        self._last_acc_input_ts: Optional[int] = None

        # --- Pipeline-Delay & -Jitter: Sensor-Eingang -> fertig fusioniert ---
        fusion_nominal_ms = 1000.0 / float(MAX_RATE_HZ_FUSION)

        self._emg_to_fusion_delay = DelayTracker(
            name="EMG_TO_FUSION_DELAY",
        )
        self._emg_to_fusion_jitter = JitterTracker(
            nominal_period_ms=fusion_nominal_ms,
            name="EMG_TO_FUSION_JITTER",
        )

        self._acc_to_fusion_delay = DelayTracker(
            name="ACC_TO_FUSION_DELAY",
        )
        self._acc_to_fusion_jitter = JitterTracker(
            nominal_period_ms=fusion_nominal_ms,
            name="ACC_TO_FUSION_JITTER",
        )

    # --------------------------------------------------------------------- #
    # Lifecycle
    # --------------------------------------------------------------------- #

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fusion-manager")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # --------------------------------------------------------------------- #
    # Debug properties
    # --------------------------------------------------------------------- #

    @property
    def debug_emg_read(self) -> int:
        """Number of EMG samples read from the EMG queue."""
        return self._debug_emg_read

    @property
    def debug_acc_read(self) -> int:
        """Number of ACC samples read from the ACC queue."""
        return self._debug_acc_read

    # --------------------------------------------------------------------- #
    # Main loop
    # --------------------------------------------------------------------- #

    async def _run(self) -> None:
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        # Reorder buffer (raw, not yet pushed into online window)
        emg_buf: List[emg_pb2.EmgSample] = []
        acc_buf: List[emg_pb2.AccelSample] = []

        fusion_step_ms = self._step_ms
        # Start fusion tick now (aligned to whole millisecond)
        next_fusion_ts = now_ms()

        try:
            while True:
                # --- 1) Fetch new EMG samples from queue ---
                try:
                    while True:
                        s = emg_q.get_nowait()
                        self._debug_emg_read += 1
                        emg_buf.append(s)
                except asyncio.QueueEmpty:
                    pass

                # --- 2) Fetch new ACC samples from queue ---
                try:
                    while True:
                        s = acc_q.get_nowait()
                        self._debug_acc_read += 1
                        acc_buf.append(s)
                except asyncio.QueueEmpty:
                    pass

                # --- 3) Reorder: sort by timestamp ---
                #emg_buf.sort(key=lambda s: s.ts_ms)
                #acc_buf.sort(key=lambda s: s.ts_ms)

                now = now_ms()

                # Safety: prevent infinite catch-up, protect backlog
                max_steps = 5000
                steps = 0

                # --- 4) Catch up fusion clock: steps in step_ms ---
                while next_fusion_ts <= now and steps < max_steps:
                    # Window end slightly in the past (delay buffer)
                    effective_end = next_fusion_ts - self._max_delay_ms
                    if effective_end < 0:
                        effective_end = 0

                    win_end = effective_end
                    win_start = win_end - self._window_ms

                    # ---- EMG: push all samples with ts <= win_end into RMS window ----
                    if emg_buf:
                        ready_emg = [s for s in emg_buf if s.ts_ms <= win_end]
                        if ready_emg:
                            emg_buf = [s for s in emg_buf if s.ts_ms > win_end]
                            self._last_emg_input_ts = max(s.ts_ms for s in ready_emg) # for jitter measurement
                            for s in ready_emg:
                                self._emg_rms_win.add_sample(s.ts_ms, float(s.value))

                    # ---- ACC: push all samples with ts <= win_end into MAG window ----
                    if acc_buf:
                        ready_acc = [s for s in acc_buf if s.ts_ms <= win_end]
                        if ready_acc:
                            acc_buf = [s for s in acc_buf if s.ts_ms > win_end]
                            self._last_acc_input_ts = max(s.ts_ms for s in ready_acc)# for jitter measurement
                            for s in ready_acc:
                                mag_abs = float(abs(s.mag))
                                self._acc_mag_win.add_sample(s.ts_ms, mag_abs)

                    # ---- Slide window start forward (remove old samples) ----
                    self._emg_rms_win.drop_older_than(win_start)
                    self._acc_mag_win.drop_older_than(win_start)

                    # ---- Compute FusedSample from online stats ----
                    fused, emg_has_new, acc_has_new = self._compute_fused_sample(ts_ms=win_end)
                    if fused is not None:
                        # Zeitpunkt, wann dieses FusedSample wirklich fertig ist
                        fusion_done_ts = now_ms()

                        # ----- EMG → Fusion-Output: Delay + Jitter -----
                        if emg_has_new and self._last_emg_input_ts is not None:
                            age_emg_ms = fusion_done_ts - self._last_emg_input_ts
                            self._emg_to_fusion_delay.add(age_emg_ms, ts_ms=fusion_done_ts)
                            self._emg_to_fusion_jitter.add(fusion_done_ts)

                        # ----- ACC → Fusion-Output: Delay + Jitter -----
                        if acc_has_new and self._last_acc_input_ts is not None:
                            age_acc_ms = fusion_done_ts - self._last_acc_input_ts
                            self._acc_to_fusion_delay.add(age_acc_ms, ts_ms=fusion_done_ts)
                            self._acc_to_fusion_jitter.add(fusion_done_ts)
                        await self._fusion_b.publish(fused)


                    # Next tick in fusion clock
                    next_fusion_ts += fusion_step_ms
                    steps += 1

                # Small pause to prevent 100% CPU usage
                await asyncio.sleep(0.0005)
        finally:
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)

    # --------------------------------------------------------------------- #
    # Feature computation / Fusion
    # --------------------------------------------------------------------- #

    def _compute_fused_sample(
        self,
        *,
        ts_ms: int,
    ) -> Tuple[Optional[fusion_pb2.FusedSample], bool, bool]:
        """
        Compute window-based features for EMG and ACC using the online windows.

        Sample & hold:
        - If there are not enough *new* samples for this window, reuse the last
          valid level instead of sending 0 (for EMG and ACC separately).
        """

        # --- Get stats from online windows ---
        emg_rms, emg_count = self._emg_rms_win.get_rms()
        move_level, move_var, acc_count = self._acc_mag_win.get_stats()

        min_emg = self._min_emg_samples
        min_acc = self._min_acc_samples

        # Are there enough *fresh* samples in this window?
        emg_has_new = emg_count >= min_emg
        acc_has_new = acc_count >= min_acc

        # ------------- EMG: sample & hold -------------
        if emg_has_new:
            # New window has enough EMG samples -> compute new RMS level
            emg_level = emg_rms / 1000.0  # normalize to ~0..1
            self._last_emg_level = emg_level
            self._has_emg_level = True
            emg_valid = True
        else:
            # Not enough EMG in this window -> sample & hold or invalid
            if self._has_emg_level:
                emg_level = self._last_emg_level
                emg_valid = True
            else:
                emg_level = 0.0
                emg_valid = False

        # ------------- ACC: sample & hold -------------
        if acc_has_new:
            # New ACC stats for this window
            self._last_move_level = move_level
            self._last_move_var = move_var
            self._has_acc_level = True
            acc_valid = True
        else:
            # Not enough ACC samples -> sample & hold or invalid
            if self._has_acc_level:
                move_level = self._last_move_level
                move_var = self._last_move_var
                acc_valid = True
            else:
                move_level = 0.0
                move_var = 0.0
                acc_valid = False

        # ------------- Fusion gating -------------
        if not emg_valid or not acc_valid:
            return None, emg_has_new, acc_has_new

        # ------------- Sequence + output -------------
        self._seq += 1

        fused =  fusion_pb2.FusedSample(
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
        return fused, emg_has_new, acc_has_new
