#!/usr/bin/env python3
"""ACC HAL service (I2C) + gRPC streaming.

Runs on the Raspberry Pi as an independent systemd service.

Responsibilities:
  - Poll accelerometer via AccelManager (HAL).
  - Publish samples to a local Broadcaster.
  - Expose gRPC endpoint emg.Accel/StreamAccel to stream AccelSample to clients
    (e.g., the fusion server).
"""

import asyncio
import os
import signal
import sys
import grpc

from emg.broadcaster import Broadcaster
from emg.accel_manager import AccelManager
from emg.config import LOG_PREFIX, GRPC_HOST, MAX_RATE_HZ_ACC
from emg.proto import emg_pb2, emg_pb2_grpc



async def main_acc_hal_async():
    
