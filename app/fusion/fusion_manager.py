# emg/fusion/fusion_manager.py
from __future__ import annotations

import asyncio
from typing import Optional, List

from matplotlib.pylab import sample

from app.fusion.rms_mag import OnlineRMSWindow, OnlineMagWindow
from app.manager.broadcaster import Broadcaster
from app.measurment.timing_mark_logger import TimingMarkLogger
from app.proto import sensor_pb2, fusion_pb2
from app.manager.config import (
    SENSOR_METRICS_LOG_FILE,
    WINDOW_MS,
    STEP_MS,
    MAX_DELAY_MS,
    now_ns,
    ns_to_us,
)


class FusionManager:
    """
    Fusion Algorithm Manager with Sliding Windows with steps.
    Fusion clock is driven by HAL timestamps (ts_us) only.
    - Starts as soon as *one* sensor delivers samples.
    - If one sensor has no samples in the window -> its output is 0 and valid=false.
    """

    def __init__(
        self,
        emg_broadcaster: Broadcaster,
        acc_broadcaster: Broadcaster,
        fusion_broadcaster: Broadcaster,
        *,
        window_ms: int = WINDOW_MS,
        step_ms: int = STEP_MS,
        max_delay_ms: int = MAX_DELAY_MS,
        min_emg_samples: int = 1,
        min_acc_samples: int = 1,
        max_steps_per_loop: int = 5000,
    ):
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster

        self._window_us = int(window_ms) * 1000
        self._step_us = max(1, int(step_ms) * 1000)
        self._max_delay_us = max(0, int(max_delay_ms) * 1000)

        self._min_emg_samples = int(min_emg_samples)
        self._min_acc_samples = int(min_acc_samples)
        self._max_steps_per_loop = int(max_steps_per_loop)

        self._task: Optional[asyncio.Task] = None
        self._seq: int = 0
        # later used for debugging
        # to know how many samples were read from each sensor ;)
        self._debug_emg_read: int = 0
        self._debug_acc_read: int = 0

        # Online windows (we use ts_us units consistently)
        self._emg_rms_win = OnlineRMSWindow(self._window_us)
        self._acc_mag_win = OnlineMagWindow(self._window_us)

        # Timing marks
        self._marklog = TimingMarkLogger(log_file=SENSOR_METRICS_LOG_FILE)
        self._BLE_fusion_idx = 0
        self._ACC_fusion_idx = 0
        self.Fusion_idx = 0

        # HAL-driven clock state
        self._first_ts_us: Optional[int] = None
        self._latest_emg_ts_us: Optional[int] = None
        self._latest_acc_ts_us: Optional[int] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fusion-manager")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    @property
    # not used for now
    def debug_emg_read(self) -> int:
        return self._debug_emg_read

    @property
    # not used for now
    def debug_acc_read(self) -> int:
        return self._debug_acc_read

    def _mark(self, point: str, idx: int) -> None:
        self._marklog.mark(point, idx, ns_to_us(now_ns()))

    @staticmethod
    # Partition samples by timestamp
    # return (ready, rest) where ready.ts_us <= ts_end_us
    # ready: List of samples ready for processing
    # rest: List of samples to keep for later
    def _partition_by_ts_us(buf: List, ts_end_us: int):
        ready, rest = [], []
        for sample in buf:
            (ready if sample.ts_us <= ts_end_us else rest).append(sample)
        return ready, rest

    # Drain all available samples from EMG queue into buf
    async def _drain_emg(self, queue: asyncio.Queue, buf: List[sensor_pb2.EmgSample]) -> None:
        while True:
            try:
                sample: sensor_pb2.EmgSample = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            self._BLE_fusion_idx += 1
            self._mark("BLEManager_Fusion_in", self._BLE_fusion_idx)

            self._debug_emg_read += 1
            buf.append(sample)
            ts = int(getattr(sample, "ts_us", 0)) # get ts_us from sensorHAL sample
            if ts > 0: # not to neccessarily but in case of corrupted samples
                # Update latest and first timestamps
                self._latest_emg_ts_us = ts if self._latest_emg_ts_us is None else max(self._latest_emg_ts_us, ts)
                if self._first_ts_us is None:
                    self._first_ts_us = ts # in case sample is first ever
                else:
                    self._first_ts_us = min(self._first_ts_us, ts) # keep earliest timestamp

    async def _drain_acc(self, q: asyncio.Queue, buf: List[sensor_pb2.AccelSample]) -> None:
        
        # Drain all available samples from ACC queue into buf
        # analog to comments in _drain_emg()
        while True:
            try:
                sample_HAL: sensor_pb2.AccelSample = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            
            self._ACC_fusion_idx += 1 # counter for fusion timing marks
            self._mark("ACCManager_Fusion_in", self._ACC_fusion_idx)

            self._debug_acc_read += 1
            buf.append(sample_HAL)
            ts = int(getattr(sample_HAL, "ts_us", 0)) # search for ts_us in HAL sample
            if ts > 0:
                self._latest_acc_ts_us = ts if self._latest_acc_ts_us is None else max(self._latest_acc_ts_us, ts)
                if self._first_ts_us is None:
                    self._first_ts_us = ts
                else:
                    self._first_ts_us = min(self._first_ts_us, ts) # choose earliest timestamp

    async def _run(self) -> None:
        # subscribe to EMG and ACC broadcasters
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()
        # Buffers for incoming samples
        emg_buf: List[sensor_pb2.EmgSample] = []
        acc_buf: List[sensor_pb2.AccelSample] = []
        # next_tick_us is the timestamp of the next fusion step
        next_tick_us: Optional[int] = None

        try:
            while True:
                # 1) store all available samples from sensors
                await self._drain_emg(emg_q, emg_buf)
                await self._drain_acc(acc_q, acc_buf)

                # 2) start fusion clock once first sample exists
                if next_tick_us is None and self._first_ts_us is not None:
                    base_tick = int(self._first_ts_us) + self._max_delay_us # first ts_us arrived sample
                                                                            # from HAL + max_delay(maximal allowable latency for fusion)
                    # ceil-align to step grid, align to step grid
                    next_tick_us = ((base_tick + self._step_us - 1) // self._step_us) * self._step_us

                # 3) if still no sensor data -> idle
                if next_tick_us is None:
                    await asyncio.sleep(0.01)
                    continue

                # 4) drive clock by newest HAL timestamp (watermark)
                # look at HAL timestamps that arrived so far and allow fusion ticks up to that time + max_delay
                # this allows to produce fused samples as long sensor data arrives
                watermark_us: Optional[int] = None
                if self._latest_emg_ts_us is not None:
                    watermark_us = self._latest_emg_ts_us
                if self._latest_acc_ts_us is not None:
                    watermark_us = watermark_us if watermark_us is not None else self._latest_acc_ts_us
                    watermark_us = max(watermark_us, self._latest_acc_ts_us)

                if watermark_us is None:
                    await asyncio.sleep(0.01)
                    continue

                allowed_tick_us = int(watermark_us) + self._max_delay_us
                

                # every ticks we look in time window and compute fused sample
                # we take from buffers all samples with ts_us <= wind_end
                # compute Feature and publish fused sample
                steps = 0
                while next_tick_us <= allowed_tick_us and steps < self._max_steps_per_loop:
                    win_end_us = int(next_tick_us) - self._max_delay_us
                    win_start_us = win_end_us - self._window_us

                    # EMG
                    if emg_buf:
                        ready, emg_buf = self._partition_by_ts_us(emg_buf, win_end_us)
                        for s in ready:
                            self._emg_rms_win.add_sample(int(s.ts_us), float(s.value))

                    # ACC
                    if acc_buf:
                        ready, acc_buf = self._partition_by_ts_us(acc_buf, win_end_us)
                        for s in ready:
                            self._acc_mag_win.add_sample(int(s.ts_us), float(abs(s.mag)))

                    # slide windows
                    self._emg_rms_win.drop_older_than(win_start_us)
                    self._acc_mag_win.drop_older_than(win_start_us)
                    # compute fused sample and publish
                    fused = self._compute_fused_sample(ts_us=win_end_us)
                    await self._fusion_b.publish(fused)

                    self.Fusion_idx += 1
                    self._mark("FusionManager_out", self.Fusion_idx)

                    next_tick_us += self._step_us
                    steps += 1

                # 5) wait a bit for new sensor data (keeps loop responsive)
                await asyncio.sleep(0.001)

        finally:
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)

    def _compute_fused_sample(self, *, ts_us: int) -> fusion_pb2.FusedSample:
        emg_rms, emg_count = self._emg_rms_win.get_rms()
        move_level, move_var, acc_count = self._acc_mag_win.get_stats()

        emg_valid = emg_count >= self._min_emg_samples
        acc_valid = acc_count >= self._min_acc_samples

        emg_level = (emg_rms / 1000.0) if emg_valid else 0.0
        if not acc_valid:
            move_level = 0.0
            move_var = 0.0

        self._seq += 1
        return fusion_pb2.FusedSample(
            ts_us=int(ts_us),              # Timestamp in microseconds (aligned fusion tick)
            seq=int(self._seq),            # Monotonic sequence number for each fused output (good for debugging)
            emg_level=float(emg_level),    # EMG feature value (e.g., window RMS), normalized/processed
            emg_valid=bool(emg_valid),     # True if enough EMG samples were available in the window (for alignment)
            emg_count=int(emg_count),      # Number of EMG samples used for this fused frame
            move_level=float(move_level),  # Motion/accel feature value (e.g., magnitude-based level)
            move_var=float(move_var),      # Motion variability over the window (variance) (we used as output for tabletapp)
            acc_valid=bool(acc_valid),     # True if enough ACC samples were available in the window
            acc_count=int(acc_count),      # Number of ACC samples used for this fused frame
        )
