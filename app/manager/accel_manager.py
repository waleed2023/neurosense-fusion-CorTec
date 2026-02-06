# emg/accel_manager.py
from __future__ import annotations

import math
import time
import asyncio
import sys
from typing import Optional

from smbus2 import SMBus

from .config import (
    BIT_TIMESTAMP_EN,
    I2C_BUS,
    I2C_ADDR,
    ACCEL_ODR_HZ,
    ACCEL_FS_G,
    LOG_PREFIX,
    REG_CTRL10_C,
    REG_CTRL1_XL,
    REG_CTRL2_G,
    REG_CTRL3_C,
    REG_TIMESTAMP0,
    REG_WHO_AM_I,
    OUTX_L_XL,
    SENSOR_METRICS_LOG_FILE,
    TS_TICK_US,
    now_ns,
    ns_to_us,
)
from ..proto import sensor_pb2
from app.measurment.timing_mark_logger import TimingMarkLogger


def to_s16(lo, hi):
    v = (hi << 8) | lo
    return v - 65536 if v > 32767 else v


def scale_factor_g(fs_g: int) -> float:
    if fs_g == 2:
        return 0.000061
    if fs_g == 4:
        return 0.000122
    if fs_g == 8:
        return 0.000244
    if fs_g == 16:
        return 0.000488
    return 0.000061


