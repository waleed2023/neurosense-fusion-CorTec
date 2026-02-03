# emg/measurment/timing_mark_logger.py
#from __future__ import annotations
#from typing import Optional
#from app.manager.config import METRICS_LOG_FILE

#class TimingMarkLogger:
 #   """
 #   CSV rows:
  #    mark,<name>,<idx>,<ts_us>,,,
   # """
    #def __init__(self, log_file: Optional[str] = None) -> None:
     #   self.log_file = log_file or METRICS_LOG_FILE

    #def mark(self, name: str, idx: int, ts_us: int) -> None:
     #   if not self.log_file:
      #      return
      #  try:
       #     with open(self.log_file, "a", encoding="utf-8") as f:
                # kind,name,idx,ts_us,dt_us,abs_dev_us,delay_us
        #        f.write(f"mark,{name},{idx},{ts_us},,,\n")
        #except OSError:
         #   pass



# emg/measurment/timing_mark_logger.py
from __future__ import annotations

import atexit
import queue
import threading
import time
from typing import Optional

from app.manager.config import METRICS_LOG_FILE


class TimingMarkLogger:
    """
    Fast, non-blocking CSV logger.

    CSV rows:
      mark,<name>,<idx>,<ts_us>,,,

    - mark() is non-blocking (enqueue only)
    - background thread writes buffered lines
    - bounded queue (optional drop oldest)
    """

    def __init__(
        self,
        log_file: Optional[str] = None,
        *,
        max_queue: int = 20000,
        drop_oldest_on_full: bool = True,
        flush_every: int = 256,
        flush_interval_s: float = 0.25,
    ) -> None:
        self.log_file = log_file or METRICS_LOG_FILE
        self._enabled = bool(self.log_file)
        print(f"{AX},{AY},{AZ}")
        self._q: queue.Queue[str] = queue.Queue(maxsize=max_queue)
        self._drop_oldest = drop_oldest_on_full
        self._flush_every = max(1, int(flush_every))
        self._flush_interval_s = max(0.01, float(flush_interval_s))

        self._stop = threading.Event()
        self._t: Optional[threading.Thread] = None

        if self._enabled:
            self._t = threading.Thread(target=self._worker, name="TimingMarkLogger", daemon=True)
            self._t.start()
            atexit.register(self.close)

    def mark(self, name: str, idx: int, ts_us: int) -> None:
        if not self._enabled:
            return

        line = f"mark,{name},{idx},{ts_us},,,\n"

        try:
            self._q.put_nowait(line)
        except queue.Full:
            if not self._drop_oldest:
                return  # drop newest
            # drop one oldest, then try again
            try:
                _ = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                self._q.put_nowait(line)
            except queue.Full:
                return

    def close(self) -> None:
        if not self._enabled:
            return
        self._stop.set()
        t = self._t
        if t and t.is_alive():
            t.join(timeout=1.0)

    def _worker(self) -> None:
        buf: list[str] = []
        last_flush = time.monotonic()

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                while not self._stop.is_set() or not self._q.empty():
                    try:
                        line = self._q.get(timeout=0.2)
                        buf.append(line)
                    except queue.Empty:
                        pass

                    now = time.monotonic()
                    if buf and (len(buf) >= self._flush_every or (now - last_flush) >= self._flush_interval_s):
                        f.writelines(buf)
                        # flush = OS buffer, kein fsync (fsync wäre wieder slow)
                        f.flush()
                        buf.clear()
                        last_flush = now

                # final flush
                if buf:
                    f.writelines(buf)
                    f.flush()

        except OSError:
            # disable on error
            self._enabled = False
