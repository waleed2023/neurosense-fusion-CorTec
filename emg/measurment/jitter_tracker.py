import sys
from emg.config import LOG_PREFIX
from typing import Optional



class JitterTracker:
    """Track timing jitter statistics for incoming samples."""

    def __init__(self, nominal_period_ms: float, name: Optional[str] = None):
        self.nominal = nominal_period_ms
        self.last_ts: Optional[int] = None
        self.count = 0
        self.sum_dt = 0.0
        self.min_dt = float("inf")
        self.max_dt = 0.0
        self.sum_abs = 0.0
        self.max_abs = 0.0
        self.name = name or "JitterTracker"

    def add(self, ts_ms: int):
        if self.last_ts is None:
            self.last_ts = ts_ms
            return

        dt = ts_ms - self.last_ts
        self.last_ts = ts_ms

        if dt <= 0:
            return  # komische Zeitstempel ignorieren

        # dt-Statistik
        self.count += 1
        self.sum_dt += dt
        if dt < self.min_dt:
            self.min_dt = dt
        if dt > self.max_dt:
            self.max_dt = dt

        # Jitter vs. nominal
        jitter = dt - self.nominal
        j_abs = abs(jitter)
        self.sum_abs += j_abs
        if j_abs > self.max_abs:
            self.max_abs = j_abs

    def stats(self) -> dict:
        """Gibt dt- und Jitter-Statistik zurück."""
        if self.count == 0:
            return {
                "count": 0,
                "mean_dt": 0.0,
                "min_dt": 0.0,
                "max_dt": 0.0,
                "mean_abs_jitter": 0.0,
                "max_abs_jitter": 0.0,
            }

        mean_dt = self.sum_dt / self.count
        mean_abs_jitter = self.sum_abs / self.count
        return {
            "count": self.count,
            "mean_dt": mean_dt,
            "min_dt": self.min_dt,
            "max_dt": self.max_dt,
            "mean_abs_jitter": mean_abs_jitter,
            "max_abs_jitter": self.max_abs,
        }





    def _log_jitter_summary(self) -> None:
        """
        # mean_dt:
        #   Average time difference between two consecutive EMG samples (in ms).
        #   This is effectively the average sampling period as seen by the HAL.
        #
        # min_dt:
        #   Smallest observed interval (in ms). Very small values indicate bursts
        #   where multiple samples arrive almost at the same time.
        #
        # max_dt:
        #   Largest observed interval (in ms). Large values indicate gaps/dropouts
        #   where no new sample arrived for a while.
        #
        # mean|Δt-100ms|:
        #   Average absolute deviation from the ideal 10 Hz grid (100 ms period).
        #   0 ms would mean perfectly regular 10 Hz timing. Large values mean that
        #   most intervals are far away from the ideal 100 ms spacing.
        #
        # max|Δt-100ms|:
        #   Worst-case absolute deviation of a single interval from 100 ms.
        #   Example: Δt = 600 ms -> |600 - 100| = 500 ms.
        #
        # stats["count"]:
        #   Number of intervals (N-1 if N samples were observed).
        """

        stats = self.stats()

        if stats["count"] == 0:
            sys.stdout.write(
                f"{LOG_PREFIX} {self.name} jitter: no intervals, nothing to report\n"
            )
            return

        mean_dt = stats["mean_dt"]
        min_dt = stats["min_dt"]
        max_dt = stats["max_dt"]
        mean_abs = stats["mean_abs_jitter"]
        max_abs = stats["max_abs_jitter"]

        nominal = self.nominal
        if nominal > 0.0:
            nominal_hz = 1000.0 / nominal
            target_text = f"target ~{nominal:.1f} ms / {nominal_hz:.1f} Hz"
        else:
            target_text = "no nominal period configured"

        sys.stdout.write(
            f"{LOG_PREFIX} {self.name} timing stats ({target_text}): "
            f"mean_dt={mean_dt:.1f} ms (avg interval), "
            f"min_dt={min_dt:.1f} ms (shortest), "
            f"max_dt={max_dt:.1f} ms (longest), "
            f"mean|Δt-nominal|={mean_abs:.1f} ms (avg deviation), "
            f"max|Δt-nominal|={max_abs:.1f} ms (worst deviation), "
            f"intervals={stats['count']}\n"
        )