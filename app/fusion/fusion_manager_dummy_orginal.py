from __future__ import annotations

import asyncio
from typing import Optional

from app.manager.broadcaster import Broadcaster
from app.manager.config import  MAX_DELAY_MS_EMG_DUMMY, MAX_DELAY_MS_ACC_DUMMY, SENSOR_METRICS_LOG_FILE, now_ns, ns_to_us
from app.measurment.timing_mark_logger import TimingMarkLogger
from app.proto import sensor_pb2, fusion_dummy_pb2 as fusion_minimal_pb2


# for client emu.
import os
import time
from app.tests import DeadlineMeter

MEASURE_ONLY = os.getenv("MEASURE_ONLY", "0") == "1"

acc_meter = DeadlineMeter("ACC_ALGO_ONLY", target_hz=833.0, log_every_s=1.0)
emg_meter = DeadlineMeter("EMG_ALGO_ONLY", target_hz=10.0,  log_every_s=1.0)


class FusionManagerMinimalEvent:
    """Minimal, event-driven fusion (latest-of-each).

    Design goals:
      - No step clock, no windows, no RMS/magnitude/variance.
      - Publish *one* fused output for *each* incoming sample (EMG or ACC).
      - Sample-and-hold the *other* sensor.
      - Optional MAX_DELAY: if the held sample is too old compared to the trigger
        timestamp, mark it invalid (and zero its fields).
        time <= max_delay:
            hold old sample as valid
        time > max_delay:
            mark held sample as invalid(zeros)
    """

    def __init__(
        self,
        emg_broadcaster: Broadcaster,
        acc_broadcaster: Broadcaster,
        fusion_broadcaster: Broadcaster,
        *,
        max_delay_ms: int = MAX_DELAY_MS_EMG_DUMMY,
    ) -> None:
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster
        self._max_delay_us = int(max_delay_ms) * 1000
        self._task: Optional[asyncio.Task] = None
        self._seq = 0
        self._last_emg: Optional[sensor_pb2.EmgSample] = None
        self._last_acc: Optional[sensor_pb2.AccelSample] = None
        # time stamps
        self._marklog = TimingMarkLogger(log_file=SENSOR_METRICS_LOG_FILE)



    # for timestamping
    def _mark_wall(self, point: str, idx: int) -> None:
        self._marklog.mark(point, idx, ns_to_us(now_ns()))

    def _mark_ts(self, point: str, idx: int, ts_us: int) -> None:
        self._marklog.mark(point, idx, int(ts_us))

        
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fusion-min-event")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def _fresh(self, *, ref_ts_us: int, sample_ts_us: int, is_emg:bool) -> bool:
        #print( f"ref_ts_us: {ref_ts_us}, sample_ts_us: {sample_ts_us}, is_emg: {is_emg}" )  
        if sample_ts_us <= 0 or ref_ts_us <= 0:
            return False
        max_delay_ms = MAX_DELAY_MS_EMG_DUMMY if is_emg else MAX_DELAY_MS_ACC_DUMMY
        max_delay_us = int(max_delay_ms) * 1000  # ms -> µs
        if max_delay_us <= 0:
            return True
        # Sample-and-hold: held sample must be <= trigger timestamp
        dt_us = int(ref_ts_us) - int(sample_ts_us)
        # Valid if not from the future and not too old
        return 0 <= dt_us <= max_delay_us

    
    def _build_fused(
        self,
        *,
        out_ts_us: int,
        trigger: int,
        emg_new: int,
        acc_new: int,
    ) -> fusion_minimal_pb2.FusedMinimalSample:
        # Decide whether the cached samples are fresh enough.
        # just with 2 variables, no need buffers or windows.
        # timestapes commes from HAL when the sample was read.
        emg_ok = self._last_emg is not None and self._fresh(
            ref_ts_us=out_ts_us, sample_ts_us=int(self._last_emg.ts_us), is_emg=True
        )
        acc_ok = self._last_acc is not None and self._fresh(
            ref_ts_us=out_ts_us, sample_ts_us=int(self._last_acc.ts_us), is_emg=False
        )

        # If a sensor is stale: expose it as "missing" (valid=false, zeros).
        emg_ts_us = int(self._last_emg.ts_us) if emg_ok else 0
        emg_value = int(self._last_emg.value) if emg_ok else 0
        emg_batt = int(self._last_emg.battery) if emg_ok else 255

        acc_ts_us = int(self._last_acc.ts_us) if acc_ok else 0
        ax = float(self._last_acc.ax) if acc_ok else 0.0
        ay = float(self._last_acc.ay) if acc_ok else 0.0
        az = float(self._last_acc.az) if acc_ok else 0.0

        self._seq += 1
        return fusion_minimal_pb2.FusedMinimalSample(
            ts_us=int(out_ts_us), # timestamp of the TRIGGER sample
            seq=int(self._seq),

            emg_ts_us=emg_ts_us, # EMG sub-sample timestamp (HAL time when the EMG sample arrived).
            emg_value=emg_value, # EMG raw value (sample-and-hold). 0 if EMG is invalid/stale.
            emg_battery=emg_batt,
            emg_valid=bool(emg_ok), # True if EMG sample exists AND is fresh enough relative to ts_us (<= MAX_DELAY).
            # How many *new* EMG samples were consumed for this fused output.
            # Usually 1 when EMG triggered, 0 when ACC triggered, 1 when both triggered.
            emg_count=int(emg_new),

            acc_ts_us=acc_ts_us,  # ACC sub-sample timestamp (HAL time when the ACC sample was read).
            ax=ax,
            ay=ay,
            az=az,
            acc_valid=bool(acc_ok),# True if ACC sample exists AND is fresh enough relative to ts_us (<= MAX_DELAY)
            acc_count=int(acc_new),# How many *new* ACC samples were consumed for this fused output.

            trigger=trigger,# Which sensor(s) triggered this fused output (EMG / ACC / EMG+ACC).
        )

    async def _run(self) -> None:
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        emg_get = asyncio.create_task(emg_q.get())
        acc_get = asyncio.create_task(acc_q.get())

        try:
            while True:
                # Wait for either an EMG or ACC sample.
                done, _pending = await asyncio.wait(
                    {emg_get, acc_get},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # publish once per completed task ("event-driven").
                for sensor in done:
                    if sensor is emg_get:
                        result: sensor_pb2.EmgSample = sensor.result()
                        self._last_emg = result
                        out_ts = int(getattr(result, "ts_us", 0))
                        fused = self._build_fused(
                            out_ts_us=out_ts,
                            trigger=fusion_minimal_pb2.TRIGGER_EMG,
                            emg_new=1,
                            acc_new=0,
                        )
                        seq = int(fused.seq)
                        self._mark_wall("Fusion_Dummy_EMG_ENTER", seq)
                        await self._fusion_b.publish(fused)
                        #self._mark_ts("Fusion_Dummy_EMG_Enter", seq, int(fused.ts_us))
                        self._mark_wall("Fusion_Dummy_EMG_Exit", seq)
                        emg_get = asyncio.create_task(emg_q.get()) 

                    elif sensor is acc_get:
                        result: sensor_pb2.AccelSample = sensor.result()
                        self._last_acc = result
                        out_ts = int(getattr(result, "ts_us", 0))
                        fused = self._build_fused(
                            out_ts_us=out_ts,
                            trigger=fusion_minimal_pb2.TRIGGER_ACC,
                            emg_new=0,
                            acc_new=1,
                        )
                        seq = int(fused.seq)
                        self._mark_wall("Fusion_Dummy_ACC_ENTER", seq)
                        await self._fusion_b.publish(fused)
                        self._mark_wall("Fusion_Dummy_ACC_Exit", seq)
                        #self._mark_ts("Fusion_Dummy_ACC_Sensor_Time_EXIT", seq, int(fused.ts_us))
                        acc_get = asyncio.create_task(acc_q.get())

        finally:
            for task in (emg_get, acc_get):
                if task and not task.done():
                    task.cancel()
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)
