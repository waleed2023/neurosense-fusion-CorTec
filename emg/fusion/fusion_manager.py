# emg/fusion/fusion_manager.py
import asyncio
import time
import sys
import math
from collections import deque
from typing import Optional, List

from emg.broadcaster import Broadcaster
from emg.proto import emg_pb2, fusion_pb2
from emg.config import LOG_PREFIX, MAX_DELAY_MS, STEP_MS, WINDOW_MS

def now_ms() -> int:
    # Common wall-clock in ms, same as used in EMG/Accel managers.
    return int(time.time() * 1000)


class OnlineRMSWindow:
    """Online RMS over a sliding time window [ts_min, +inf)."""

    def __init__(self, window_ms: int):
        self.window_ms = window_ms
        # deque of (ts_ms, value)
        self.samples: deque[tuple[int, float]] = deque()
        self.sum_sq: float = 0.0

    def add_sample(self, ts_ms: int, value: float) -> None:
        """Add new EMG sample to the window."""
        self.samples.append((ts_ms, value))
        self.sum_sq += value * value

    def drop_older_than(self, ts_min: int) -> None:
        """Drop all samples with timestamp < ts_min."""
        while self.samples and self.samples[0][0] < ts_min:
            _, v_old = self.samples.popleft()
            self.sum_sq -= v_old * v_old

    def get_rms(self) -> tuple[float, int]:
        """Return (RMS, count) for current samples in window."""
        n = len(self.samples)
        if n == 0:
            return 0.0, 0
        mean_sq = self.sum_sq / float(n)
        return math.sqrt(mean_sq), n


class OnlineMagWindow:
    """
    Online stats for |mag| in a sliding time window [ts_min, +inf).

    We keep:
      - sum_mag     = Σ |mag|
      - sum_mag_sq  = Σ |mag|^2
    so that:
      - mean = sum_mag / N
      - var  = sum_mag_sq / N - mean^2
    """

    def __init__(self, window_ms: int):
        self.window_ms = window_ms
        # deque of (ts_ms, mag_abs)
        self.samples: deque[tuple[int, float]] = deque()
        self.sum_mag: float = 0.0
        self.sum_mag_sq: float = 0.0

    def add_sample(self, ts_ms: int, mag_abs: float) -> None:
        self.samples.append((ts_ms, mag_abs))
        self.sum_mag += mag_abs
        self.sum_mag_sq += mag_abs * mag_abs

    def drop_older_than(self, ts_min: int) -> None:
        while self.samples and self.samples[0][0] < ts_min:
            _, m_old = self.samples.popleft()
            self.sum_mag -= m_old
            self.sum_mag_sq -= m_old * m_old

    def get_stats(self) -> tuple[float, float, int]:
        """Return (mean, variance, count) of |mag| in the window."""
        n = len(self.samples)
        if n == 0:
            return 0.0, 0.0, 0

        mean = self.sum_mag / float(n)
        var = self.sum_mag_sq / float(n) - mean * mean
        if var < 0.0:
            var = 0.0  # numerical safety
        return mean, var, n



