# emg/fusion/fusion_manager.py
from __future__ import annotations

import asyncio
from typing import Optional, List

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
    Fusion Algorithm Manager with delay windows and raw data steps without futures such as RMS
    and magnitude.
    - starts as soon as *one* sensor delivers samples.
    - if one sensor has no samples in the window -> its output is 0 and valid=false.
    """


    def __init__(
        self,
        emg_broadcaster: Broadcaster,
        acc_broadcaster: Broadcaster,
        fusion_broadcaster: Broadcaster,
        