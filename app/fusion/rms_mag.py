from collections import deque
import math


class OnlineRMSWindow:
    """Online RMS over a sliding time window, using timestamps in microseconds (ts_us)."""

    def __init__(self, window_us: int):
        self.window_us = int(window_us)
        self.samples: deque[tuple[int, float]] = deque()  # (ts_us, value)
        self.sum_sq: float = 0.0

    def add_sample(self, ts_us: int, value: float) -> None:
        self.samples.append((int(ts_us), float(value)))
        self.sum_sq += float(value) * float(value)

    def drop_older_than(self, ts_min_us: int) -> None:
        ts_min_us = int(ts_min_us)
        while self.samples and self.samples[0][0] < ts_min_us:
            _, v_old = self.samples.popleft()
            self.sum_sq -= v_old * v_old

    def get_rms(self) -> tuple[float, int]:
        n = len(self.samples)
        if n == 0:
            return 0.0, 0
        mean_sq = self.sum_sq / float(n)
        return math.sqrt(mean_sq), n


class OnlineMagWindow:
    """
    Online stats for |mag| in a sliding time window, using timestamps in microseconds (ts_us).

    Keeps:
      - sum_mag     = Σ |mag|
      - sum_mag_sq  = Σ |mag|^2
    """

    def __init__(self, window_us: int):
        self.window_us = int(window_us)
        self.samples: deque[tuple[int, float]] = deque()  # (ts_us, |mag|)
        self.sum_mag: float = 0.0
        self.sum_mag_sq: float = 0.0

    def add_sample(self, ts_us: int, mag_abs: float) -> None:
        m = float(mag_abs)
        self.samples.append((int(ts_us), m))
        self.sum_mag += m
        self.sum_mag_sq += m * m

    def drop_older_than(self, ts_min_us: int) -> None:
        ts_min_us = int(ts_min_us)
        while self.samples and self.samples[0][0] < ts_min_us:
            _, m_old = self.samples.popleft()
            self.sum_mag -= m_old
            self.sum_mag_sq -= m_old * m_old

    def get_stats(self) -> tuple[float, float, int]:
        n = len(self.samples)
        if n == 0:
            return 0.0, 0.0, 0

        mean = self.sum_mag / float(n)
        var = self.sum_mag_sq / float(n) - mean * mean
        if var < 0.0:
            var = 0.0
        return mean, var, n
