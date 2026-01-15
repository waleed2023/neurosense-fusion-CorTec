# bandwidth.py

import time
import sys

class BandwidthTracker:
    """Tracks how many bytes per second are sent on a stream."""
    def __init__(self, label: str, log_interval_s: float = 5.0):
        self.label = label
        self.log_interval_s = log_interval_s
        self.total_bytes = 0
        self.start_ts = time.time()
        self.last_log_ts = self.start_ts

    def add(self, n_bytes: int) -> None:
        """Call this every time you send a protobuf message."""
        self.total_bytes += n_bytes
        now = time.time()

        if now - self.last_log_ts >= self.log_interval_s:
            duration = now - self.start_ts
            if duration <= 0:
                return

            bps = self.total_bytes * 8.0 / duration
            kbps = bps / 1000.0

            sys.stdout.write(
                f"[BW] {self.label}: {kbps:.2f} kbit/s "
                f"({self.total_bytes} bytes in {duration:.1f} s)\n"
            )
            sys.stdout.flush()
            self.last_log_ts = now
