#!/usr/bin/env python3
"""Fusion server: gRPC clients (EMG+ACC) + FusionManager + BLE ProtoBLE (GATT/Adv).

Runs on the Raspberry Pi as a third service.

Responsibilities:
  - Connect to the two HAL services via gRPC streams:
      * server_BLE_HAL  -> app.manager/StreamEmg
      * server_ACC_HAL  -> app.manager/StreamAccel
  - Feed received samples into local Broadcasters.
  - Run FusionManager to compute FusedSample and publish to fusion broadcaster.
  - Expose BLE GATT service (Fusion) + advertisement via BlueZ D-Bus, similar to server_main.py.
"""

import asyncio
import os
import signal
import sys
import grpc

from dbus_next.aio import MessageBus
from dbus_next import BusType

from app.manager.broadcaster import Broadcaster
from app.manager.config import LOG_PREFIX, MAX_RATE_HZ_EMG, MAX_RATE_HZ_ACC, FUSION_RET
from app.fusion.fusion_manager import FusionManager
from app.servers.gatt_core import BleGattApp
from app.servers.fusion_service import build_fusion_service
from app.proto import sensor_pb2, sensor_pb2_grpc
