import sys
from typing import Optional

from emg.config import LOG_PREFIX, METRICS_LOG_FILE


class JitterTracker:
    """Track timing jitter statistics for incoming samples and log all values."""

    def __init__(
        self,
        nominal_period_ms: float,
        name: Optional[str] = None,
        log_file: Optional[str] = None,
    ):
        self.nominal = nominal_period_ms
        self.name = name or "JITTER"
        self.last_ts: Optional[int] = None

        self.count = 0
        self.sum_dt = 0.0
        self.min_dt = float("inf")
        self.max_dt = 0.0

        self.sum_abs = 0.0
        self.max_abs = 0.0

        self._sample_idx = 0
        self.log_file = log_file or METRICS_LOG_FILE

    def add(self, ts_ms: int) -> None:
        """Feed new timestamp (ms)."""
        if self.last_ts is None:
            self.last_ts = ts_ms
            return

        dt = float(ts_ms - self.last_ts)
        self.last_ts = ts_ms

        if dt < 0:
            return  # ignore negative

        self.count += 1
        self.sum_dt += dt
        self.min_dt = min(self.min_dt, dt)
        self.max_dt = max(self.max_dt, dt)

        abs_dev = 0.0
        if self.nominal > 0.0:
            abs_dev = abs(dt - self.nominal)
            self.sum_abs += abs_dev
            self.max_abs = max(self.max_abs, abs_dev)

        # alle Werte in CSV speichern
        self._sample_idx += 1
        self._append_sample_to_file(ts_ms, dt, abs_dev)

    def _append_sample_to_file(self, ts_ms: int, dt_ms: float, abs_dev_ms: float) -> None:
        """Eine Zeile pro Jitter-Messung in CSV."""
        if not self.log_file:
            return
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                # kind,name,idx,ts_ms,dt_ms,abs_dev_ms,delay_ms
                f.write(
                    f"jitter,{self.name},{self._sample_idx},{ts_ms},"
                    f"{dt_ms:.3f},{abs_dev_ms:.3f},\n"
                )
        except OSError:
            pass

    def _compute_stats(self) -> dict:
        if self.count == 0:
            return dict(
                count=0,
                mean_dt=0.0,
                min_dt=0.0,
                max_dt=0.0,
                mean_abs=0.0,
                max_abs=0.0,
            )
        mean_dt = self.sum_dt / self.count
        mean_abs = self.sum_abs / self.count if self.nominal > 0.0 else 0.0
        return dict(
            count=self.count,
            mean_dt=mean_dt,
            min_dt=self.min_dt,
            max_dt=self.max_dt,
            mean_abs=mean_abs,
            max_abs=self.max_abs,
        )

    def _log_jitter_summary(self) -> None:
        stats = self._compute_stats()
        nominal = self.nominal
        if nominal > 0.0:
            nominal_hz = 1000.0 / nominal
            target_text = f"target ~{nominal:.1f} ms / {nominal_hz:.1f} Hz"
        else:
            target_text = "no nominal period configured"

        mean_dt = stats["mean_dt"]
        min_dt = stats["min_dt"]
        max_dt = stats["max_dt"]
        mean_abs = stats["mean_abs"]
        max_abs = stats["max_abs"]

        sys.stdout.write(
            f"{LOG_PREFIX} {self.name} timing stats ({target_text}):\n"
            f"  intervals            : {stats['count']}\n"
            f"  dt [ms]              : mean={mean_dt:.1f}, "
            f"min={min_dt:.1f}, max={max_dt:.1f}\n"
            f"  deviation from target: mean={mean_abs:.1f} ms, "
            f"max={max_abs:.1f} ms\n"
        )

        # Summary ebenfalls in CSV (eine Zeile)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(
                    f"jitter_summary,{self.name},,{target_text},"
                    f"{mean_dt:.3f},{mean_abs:.3f},{stats['count']}\n"
                )
        except OSError:
            pass
