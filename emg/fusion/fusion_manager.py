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
        window_ms: int = 200,
        step_ms: int = 100,
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
                        emg_buf.append(s)
                except asyncio.QueueEmpty:
                    pass

                # 2) Drain new ACC samples into buffer
                try:
                    while True:
                        s = acc_q.get_nowait()
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
        Compute basic window features:
        - emg_level: simple RMS over EMG value
        - move_level: mean(|mag|)
        - move_var: variance of mag
        plus valid flags and counts.
        """
        emg_count = len(emg_window)
        acc_count = len(acc_window)

        emg_valid = emg_count >= self._min_emg
        acc_valid = acc_count >= self._min_acc

        # If neither stream has usable data, skip this window completely
        if not emg_valid and not acc_valid:
            return None

        # --- EMG feature: RMS over "value" (0..1000) normalized to 0..1 ---
        if emg_valid:
            # Compute RMS of EMG value
            sq_sum = 0.0
            for s in emg_window:
                v = float(s.value)
                sq_sum += v * v
            mean_sq = sq_sum / emg_count
            emg_rms = mean_sq ** 0.5
            # Normalize to [0,1] using 1000 as nominal max
            emg_level = emg_rms / 1000.0
            #print (f"EMG RMS: {emg_rms:.1f} → level {emg_level:.3f}")
        else:
            # print("EMG data not valid")
            emg_level = 0.0

        # --- ACC features: mean(|mag|) and variance of mag ---
        if acc_valid:
            mags = [float(abs(s.mag)) for s in acc_window]
            # Mean(|mag|)
            move_level = sum(mags) / acc_count

            # Variance of mag (population variance)
            if acc_count > 1:
                mean_mag = sum(mags) / acc_count
                var_sum = 0.0
                for m in mags:
                    d = m - mean_mag
                    var_sum += d * d
                move_var = var_sum / acc_count
            else:
                move_var = 0.0
        else:
            move_level = 0.0
            move_var = 0.0
       # print(f"ACC move_level: {move_level:.3f}, move_var: {move_var:.3f}")

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
