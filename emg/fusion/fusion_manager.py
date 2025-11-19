# emg/fusion/fusion_manager.py
import asyncio
import time
from typing import Optional, List

from emg.broadcaster import Broadcaster
from emg.proto import emg_pb2, fusion_pb2


def now_ms() -> int:
    # Common wall-clock in ms, same as used in EMG/Accel managers.
    return int(time.time() * 1000)


class FusionManager:
    """
    Collects EMG and accelerometer samples from their broadcasters,
    aggregates them in fixed time windows, computes simple features,
    and publishes FusedSample objects into fusion_broadcaster.
    """
    def __init__(
        self,
        emg_broadcaster: Broadcaster,
        acc_broadcaster: Broadcaster,
        fusion_broadcaster: Broadcaster,
        *,
        window_ms: int = 500,
        step_ms: int = 200,
        min_emg_samples: int = 1,
        min_acc_samples: int = 1,
    ):
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster

        self._window_ms = window_ms
        self._step_ms = step_ms
        self._min_emg = min_emg_samples
        self._min_acc = min_acc_samples

        self._task: Optional[asyncio.Task] = None
        self._seq: int = 0

        # Debug counters for test code (removed later)
        self._debug_emg_read: int = 0
        self._debug_acc_read: int = 0


    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fusion-manager")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
    # debug properties, removed later
    @property
    def debug_emg_read(self) -> int:
        """Number of EMG samples read from the EMG queue."""
        return self._debug_emg_read

    # debug properties, removed later
    @property
    def debug_acc_read(self) -> int:
        """Number of ACC samples read from the ACC queue."""
        return self._debug_acc_read


    async def _run(self) -> None:
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        emg_buf: List[emg_pb2.EmgSample] = []
        acc_buf: List[emg_pb2.AccelSample] = []

        last_window_ts = now_ms()

        try:
            while True:
                # 1) Drain new EMG samples into buffer
                try:
                    while True:
                        s = emg_q.get_nowait()
                        self._debug_emg_read += 1 # count how EMG samples are consumed
                        emg_buf.append(s)
                except asyncio.QueueEmpty:
                    pass

                # 2) Drain new ACC samples into buffer
                try:
                    while True:
                        s = acc_q.get_nowait()
                        self._debug_acc_read += 1 # count how ACC samples are consumed
                        acc_buf.append(s)
                except asyncio.QueueEmpty:
                    pass

                now = now_ms()
                if (now - last_window_ts) >= self._step_ms:
                    win_end = now
                    win_start = win_end - self._window_ms

                    # Select samples inside current window
                    emg_window = [s for s in emg_buf if win_start <= s.ts_ms <= win_end]
                    acc_window = [s for s in acc_buf if win_start <= s.ts_ms <= win_end]

                    # Drop old samples from buffers (keep only those >= win_start)
                    emg_buf = [s for s in emg_buf if s.ts_ms >= win_start]
                    acc_buf = [s for s in acc_buf if s.ts_ms >= win_start]

                    fused = self._compute_fused_sample(
                        emg_window=emg_window,
                        acc_window=acc_window,
                        ts_ms=win_end,
                    )
                    if fused is not None:
                        await self._fusion_b.publish(fused)

                    last_window_ts = now

                # Avoid busy-loop
                await asyncio.sleep(0.01)
        finally:
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)

    def _compute_fused_sample(
        self,
        *,
        emg_window: List[emg_pb2.EmgSample],
        acc_window: List[emg_pb2.AccelSample],
        ts_ms: int,
    ) -> Optional[fusion_pb2.FusedSample]:
        """
        Compute window-based features for EMG and ACC as *independent* pipelines,
        then combine them into a single FusedSample.

        - EMG pipeline:
            * emg_level: RMS of EMG 'value' (0..1000), normalized to ~0..1
            * emg_valid: True if at least min_emg_samples in this window
            * emg_count: number of EMG samples used

        - ACC pipeline:
            * move_level: mean(|mag|)
            * move_var: variance of mag (population variance)
            * acc_valid: True if at least min_acc_samples in this window
            * acc_count: number of ACC samples used

        IMPORTANT:
        - EMG features depend ONLY on emg_window.
        - ACC features depend ONLY on acc_window.
        - EMG and ACC do not influence each other.
        - Fusion (this method) is "dependent on both": it only emits a FusedSample
          if *both* EMG and ACC are valid in this window.
        """
        emg_count = len(emg_window)
        acc_count = len(acc_window)

        # Per-stream validity
        min_emg = getattr(self, "_min_emg_samples", 1)
        min_acc = getattr(self, "_min_acc_samples", 1)

        emg_valid = emg_count >= min_emg
        acc_valid = acc_count >= min_acc

        # --- Fusion depends on both streams ---
        # If you ever want "at least one stream is valid", change this to:
        #   if not emg_valid and not acc_valid: return None
        if not emg_valid or not acc_valid:
            return None

        # ------------- EMG FEATURES (independent from ACC) -------------
        if emg_valid:
            # RMS of EMG values (0..1000)
            sq_sum = 0.0
            for s in emg_window:
                v = float(s.value)
                sq_sum += v * v
            mean_sq = sq_sum / float(emg_count)
            emg_rms = mean_sq**0.5

            # Normalize to ~0..1 assuming full scale ~1000
            emg_level = emg_rms / 1000.0
        else:
            # Should not happen because of gate above, but keep for safety
            emg_level = 0.0

        # ------------- ACC FEATURES (independent from EMG) -------------
        if acc_valid:
            # Use |mag| as simple motion magnitude
            mags = [float(abs(s.mag)) for s in acc_window]

            # Mean(|mag|)
            move_level = sum(mags) / float(acc_count)

            # Population variance of mag
            if acc_count > 1:
                mean_mag = move_level
                var_sum = 0.0
                for m in mags:
                    d = m - mean_mag
                    var_sum += d * d
                move_var = var_sum / float(acc_count)
            else:
                move_var = 0.0
        else:
            move_level = 0.0
            move_var = 0.0

        # ------------- SEQUENCE + OUTPUT -------------
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

