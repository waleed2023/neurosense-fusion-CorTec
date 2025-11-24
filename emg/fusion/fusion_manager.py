# emg/fusion/fusion_manager.py
import asyncio
import time
import sys
import math
from collections import deque
from typing import Optional, List

from emg.broadcaster import Broadcaster
from emg.proto import emg_pb2, fusion_pb2
from emg.config import LOG_PREFIX, WINDOW_MS, STEP_MS, MAX_DELAY_MS


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
    def __init__(
        self,
        emg_broadcaster: Broadcaster,
        acc_broadcaster: Broadcaster,
        fusion_broadcaster: Broadcaster,
        *,
        window_ms: int = WINDOW_MS,
        step_ms: int = STEP_MS,          # bleibt für Kompatibilität, wird aktuell nicht benutzt
        max_delay_ms: int = MAX_DELAY_MS,
        min_emg_samples: int = 1,
        min_acc_samples: int = 1,
        fusion_step_ms: int = 1,         # 1 ms = 1000 Hz Fusion-Clock
    ):
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster

        self._window_ms = window_ms
        self._max_delay_ms = max_delay_ms
        self._step_ms = step_ms
        self._fusion_step_ms = fusion_step_ms

        # konsistente Namen für die Mindest-Samples
        self._min_emg_samples = min_emg_samples
        self._min_acc_samples = min_acc_samples

        self._task: Optional[asyncio.Task] = None
        self._seq: int = 0

        # Debug counters
        self._debug_emg_read: int = 0
        self._debug_acc_read: int = 0

        # Online-Fenster für Features
        self._emg_rms_win = OnlineRMSWindow(window_ms)
        self._acc_mag_win = OnlineMagWindow(window_ms)

        # --- Sample & hold state ---
        self._last_emg_level: float = 0.0
        self._has_emg_level: bool = False

        self._last_move_level: float = 0.0
        self._last_move_var: float = 0.0
        self._has_acc_level: bool = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fusion-manager")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                # sys.stdout.write(f"{LOG_PREFIX} FusionManager stopped\n")
                pass
            self._task = None

    # debug properties, removed später
    @property
    def debug_emg_read(self) -> int:
        """Number of EMG samples read from the EMG queue."""
        return self._debug_emg_read

    @property
    def debug_acc_read(self) -> int:
        """Number of ACC samples read from the ACC queue."""
        return self._debug_acc_read

    async def _run(self) -> None:
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        # Reorder-Buffer (roh, noch nicht ins Online-Fenster geschoben)
        emg_buf: List[emg_pb2.EmgSample] = []
        acc_buf: List[emg_pb2.AccelSample] = []

        fusion_step_ms = self._fusion_step_ms
        # Starte Fusion-Takt jetzt (auf ganze Millisekunde ausgerichtet)
        next_fusion_ts = now_ms()

        try:
            while True:
                # --- 1) Neue EMG-Samples aus Queue holen ---
                try:
                    while True:
                        s = emg_q.get_nowait()
                        self._debug_emg_read += 1
                        emg_buf.append(s)
                except asyncio.QueueEmpty:
                    pass

                # --- 2) Neue ACC-Samples aus Queue holen ---
                try:
                    while True:
                        s = acc_q.get_nowait()
                        self._debug_acc_read += 1
                        acc_buf.append(s)
                except asyncio.QueueEmpty:
                    pass

                # --- 3) Reorder: nach Timestamp sortieren ---
                emg_buf.sort(key=lambda s: s.ts_ms)
                acc_buf.sort(key=lambda s: s.ts_ms)

                now = now_ms()

                # Sicherheit: nicht unendlich "aufholen"
                max_steps = 5000
                steps = 0

                # --- 4) Fusion-Clock aufholen: 1 ms Schritte ---
                while next_fusion_ts <= now and steps < max_steps:
                    # Fenster-Ende leicht in der Vergangenheit (Delay-Puffer)
                    effective_end = next_fusion_ts - self._max_delay_ms
                    if effective_end < 0:
                        effective_end = 0

                    win_end = effective_end
                    win_start = win_end - self._window_ms

                    # ---- EMG: alle Samples mit ts <= win_end in das RMS-Fenster schieben ----
                    if emg_buf:
                        ready_emg = [s for s in emg_buf if s.ts_ms <= win_end]
                        if ready_emg:
                            emg_buf = [s for s in emg_buf if s.ts_ms > win_end]
                            for s in ready_emg:
                                self._emg_rms_win.add_sample(s.ts_ms, float(s.value))

                    # ---- ACC: alle Samples mit ts <= win_end in das MAG-Fenster schieben ----
                    if acc_buf:
                        ready_acc = [s for s in acc_buf if s.ts_ms <= win_end]
                        if ready_acc:
                            acc_buf = [s for s in acc_buf if s.ts_ms > win_end]
                            for s in ready_acc:
                                mag_abs = float(abs(s.mag))
                                self._acc_mag_win.add_sample(s.ts_ms, mag_abs)

                    # ---- Fenster-Anfang nach vorne schieben (alte Samples raus) ----
                    self._emg_rms_win.drop_older_than(win_start)
                    self._acc_mag_win.drop_older_than(win_start)

                    # ---- FusedSample aus Online-Stats berechnen ----
                    fused = self._compute_fused_sample(ts_ms=win_end)
                    if fused is not None:
                        await self._fusion_b.publish(fused)

                    # 1 ms weiter im Fusion-Clock
                    next_fusion_ts += fusion_step_ms
                    steps += 1

                # Kleine Pause, damit die CPU nicht 100% läuft
                await asyncio.sleep(0.0005)
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
        # Aktuell: nur senden, wenn beide Streams gültig sind (inkl. Hold).
        if not emg_valid or not acc_valid:
            return None

        # ------------- Sequence + output -------------
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
