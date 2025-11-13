import os

# BLE
ANALOG_CHAR = os.getenv("ANALOG_CHAR", "00002a58-0000-1000-8000-00805f9b34fb")
BATTERY_CHAR = os.getenv("BATTERY_CHAR", "00002a19-0000-1000-8000-00805f9b34fb")
TARGET_MAC = os.getenv("EMG_TARGET_MAC", "")
TARGET_NAME = os.getenv("EMG_TARGET_NAME", "ANR Corp M40")

# Clamp
VALUE_MAX = int(os.getenv("VALUE_MAX", "1000")) # max value for EMG channels

# gRPC
GRPC_HOST = os.getenv("GRPC_HOST", "0.0.0.0")
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))

# Streaming / Backpressure
CLIENT_QUEUE_MAX = int(os.getenv("CLIENT_QUEUE_MAX", "512"))  # max Samples pro Client
DROP_OLDEST_ON_FULL = os.getenv("DROP_OLDEST_ON_FULL", "1") not in ("0", "false", "False") # default: drop oldest



# ===== I2C / ACCEL (LSM6DSx) =====
I2C_BUS = int(os.getenv("I2C_BUS", "1"))
I2C_ADDR = int(os.getenv("I2C_ADDR", "0x6B"), 16)
ACCEL_ODR_HZ = float(os.getenv("ACCEL_ODR_HZ", "833"))  # Poll-Rate, options: 12.5, 26, 52, 104, 208, 416, 833, 1660
ACCEL_FS_G = int(os.getenv("ACCEL_FS_G", "2")) # scale ±2g, ±4g, ±8g, ±16g, depending on case
LOG_PREFIX = os.getenv("LOG_PREFIX", "[emg]") # Prefix for log messages

