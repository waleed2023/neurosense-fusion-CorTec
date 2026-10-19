from __future__ import annotations

import asyncio
import os
from typing import Optional

from app.manager.broadcaster import Broadcaster
from app.manager.config import (
    DISABLE_MARKS,
    MAX_DELAY_MS_ACC_DUMMY,
    MAX_DELAY_MS_EMG_DUMMY,
    SENSOR_METRICS_LOG_FILE,
    now_ns,
    ns_to_us,
)
from app.measurment.timing_mark_logger import TimingMarkLogger
from app.proto import fusion_dummy_pb2 as fusion_minimal_pb2
from app.proto import sensor_pb2

# Optional: reines Compute-Meter (hat nichts mit CSV-Marks zu tun)
from app.tests.client_emu import DeadlineMeter

MEASURE_ONLY = os.getenv("MEASURE_ONLY", "0") == "1"

acc_meter = DeadlineMeter("ACC_ALGO_ONLY", target_hz=833.0, log_every_s=1.0)
emg_meter = DeadlineMeter("EMG_ALGO_ONLY", target_hz=10.0, log_every_s=1.0)


class FusionManagerMinimalEvent:
    """Minimal, event-driven fusion (latest-of-each).

    - Publisht für *jedes* eingehende Sample (EMG oder ACC) genau 1 fused Output.
    - Sample-and-hold des anderen Sensors.
    - Stale-Handling via MAX_DELAY (separat für EMG/ACC).
    - Timing-Messung: NUR EIN MARK PRO OUTPUT -> "Fusion_Dummy_OUT" (Wall time nach publish).
    """

    def __init__(
        self,
        emg_broadcaster: Broadcaster,
        acc_broadcaster: Broadcaster,
        fusion_broadcaster: Broadcaster,
        *,
        max_delay_ms: int = MAX_DELAY_MS_EMG_DUMMY,  # bleibt kompatibel: gilt für EMG
    ) -> None:
        self._emg_b = emg_broadcaster
        self._acc_b = acc_broadcaster
        self._fusion_b = fusion_broadcaster

        # Delay-Grenzen (µs)
        self._max_delay_us_emg = int(max_delay_ms) * 1000
        self._max_delay_us_acc = int(MAX_DELAY_MS_ACC_DUMMY) * 1000

        self._task: Optional[asyncio.Task] = None
        self._seq = 0

        self._last_emg: Optional[sensor_pb2.EmgSample] = None
        self._last_acc: Optional[sensor_pb2.AccelSample] = None

        self._marklog = None if DISABLE_MARKS else TimingMarkLogger(log_file=SENSOR_METRICS_LOG_FILE)

    def _mark_wall(self, name: str, idx: int) -> None:
        if DISABLE_MARKS or self._marklog is None:
            return
        self._marklog.mark(name, idx, ns_to_us(now_ns()))

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

        if self._marklog is not None:
            self._marklog.close()

    @staticmethod
    def _fresh(*, ref_ts_us: int, sample_ts_us: int, max_delay_us: int) -> bool:
        if sample_ts_us <= 0 or ref_ts_us <= 0:
            return False
        if max_delay_us <= 0:
            return True
        dt_us = int(ref_ts_us) - int(sample_ts_us)
        return 0 <= dt_us <= int(max_delay_us)

    def _build_fused(
        self,
        *,
        out_ts_us: int,
        trigger: int,
        emg_new: int,
        acc_new: int,
    ) -> fusion_minimal_pb2.FusedMinimalSample:
        emg_ok = (
            self._last_emg is not None
            and self._fresh(
                ref_ts_us=out_ts_us,
                sample_ts_us=int(getattr(self._last_emg, "ts_us", 0)),
                max_delay_us=self._max_delay_us_emg,
            )
        )
        acc_ok = (
            self._last_acc is not None
            and self._fresh(
                ref_ts_us=out_ts_us,
                sample_ts_us=int(getattr(self._last_acc, "ts_us", 0)),
                max_delay_us=self._max_delay_us_acc,
            )
        )

        # Sub-samples (oder 0/invalid wenn stale)
        emg_ts_us = int(self._last_emg.ts_us) if emg_ok else 0
        emg_value = int(self._last_emg.value) if emg_ok else 0
        emg_batt = int(self._last_emg.battery) if emg_ok else 255

        acc_ts_us = int(self._last_acc.ts_us) if acc_ok else 0
        ax = float(self._last_acc.ax) if acc_ok else 0.0
        ay = float(self._last_acc.ay) if acc_ok else 0.0
        az = float(self._last_acc.az) if acc_ok else 0.0

        self._seq += 1
        return fusion_minimal_pb2.FusedMinimalSample(
            ts_us=int(out_ts_us),  # timestamp des Trigger-Samples (HAL time)
            seq=int(self._seq),
            emg_ts_us=emg_ts_us,
            emg_value=emg_value,
            emg_battery=emg_batt,
            emg_valid=bool(emg_ok),
            emg_count=int(emg_new),
            acc_ts_us=acc_ts_us,
            ax=ax,
            ay=ay,
            az=az,
            acc_valid=bool(acc_ok),
            acc_count=int(acc_new),
            trigger=int(trigger),
        )

    async def _run(self) -> None:
        emg_q = await self._emg_b.subscribe()
        acc_q = await self._acc_b.subscribe()

        emg_get: Optional[asyncio.Task] = asyncio.create_task(emg_q.get())
        acc_get: Optional[asyncio.Task] = asyncio.create_task(acc_q.get())

        try:
            while True:
                done, _ = await asyncio.wait(
                    {emg_get, acc_get},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in done:
                    t0_ns = asyncio.get_running_loop().time()  # monotonic seconds
                    t0_ns = int(t0_ns * 1_000_000_000)

                    # -------------------------
                    # EMG
                    # -------------------------
                    if task is emg_get:
                        result: sensor_pb2.EmgSample = task.result()
                        self._last_emg = result

                        out_ts = int(getattr(result, "ts_us", 0) or 0)
                        if out_ts <= 0:
                            out_ts = ns_to_us(now_ns())  # Fallback

                        fused = self._build_fused(
                            out_ts_us=out_ts,
                            trigger=fusion_minimal_pb2.TRIGGER_EMG,
                            emg_new=1,
                            acc_new=0,
                        )

                        if not MEASURE_ONLY:
                            await self._fusion_b.publish(fused)

                        # EINZIGER Messpunkt: Output ist "fertig & raus"
                        self._mark_wall("Fusion_Dummy_OUT", int(fused.seq))

                        t1_ns = int(asyncio.get_running_loop().time() * 1_000_000_000)
                        emg_meter.tick(t0_ns, t1_ns, produced_count=1)

                        emg_get = asyncio.create_task(emg_q.get())

                    # -------------------------
                    # ACC
                    # -------------------------
                    elif task is acc_get:
                        result: sensor_pb2.AccelSample = task.result()
                        self._last_acc = result

                        out_ts = int(getattr(result, "ts_us", 0) or 0)
                        if out_ts <= 0:
                            out_ts = ns_to_us(now_ns())  # Fallback

                        fused = self._build_fused(
                            out_ts_us=out_ts,
                            trigger=fusion_minimal_pb2.TRIGGER_ACC,
                            emg_new=0,
                            acc_new=1,
                        )

                        if not MEASURE_ONLY:
                            await self._fusion_b.publish(fused)

                        # EINZIGER Messpunkt: Output ist "fertig & raus"
                        self._mark_wall("Fusion_Dummy_OUT", int(fused.seq))

                        t1_ns = int(asyncio.get_running_loop().time() * 1_000_000_000)
                        acc_meter.tick(t0_ns, t1_ns, produced_count=1)

                        acc_get = asyncio.create_task(acc_q.get())

        finally:
            for t in (emg_get, acc_get):
                if t is not None and not t.done():
                    t.cancel()
            await self._emg_b.unsubscribe(emg_q)
            await self._acc_b.unsubscribe(acc_q)
