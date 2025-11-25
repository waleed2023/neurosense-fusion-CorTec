from collections import deque
from typing import Optional, List
import math
import time


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

