import os
import time


# micro services configuration
GRPC_HOST = os.getenv("GRPC_HOST", "0.0.0.0")

GRPC_PORT_EMG = int(os.getenv("GRPC_PORT_EMG", "50051"))  # BLE HAL -> Fusion
GRPC_PORT_ACC = int(os.getenv("GRPC_PORT_ACC", "50052"))  # ACC HAL -> Fusion




# BLE
ANALOG_CHAR = os.getenv("ANALOG_CHAR", "00002a58-0000-1000-8000-00805f9b34fb")
BATTERY_CHAR = os.getenv("BATTERY_CHAR", "00002a19-0000-1000-8000-00805f9b34fb")
TARGET_MAC = os.getenv("EMG_TARGET_MAC", "")
TARGET_NAME = os.getenv("EMG_TARGET_NAME", "ANR Corp M40")
EMG_RX_QUEUE_MAX = int(os.getenv("EMG_RX_QUEUE_MAX", "2048")) # for buffering incoming samples
EMG_RX_DROP_OLDEST_ON_FULL = os.getenv("EMG_RX_DROP_OLDEST_ON_FULL", "1") not in ("0", "false", "False") # drop oldest when queue full

# clamp VALUE_MAX to reasonable range
VALUE_MAX = int(os.getenv("VALUE_MAX", "1000")) # max value for EMG channels


# gRPC
GRPC_HOST = os.getenv("GRPC_HOST", "0.0.0.0")
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))

# Streaming / Backpressure
CLIENT_QUEUE_MAX = int(os.getenv("CLIENT_QUEUE_MAX", "1024"))  # max Samples pro Client
DROP_OLDEST_ON_FULL = os.getenv("DROP_OLDEST_ON_FULL", "1") not in ("0", "false", "False") # default: drop oldest



# ===== I2C / ACCEL (LSM6DSx) =====
I2C_BUS = int(os.getenv("I2C_BUS", "1"))
I2C_ADDR = int(os.getenv("I2C_ADDR", "0x6B"), 16)
ACCEL_ODR_HZ = float(os.getenv("ACCEL_ODR_HZ", "833"))  # Poll-Rate, options: 12.5, 26, 52, 104, 208, 416, 833, 1666 ,3332, 6664
ACCEL_FS_G = int(os.getenv("ACCEL_FS_G", "2")) # scale ±2g, ±4g, ±8g, ±16g, depending on case
LOG_PREFIX = os.getenv("LOG_PREFIX", "[emg]") # Prefix for log messages

# LSM6DSx Register
REG_FUNC_CFG_ACCESS = 0x01
REG_WHO_AM_I        = 0x0F
REG_CTRL1_XL        = 0x10
REG_CTRL2_G         = 0x11
REG_CTRL3_C         = 0x12
OUTX_L_XL           = 0x28
WHOAMI_EXPECTED = {0x6A, 0x6B, 0x69, 0x6c} # possible values for LSM6DS3, LSM6DS4, LSM6DSO

REG_CTRL10_C        = 0x19
BIT_TIMESTAMP_EN    = 0x20  # CTRL10_C bit5
REG_TIMESTAMP0      = 0x40  # TIMESTAMP0..3 = 0x40..0x43

TS_TICK_US          = 25    # nominal: 1 tick = 25 µs


# ===== Fusion =====
WINDOW_MS = int(os.getenv("FUSION_WINDOW_MS", "400"))  # time window for fusion in ms
STEP_MS = int(os.getenv("FUSION_STEP_MS", "1"))      # step size for fusion in ms, define 1 -> 1kHz output, 10 -> 100Hz output
MAX_DELAY_MS = int(os.getenv("FUSION_MAX_DELAY_MS", "100"))  # 100 ms max allowed delay between EMG and ACC samples, defines the lookback window into historical data
MAX_DELAY_MS_EMG_DUMMY = int(os.getenv("FUSION_MAX_DELAY_MS_DUMMY", "4000"))  # for dummy fusion manager, allow larger delay
MAX_DELAY_MS_ACC_DUMMY = int(os.getenv("FUSION_MAX_DELAY_MS_DUMMY", "100"))  # for dummy fusion manager, allow larger delay

