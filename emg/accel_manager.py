import math
import time
import asyncio
import sys
from typing import Optional

from smbus2 import SMBus

from .config import (
    I2C_BUS, I2C_ADDR, ACCEL_ODR_HZ, ACCEL_FS_G, LOG_PREFIX, WHOAMI_EXPECTED,
    REG_CTRL1_XL, REG_CTRL2_G, REG_CTRL3_C, REG_WHO_AM_I, OUTX_L_XL,
)
from .proto import emg_pb2


# LSM6DSx Register Map
def to_s16(lo, hi):
    v = (hi << 8) | lo
    return v - 65536 if v > 32767 else v

def scale_factor_g(fs_g: int) -> float:
    """Return scale factor to convert raw accel value to g based on full-scale setting."""
    if fs_g == 2:
        return 0.000061
    if fs_g == 4:
        return 0.000122
    if fs_g == 8:
        return 0.000244
    if fs_g == 16:
        return 0.000488
    return 0.000061  # default 2g

class AccelManager:
    """
    Polls the LSM6DSx via I²C and publishes AccelSample via the Broadcaster.
    Runs as an asyncio task; smbus access is synchronous, so polling is done in the loop.
    """
    def __init__(self, broadcaster):
        self.broadcaster = broadcaster
        self._stop = asyncio.Event()
        self._bus: Optional[SMBus] = None

    async def stop(self):
        self._stop.set()

    def _init_sensor(self, bus: SMBus, addr: int):
        # Soft-Reset
        bus.write_byte_data(addr, REG_CTRL3_C, 0x01)
        time.sleep(0.05)
        # IF_INC=1 (Auto-Increment), BDU=1
        bus.write_byte_data(addr, REG_CTRL3_C, 0b01000100)

        # Accel ODR (approx) + FS
        # ODR Bits: 0x10=12.5Hz, 0x20=26Hz, 0x30=52Hz, 0x40=104Hz, 0x50=208Hz, 0x60=416Hz, 0x70=833Hz, 0x80=1.66k
        odr_bits = 0x40  # default 104Hz
        if   ACCEL_ODR_HZ >= 1660: odr_bits = 0x80
        elif ACCEL_ODR_HZ >= 833:  odr_bits = 0x70
        elif ACCEL_ODR_HZ >= 416:  odr_bits = 0x60
        elif ACCEL_ODR_HZ >= 208:  odr_bits = 0x50
        elif ACCEL_ODR_HZ >= 104:  odr_bits = 0x40
        elif ACCEL_ODR_HZ >= 52:   odr_bits = 0x30
        else:                      odr_bits = 0x10

        # FS Bits (CTRL1_XL[5:4]): 00=±2g, 10=±4g, 11=±8g, 01=±16g (depending on data sheet variant)
        fs_bits = 0x00
        if ACCEL_FS_G == 4:  fs_bits = 0x10
        if ACCEL_FS_G == 8:  fs_bits = 0x18
        if ACCEL_FS_G == 16: fs_bits = 0x08

        bus.write_byte_data(addr, REG_CTRL1_XL, odr_bits | fs_bits)

        # Disable gyro
        # for now we don't use the gyro, so turn it off to save power
        bus.write_byte_data(addr, REG_CTRL2_G, 0x00)

        who = bus.read_byte_data(addr, REG_WHO_AM_I)
        if who not in WHOAMI_EXPECTED:
            sys.stderr.write(f"{LOG_PREFIX} Warning: WHO_AM_I=0x{who:02X}\n")

    def _read_accel_g(self, bus: SMBus, addr: int):
        """Read accel data, return AX, AY, AZ in g and magnitude MAG."""


        a = bus.read_i2c_block_data(addr, OUTX_L_XL, 6)
        ax = to_s16(a[0], a[1])
        ay = to_s16(a[2], a[3])
        az = to_s16(a[4], a[5])
        sf = scale_factor_g(ACCEL_FS_G)
        AX = ax * sf
        AY = ay * sf
        AZ = az * sf
        MAG = math.sqrt(AX*AX + AY*AY + AZ*AZ)
        return AX, AY, AZ, MAG

    async def run(self):
        period = 1.0 / max(1.0, ACCEL_ODR_HZ)
        try:
            with SMBus(I2C_BUS) as bus:
                self._bus = bus
                self._init_sensor(bus, I2C_ADDR)
                sys.stdout.write(f"{LOG_PREFIX} Accel-Streaming started (I2C bus={I2C_BUS} addr=0x{I2C_ADDR:02X})\n")
                last = 0.0
                while not self._stop.is_set():
                    # Poll
                    AX, AY, AZ, MAG = self._read_accel_g(bus, I2C_ADDR)
                    sample = emg_pb2.AccelSample(ax=AX, ay=AY, az=AZ, mag=MAG, ts_ms=int(time.time()*1000))  # send AccelSample to Broadcaster
                    await self.broadcaster.publish(sample)

                    # Sleep to achieve approx. ODR_HZ
                    now = time.perf_counter()
                    if last == 0.0:
                        last = now
                    dt = now - last
                    sleep_t = max(0.0, period - dt)
                    await asyncio.sleep(sleep_t)
                    last = time.perf_counter()
        except asyncio.CancelledError:
            sys.stdout.write(f"{LOG_PREFIX} Accel-Streaming stopped\n")
            pass
        except Exception as e:
            sys.stdout.write(f"{LOG_PREFIX} Accel-Error: {e}\n")
