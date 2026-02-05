# algo_deadline_meter.py
import time

class DeadlineMeter:
    def __init__(self, name: str, target_hz: float, log_every_s: float = 1.0, keep: int = 5000):
        self.name = name
        self.target_hz = float(target_hz)
        self.period_us = 1_000_000.0 / self.target_hz
        self.log_every_s = float(log_every_s)
        self.keep = int(keep)

        self.t0 = time.monotonic()
        self.last_log = self.t0
        self.n = 0
        self.missed = 0
        self.compute_us = []

    def tick(self, t_start_ns: int, t_end_ns: int, produced_count: int = 1):
        # produced_count: wie viele Samples dein Algo in diesem Schritt "produziert"
        c_us = (t_end_ns - t_start_ns) / 1000.0
        self.n += int(produced_count)

        self.compute_us.append(c_us)
        if len(self.compute_us) > self.keep:
            self.compute_us = self.compute_us[-self.keep:]

        if c_us > self.period_us:
            self.missed += 1

        now = time.monotonic()
        if now - self.last_log >= self.log_every_s and self.compute_us:
            xs = sorted(self.compute_us)
            p50 = xs[len(xs)//2]
            p95 = xs[int(0.95 * (len(xs)-1))]
            p99 = xs[int(0.99 * (len(xs)-1))]
            avg_hz = self.n / (now - self.t0)
            print(
                f"[{self.name}] avg_rate={avg_hz:.2f} Hz | "
                f"compute_us p50={p50:.0f} p95={p95:.0f} p99={p99:.0f} | "
                f"missed={self.missed}"
            )
            self.last_log = now