class FusionManager:
    """
    Collects EMG and accelerometer samples from their broadcasters,
    aggregates them in fixed time windows, computes simple features,
    and publishes FusedSample objects into fusion_broadcaster.
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
    ):
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster

        self._window_ms = window_ms
        self._max_delay_ms = max_delay_ms
        self._step_ms = step_ms
        self._min_emg = min_emg_samples
        self._min_acc = min_acc_samples

        self._task: Optional[asyncio.Task] = None
        self._seq: int = 0

        # Debug counters for test code (removed later)
        self._debug_emg_read: int = 0
        self._debug_acc_read: int = 0

        # Online windows for features
        self._emg_rms_win = OnlineRMSWindow(window_ms)
        self._acc_mag_win = OnlineMagWindow(window_ms)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fusion-manager")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                #sys.stdout.write(f"{LOG_PREFIX} FusionManager stopped\n")
                pass
            self._task = None
    # debug properties, removed later
    @property
    def debug_emg_read(self) -> int:
        """Number of EMG samples read from the EMG queue."""
        return self._debug_emg_read

    # debug properties, removed later
    @property
    def debug_acc_read(self) -> int:
        """Number of ACC samples read from the ACC queue."""
        return self._debug_acc_read


    async def _run(self) -> None:
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        # Reorder/late buffer: raw samples not yet "consumed" into the windows
        emg_buf: List[emg_pb2.EmgSample] = []
        acc_buf: List[emg_pb2.AccelSample] = []

        last_window_ts = now_ms()

        try:
            while True:
                # 1) Drain new EMG samples into buffer
                try:
                    while True:
                        s = emg_q.get_nowait()
                        self._debug_emg_read += 1  # count EMG samples consumed
                        emg_buf.append(s)
                except asyncio.QueueEmpty:
                    pass

                # 2) Drain new ACC samples into buffer
                try:
                    while True:
                        s = acc_q.get_nowait()
                        self._debug_acc_read += 1  # count ACC samples consumed
                        acc_buf.append(s)
                except asyncio.QueueEmpty:
                    pass

                # 3) Sort buffers by timestamp (out-of-order fix)
                emg_buf.sort(key=lambda s: s.ts_ms)
                acc_buf.sort(key=lambda s: s.ts_ms)

                now = now_ms()
                if (now - last_window_ts) >= self._step_ms:
                    # Evaluate window slightly in the past to tolerate delay
                    effective_end = now - self._max_delay_ms
                    if effective_end < 0:
                        effective_end = 0  # safety in case of time issues

                    win_end = effective_end
                    win_start = win_end - self._window_ms

                    # --- FEED ONLINE WINDOWS WITH "READY" SAMPLES (ts <= win_end) ---

                    # EMG: move all samples with ts <= win_end from emg_buf into RMS window
                    ready_emg: List[emg_pb2.EmgSample] = [
                        s for s in emg_buf if s.ts_ms <= win_end
                    ]
                    emg_buf = [s for s in emg_buf if s.ts_ms > win_end]

                    for s in ready_emg:
                        self._emg_rms_win.add_sample(s.ts_ms, float(s.value))

                    # ACC: same idea for magnitude stats
                    ready_acc: List[emg_pb2.AccelSample] = [
                        s for s in acc_buf if s.ts_ms <= win_end
                    ]
                    acc_buf = [s for s in acc_buf if s.ts_ms > win_end]

                    for s in ready_acc:
                        mag_abs = float(abs(s.mag))
                        self._acc_mag_win.add_sample(s.ts_ms, mag_abs)

                    # --- SLIDE WINDOW START FOR ONLINE STATS ---
                    self._emg_rms_win.drop_older_than(win_start)
                    self._acc_mag_win.drop_older_than(win_start)

                    # --- COMPUTE FUSED SAMPLE FROM ONLINE WINDOWS ---
                    fused = self._compute_fused_sample(ts_ms=win_end)
                    if fused is not None:
                        await self._fusion_b.publish(fused)

                    last_window_ts = now

                # Avoid busy-loop
                await asyncio.sleep(0.01)
        finally:
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)


    def _compute_fused_sample(
        self,
        *,
        ts_ms: int,
    ) -> Optional[fusion_pb2.FusedSample]:
        """
        Compute window-based features for EMG and ACC using the online windows.

        - EMG:
            * emg_level: RMS of EMG 'value' (0..1000), normalized to ~0..1
            * emg_valid: True if at least min_emg_samples in this window
            * emg_count: number of EMG samples in RMS window

        - ACC:
            * move_level: mean(|mag|)
            * move_var: variance of |mag| (population variance)
            * acc_valid: True if at least min_acc_samples in this window
            * acc_count: number of ACC samples in MAG window

        IMPORTANT:
        - EMG and ACC pipelines are independent.
        - Fusion layer only emits if *both* streams are valid.
        """

        # --- Get stats from online windows ---
        emg_rms, emg_count = self._emg_rms_win.get_rms()
        move_level, move_var, acc_count = self._acc_mag_win.get_stats()

        min_emg = self._min_emg_samples
        min_acc = self._min_acc_samples

        emg_valid = emg_count >= min_emg
        acc_valid = acc_count >= min_acc

        # Fusion depends on both streams being valid
        if not emg_valid or not acc_valid:
            return None

        # ------------- EMG FEATURES -------------
        if emg_valid:
            # Normalize RMS to ~0..1 assuming full scale ~1000
            emg_level = emg_rms / 1000.0
        else:
            emg_level = 0.0

        # ------------- ACC FEATURES -------------
        if acc_valid:
            # move_level: mean(|mag|), move_var: variance
            pass  # values already in move_level / move_var
        else:
            move_level = 0.0
            move_var = 0.0

        # ------------- SEQUENCE + OUTPUT -------------
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

