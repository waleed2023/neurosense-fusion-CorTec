# emg/accel_manager.py
from __future__ import annotations

import math
import time
import asyncio
import sys
from typing import Optional
from collections import deque

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

    # FIFO (LSM6DSOX)
    ACC_USE_FIFO,
    ACC_FIFO_WTM_WORDS,
    ACC_FIFO_READ_WORDS_PER_CALL,
    ACC_FIFO_MAX_DRAIN_WORDS,
    REG_FIFO_CTRL1,
    REG_FIFO_CTRL2,
    REG_FIFO_CTRL3,
    REG_FIFO_CTRL4,
    REG_FIFO_STATUS1,
    REG_FIFO_STATUS2,
    REG_FIFO_DATA_OUT_TAG,
    FIFO_MODE_BYPASS,
    FIFO_MODE_CONTINUOUS,
    FIFO_TAG_ACCEL_NC,
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

        # FIFO raw buffer (x,y,z int16). We keep a small local queue so we can
        # read FIFO in bursts but still return one sample per call.
        self._fifo_buf: deque[tuple[int, int, int]] = deque()

        # Last valid scaled sample (fallback if FIFO empty)
        self._last_scaled: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


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

        # FIFO setup (optional). FIFO allows reading multiple samples in bursts.
        if ACC_USE_FIFO:
            self._fifo_setup(bus, addr)


    def _odr_to_fifo_bdr_xl(self, odr_hz: float) -> int:
        """Map desired accel ODR to FIFO_CTRL3.BDR_XL encoding."""
        # Encodings follow LSM6DSOX datasheet tables (same steps as ODR).
        if odr_hz <= 0:
            return 0x00
        if odr_hz >= 6660:
            return 0x0A
        if odr_hz >= 3330:
            return 0x09
        if odr_hz >= 1660:
            return 0x08
        if odr_hz >= 833:
            return 0x07
        if odr_hz >= 416:
            return 0x06
        if odr_hz >= 208:
            return 0x05
        if odr_hz >= 104:
            return 0x04
        if odr_hz >= 52:
            return 0x03
        if odr_hz >= 26:
            return 0x02
        return 0x01


    def _fifo_setup(self, bus: SMBus, addr: int) -> None:
        """Configure FIFO in continuous mode, batching accelerometer only."""
        # Reset FIFO (bypass)
        bus.write_byte_data(addr, REG_FIFO_CTRL4, FIFO_MODE_BYPASS)

        # Watermark (WTM[8:0]) -> CTRL1 (low) + CTRL2 (bit8)
        wtm = int(ACC_FIFO_WTM_WORDS) & 0x1FF
        bus.write_byte_data(addr, REG_FIFO_CTRL1, wtm & 0xFF)
        bus.write_byte_data(addr, REG_FIFO_CTRL2, (wtm >> 8) & 0x01)

        # FIFO batch data rate: only accelerometer (low nibble), gyro off (high nibble)
        bdr_xl = self._odr_to_fifo_bdr_xl(float(ACCEL_ODR_HZ)) & 0x0F
        fifo_ctrl3 = (0x00 << 4) | bdr_xl
        bus.write_byte_data(addr, REG_FIFO_CTRL3, fifo_ctrl3)

        # Continuous mode
        bus.write_byte_data(addr, REG_FIFO_CTRL4, FIFO_MODE_CONTINUOUS)

        # Clear local buffer
        self._fifo_buf.clear()


    def _fifo_diff_words(self, bus: SMBus, addr: int) -> int:
        """Unread FIFO words (DIFF_FIFO[9:0]) from FIFO_STATUS1/2."""
        s1 = bus.read_byte_data(addr, REG_FIFO_STATUS1) & 0xFF
        s2 = bus.read_byte_data(addr, REG_FIFO_STATUS2) & 0xFF
        return s1 | ((s2 & 0x03) << 8)


    def _fifo_refill(self, bus: SMBus, addr: int) -> None:
        """Read FIFO in bursts and enqueue accel raw samples into self._fifo_buf."""
        diff = self._fifo_diff_words(bus, addr)
        if diff <= 0:
            return

        # Cap work per refill to keep loop responsive
        to_read = min(int(diff), int(ACC_FIFO_MAX_DRAIN_WORDS))

        # SMBus block read is limited (typically 32 bytes), so read in chunks.
        while to_read > 0:
            n_words = min(int(ACC_FIFO_READ_WORDS_PER_CALL), to_read)
            n_bytes = 7 * n_words
            data = bus.read_i2c_block_data(addr, REG_FIFO_DATA_OUT_TAG, n_bytes)

            for i in range(n_words):
                off = i * 7
                tag_byte = data[off]
                tag_sensor = (tag_byte >> 3) & 0x1F  # TAG_SENSOR[4:0]

                # We only enabled accel batching, so this is usually always true.
                if tag_sensor != FIFO_TAG_ACCEL_NC:
                    continue

                x = to_s16(data[off + 1], data[off + 2])
                y = to_s16(data[off + 3], data[off + 4])
                z = to_s16(data[off + 5], data[off + 6])
                self._fifo_buf.append((x, y, z))

            to_read -= n_words


    def _read_accel_g(self, bus: SMBus, addr: int):
        # FIFO path: refill buffer and pop next raw sample
        if ACC_USE_FIFO:
            if not self._fifo_buf:
                self._fifo_refill(bus, addr)

            if self._fifo_buf:
                ax, ay, az = self._fifo_buf.popleft()
            else:
                # FIFO empty -> fallback to last (keeps function signature stable)
                return self._last_scaled
        else:
            a = bus.read_i2c_block_data(addr, OUTX_L_XL, 6)
            ax = to_s16(a[0], a[1])
            ay = to_s16(a[2], a[3])
            az = to_s16(a[4], a[5])

        sf = scale_factor_g(ACCEL_FS_G)
        AX = ax * sf
        AY = ay * sf
        AZ = az * sf
        MAG = math.sqrt(AX * AX + AY * AY + AZ * AZ)
        self._last_scaled = (AX, AY, AZ, MAG)
        return AX, AY, AZ, MAG

    async def run(self):
        period = 1.0 / max(1.0, float(ACCEL_ODR_HZ))
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

                    last = 0.0
                    backoff = 1.0

                    while not self._stop.is_set():
                        # FIFO can deliver multiple samples per poll -> drain a bounded batch
                        n_sent = 0
                        if ACC_USE_FIFO:
                            self._fifo_refill(bus, I2C_ADDR)

                        while n_sent < int(ACC_FIFO_MAX_DRAIN_WORDS):
                            if ACC_USE_FIFO and not self._fifo_buf:
                                break

                            AX, AY, AZ, MAG = self._read_accel_g(bus, I2C_ADDR)

                            self._hal_idx += 1
                            idx = self._hal_idx
                            ts_us = ns_to_us(now_ns())
                            self._marklog.mark("ACC_HAL_ENTRY", idx, ts_us)

                            sample = sensor_pb2.AccelSample(
                                ax=AX,
                                ay=AY,
                                az=AZ,
                                mag=MAG,
                                ts_us=ts_us,
                            )

                            await self.broadcaster.publish(sample)
                            self._marklog.mark("ACC_HAL_OUT", idx, ns_to_us(now_ns()))
                            n_sent += 1
                        
                        now = time.perf_counter()
                        if last == 0.0:
                            last = now
                        dt = now - last
                        sleep_t = max(0.0, period - dt)
                        # If we drained FIFO, just yield; otherwise keep pacing.
                        await asyncio.sleep(0.0 if (ACC_USE_FIFO and n_sent > 0) else sleep_t)
                        last = time.perf_counter()

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