# ===== max rates just for jitter =====
MAX_RATE_HZ_ACC = int(os.getenv("FUSION_MAX_RATE_HZ", "1000")) 
MAX_RATE_HZ_EMG = int(os.getenv("FUSION_MAX_RATE_HZ_EMG", "50"))
MAX_RATE_HZ_FUSION = int(os.getenv("FUSION_MAX_RATE_HZ_FUSION", "833")) # max output rate of fusion service, yo could hier downsample if needed


# UUIDs
EMG_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1000"
EMG_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1001"  # Write (SubscribeRequest)
EMG_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a1002"  # Notify (EmgSample)

ACC_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2000"
ACC_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2001"  # Write (AccelSubscribeRequest)
ACC_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a2002"  # Notify (AccelSample)

FUSION_SVC   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a3000" 
FUSION_PARAM = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a3001"  # Write (FusionSubscribeRequest)
FUSION_RET   = "f8c94a20-9f1f-4f5a-8ad1-3d1b7c0a3002"  # Notify (FusedSample)



# ---- BlueZ interfaces / constants ----
BLUEZ = "org.bluez"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
OBJM_IFACE = "org.freedesktop.DBus.ObjectManager"
ADAPTER_IFACE = "org.bluez.Adapter1"
GATT_MGR_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHAR_IFACE = "org.bluez.GattCharacteristic1"
LE_ADV_IFACE = "org.bluez.LEAdvertisement1"
LE_ADV_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
DEVICE_IFACE = "org.bluez.Device1"

DEFAULT_APP_PATH = "/org/bluez/protoble"
DEFAULT_ADV_PATH = f"{DEFAULT_APP_PATH}/advertisement0"
DEFAULT_ADAPTER = "/org/bluez/hci0"
# ===== Other Defaults =====
EMG_IDLE_TIMEOUT_MS = int(os.getenv("EMG_IDLE_TIMEOUT_MS", "50000"))  # default 5s
METRICS_LOG_FILE = os.getenv("METRICS_LOG_FILE", "timing_metrics.csv")
# Sensor metrics log file
SENSOR_METRICS_LOG_FILE = os.getenv(
    "SENSOR_METRICS_LOG_FILE",
    "timing_metrics.csv",
)



import time

_EPOCH_OFFSET_NS = time.time_ns() - time.monotonic_ns()

def now_ns() -> int:
    # epoch-like wall clock, but monotonic-stable
    return time.monotonic_ns() + _EPOCH_OFFSET_NS

def ns_to_us(ns: int) -> int:
    return ns // 1_000




# =========================
# LSM6DSOX FIFO (ACC)
# =========================
ACC_USE_FIFO = True

# Watermark optional (wir pollen DIFF_FIFO, Watermark ist hier nur “nice to have”)
ACC_FIFO_WTM_WORDS = 16

# Damit ein run-Loop nicht “zu lange” FIFO drain’t
ACC_FIFO_MAX_DRAIN_WORDS = 64

# SMBus block-read ist i.d.R. auf 32 Bytes begrenzt → 4 FIFO-words = 4*7 = 28 Bytes passt
ACC_FIFO_READ_WORDS_PER_CALL = 4

# FIFO registers (LSM6DSOX)
REG_FIFO_CTRL1 = 0x07
REG_FIFO_CTRL2 = 0x08
REG_FIFO_CTRL3 = 0x09
REG_FIFO_CTRL4 = 0x0A
REG_FIFO_STATUS1 = 0x3A
REG_FIFO_STATUS2 = 0x3B
REG_FIFO_DATA_OUT_TAG = 0x78

# FIFO modes (FIFO_CTRL4.FIFO_MODE[2:0])
FIFO_MODE_BYPASS = 0x00
FIFO_MODE_CONTINUOUS = 0x06   # 0b110 continuous mode

# FIFO TAG (TAG_SENSOR[4:0]) für Accelerometer “NC”
FIFO_TAG_ACCEL_NC = 0x02


# for clent_emu
MEASURE_ONLY = os.getenv("MEASURE_ONLY", "0") == "1"
DISABLE_MARKS = os.getenv("DISABLE_MARKS", "0") == "1"
