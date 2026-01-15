# emg/measurment/timing_mark_logger.py
from __future__ import annotations
from typing import Optional
from emg.config import METRICS_LOG_FILE

class TimingMarkLogger:
    """
    CSV rows:
      mark,<name>,<idx>,<ts_us>,,,
    """
    def __init__(self, log_file: Optional[str] = None) -> None:
        self.log_file = log_file or METRICS_LOG_FILE

    def mark(self, name: str, idx: int, ts_us: int) -> None:
        if not self.log_file:
            return
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                # kind,name,idx,ts_us,dt_us,abs_dev_us,delay_us
                f.write(f"mark,{name},{idx},{ts_us},,,\n")
        except OSError:
            pass