class AccelManager:
    """
    Polls the LSM6DSx via I²C and publishes AccelSample via the Broadcaster.
    Uses Unix epoch timestamps in µs (ts_us).
    """

    def __init__(self, broadcaster):
        self.broadcaster = broadcaster
        self._stop = asyncio.Event()
        self._bus: Optional[SMBus] = None

        self._marklog = TimingMarkLogger(log_file=SENSOR_METRICS_LOG_FILE)
        self._hal_idx = 0
        self._hal_idx_without_scale = 0


    async def stop(self):
        self._stop.set()

    def _enable_timestamp(self, bus: SMBus, addr: int) -> None:
        """Enable internal timestamp counter in LSM6DSOX."""
        v = bus.read_byte_data(addr, REG_CTRL10_C) & 0xFF
        bus.write_byte_data(addr, REG_CTRL10_C, (v | BIT_TIMESTAMP_EN) & 0xFF)

    def _read_timestamp_ticks(self, bus: SMBus, addr: int) -> int:
        """Read 32-bit timestamp ticks (little-endian) from 0x40..0x43."""
        data = bus.read_i2c_block_data(addr, REG_TIMESTAMP0, 4)
        ts = (data[0]
            | (data[1] << 8)
            | (data[2] << 16)
            | (data[3] << 24))
        return ts & 0xFFFFFFFF

    def read_timestamp_us(self, bus: SMBus, addr: int) -> int:
        """Timestamp in microseconds (nominal)."""
        return self._read_timestamp_ticks(bus, addr) * TS_TICK_US


    def _init_sensor(self, bus: SMBus, addr: int):
        who = bus.read_byte_data(addr, REG_WHO_AM_I)
        if who not in (0x6A, 0x6B, 0x69, 0x6C):
            sys.stderr.write(f"{LOG_PREFIX} Warning: WHO_AM_I=0x{who:02X}\n")

        # soft reset
        bus.write_byte_data(addr, REG_CTRL3_C, 0x01)
        t0 = time.time()
        while (bus.read_byte_data(addr, REG_CTRL3_C) & 0x01) != 0:
            if time.time() - t0 > 0.2:
                break
            time.sleep(0.005)

        # BDU=1, IF_INC=1
        bus.write_byte_data(addr, REG_CTRL3_C, 0x44)

        # ODR bits
        if ACCEL_ODR_HZ <= 0:
            odr_bits = 0x00
        elif ACCEL_ODR_HZ >= 6660:
            odr_bits = 0xA0
        elif ACCEL_ODR_HZ >= 3330:
            odr_bits = 0x90
        elif ACCEL_ODR_HZ >= 1660:
            odr_bits = 0x80
        elif ACCEL_ODR_HZ >= 833:
            odr_bits = 0x70
        elif ACCEL_ODR_HZ >= 416:
            odr_bits = 0x60
        elif ACCEL_ODR_HZ >= 208:
            odr_bits = 0x50
        elif ACCEL_ODR_HZ >= 104:
            odr_bits = 0x40
        elif ACCEL_ODR_HZ >= 52:
            odr_bits = 0x30
        elif ACCEL_ODR_HZ >= 26:
            odr_bits = 0x20
        else:
            odr_bits = 0x10

        # FS bits
        if ACCEL_FS_G == 16:
            fs_bits = 0x04
        elif ACCEL_FS_G == 4:
            fs_bits = 0x08
        elif ACCEL_FS_G == 8:
            fs_bits = 0x0C
        else:
            fs_bits = 0x00

        bus.write_byte_data(addr, REG_CTRL1_XL, odr_bits | fs_bits)
        bus.write_byte_data(addr, REG_CTRL2_G, 0x00)

    def _read_accel_g(self, bus: SMBus, addr: int):
        a = bus.read_i2c_block_data(addr, OUTX_L_XL, 6)
        ax = to_s16(a[0], a[1])
        ay = to_s16(a[2], a[3])
        az = to_s16(a[4], a[5])


        sf = scale_factor_g(ACCEL_FS_G)
        AX = ax * sf
        AY = ay * sf
        AZ = az * sf
        MAG = math.sqrt(AX * AX + AY * AY + AZ * AZ)
        return AX, AY, AZ, MAG
    
    async def _broadcaster_worker(self):
            """Dieser Task läuft im Hintergrund und sendet die Daten."""
            while not self._stop.is_set():
                try:
                    sample = await self.queue.get()
                    await self.broadcaster.publish(sample)
                    self.queue.task_done()
                except Exception as e:
                    sys.stderr.write(f"{LOG_PREFIX} Broadcast-Error: {e}\n")

    async def run(self):
        period = 1.0 / max(1.0, float(ACCEL_ODR_HZ))
        worker_task = asyncio.create_task(self._broadcaster_worker())
        backoff = 1.0
        max_backoff = 10.0

        while not self._stop.is_set():
            try:
                with SMBus(I2C_BUS) as bus:
                    self._bus = bus
                    self._init_sensor(bus, I2C_ADDR)
                    self._enable_timestamp(bus, I2C_ADDR) # enable timesatmp in Sensor
                    sys.stdout.write(
                        f"{LOG_PREFIX} Accel-Streaming started "
                        f"(I2C bus={I2C_BUS} addr=0x{I2C_ADDR:02X})\n"
                    )
                    next_call = time.perf_counter()
                    #last = 0.0
                    backoff = 1.0

                    while not self._stop.is_set():
                        AX, AY, AZ, MAG = self._read_accel_g(bus, I2C_ADDR)

                        self._hal_idx += 1
                        idx = self._hal_idx
                        # timestamp from sensor
                        #acc_ts_us = self.read_timestamp_us(bus, I2C_ADDR)
                        #self._marklog.mark("ACC_SENSOR_TS", idx, acc_ts_us)
                        # epoch timestampe
                        ts_us = ns_to_us(now_ns())
                        self._marklog.mark("ACC_HAL_ENTRY", idx, ts_us)

                        sample = sensor_pb2.AccelSample(
                            ax=AX,
                            ay=AY,
                            az=AZ,
                            mag=MAG,
                            ts_us=ts_us,
                        )
                        try:
                            self.queu.put_nowait(sample)
                        except asyncio.QueueFull:
                            pass
                        next_call += period
                        now = time.perf_counter()
                        sleep_t = next_call - now

                        if sleep_t > 0.0005: # Nur schlafen, wenn > 0.5ms Zeit ist
                            await asyncio.sleep(sleep_t)
                        elif sleep_t < -period:
                            # Wir sind mehr als eine Periode im Verzug -> Zeitgeber resetten
                            next_call = now
                        # now = time.perf_counter()
                        # if last == 0.0:
                        #     last = now
                        # dt = now - last
                        # sleep_t = max(0.0, period - dt)
                        # await asyncio.sleep(sleep_t)
                        # last = time.perf_counter()

                self._bus = None

            except asyncio.CancelledError:
                sys.stdout.write(f"{LOG_PREFIX} Accel-Streaming stopped\n")
                break

            except Exception as e:
                sys.stdout.write(f"{LOG_PREFIX} Accel-Error: {e}\n")
                self._bus = None

                if self._stop.is_set():
                    break

                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, max_backoff)
