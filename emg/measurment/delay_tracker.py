import sys
from typing import Optional

from emg.config import LOG_PREFIX, METRICS_LOG_FILE


class DelayTracker:
    """Track delay (age) statistics in ms and log all values."""

    def __init__(self, name: Optional[str] = None, log_file: Optional[str] = None):
        self.name = name or "DELAY"
        self.count = 0
        self.sum_delay = 0.0
        self.min_delay = float("inf")
        self.max_delay = 0.0

        self._sample_idx = 0
        self.log_file = log_file or METRICS_LOG_FILE

    def add(self, delay_ms: float, ts_ms: Optional[int] = None) -> None:
        """Neuen Delay-Wert (ms) hinzufügen."""
        if delay_ms < 0:
            delay_ms = 0.0

        self.count += 1
        self.sum_delay += delay_ms
        self.min_delay = min(self.min_delay, delay_ms)
        self.max_delay = max(self.max_delay, delay_ms)

        self._sample_idx += 1
        self._append_sample_to_file(delay_ms, ts_ms)

    def _append_sample_to_file(self, delay_ms: float, ts_ms: Optional[int]) -> None:
        """Eine Zeile pro Delay-Messung in CSV."""
        if not self.log_file:
            return
        if ts_ms is None:
            ts_ms = 0
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                # kind,name,idx,ts_ms,dt_ms,abs_dev_ms,delay_ms
                f.write(
                    f"delay,{self.name},{self._sample_idx},{ts_ms},,,{delay_ms:.3f}\n"
                )
        except OSError:
            pass

    def _compute_stats(self) -> dict:
        if self.count == 0:
            return dict(count=0, mean_delay=0.0, min_delay=0.0, max_delay=0.0)
        mean_delay = self.sum_delay / self.count
        return dict(
            count=self.count,
            mean_delay=mean_delay,
            min_delay=self.min_delay,
            max_delay=self.max_delay,
        )

    def log_summary(self) -> None:
        stats = self._compute_stats()
        sys.stdout.write(
            f"{LOG_PREFIX} {self.name} delay stats:\n"
            f"  samples : {stats['count']}\n"
            f"  delay[ms]: mean={stats['mean_delay']:.1f}, "
            f"min={stats['min_delay']:.1f}, max={stats['max_delay']:.1f}\n"
        )

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(
                    f"delay_summary,{self.name},,,"
                    f"{stats['mean_delay']:.3f},,"
                    f"{stats['count']}\n"
                )
        except OSError:
            pass
