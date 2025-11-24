import os

# BLE
ANALOG_CHAR = os.getenv("ANALOG_CHAR", "00002a58-0000-1000-8000-00805f9b34fb")
BATTERY_CHAR = os.getenv("BATTERY_CHAR", "00002a19-0000-1000-8000-00805f9b34fb")
TARGET_MAC = os.getenv("EMG_TARGET_MAC", "")
TARGET_NAME = os.getenv("EMG_TARGET_NAME", "ANR Corp M40")

# clamp VALUE_MAX to reasonable range
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

# LSM6DSx Register
REG_FUNC_CFG_ACCESS = 0x01
REG_WHO_AM_I        = 0x0F
REG_CTRL1_XL        = 0x10
REG_CTRL2_G         = 0x11
REG_CTRL3_C         = 0x12
OUTX_L_XL           = 0x28
WHOAMI_EXPECTED = {0x6A, 0x6B, 0x69} # possible values for LSM6DS3, LSM6DS4, LSM6DSO


# ===== Streaming / Packet Sizes =====
MAX_SAMPLES_PER_PACKET_EMG = int(os.getenv("MAX_SAMPLES_PER_PACKET", "64"))  # max samples per packet in streaming services
MAX_WAIT_S_EMG = float(os.getenv("MAX_WAIT_S", "0.02"))  # max wait time to gather more samples into one packet
MAX_SAMPLES_PER_PACKET_ACCEL = int(os.getenv("MAX_SAMPLES_PER_PACKET_ACCEL", "128"))
MAX_WAIT_S_ACCEL = float(os.getenv("MAX_WAIT_S_ACCEL", "0.02"))
MAX_SAMPLES_PER_PACKET_FUSION = int(os.getenv("MAX_SAMPLES_PER_PACKET_FUSION", "32"))
MAX_WAIT_S_FUSION = float(os.getenv("MAX_WAIT_S_FUSION", "0.05"))

# ===== Fusion =====
WINDOW_MS = int(os.getenv("FUSION_WINDOW_MS", "550"))  # time window for fusion in ms
STEP_MS = int(os.getenv("FUSION_STEP_MS", "10"))      # step size for fusion in ms
MAX_DELAY_MS = int(os.getenv("FUSION_MAX_DELAY_MS", "120"))  # z.B. 120 ms max allowed delay between EMG and ACC samples

# ===== max rates =====
MAX_RATE_HZ_ACC = int(os.getenv("FUSION_MAX_RATE_HZ", "800"))
MAX_RATE_HZ_EMG = int(os.getenv("FUSION_MAX_RATE_HZ_EMG", "10"))
MAX_RATE_HZ_FUSION = int(os.getenv("FUSION_MAX_RATE_HZ_FUSION", "100"))


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