# Real-Time Multisensor Fusion

A Raspberry Pi based prototype that acquires EMG data over Bluetooth Low Energy (BLE) and accelerometer data over I²C, combines both streams, and sends the results to a Flutter app for live visualization.

## How it works

- **Acquisition:** Separate Python services read EMG and accelerometer data.
- **Communication:** The services exchange timestamped samples using gRPC and Protocol Buffers.
- **Fusion:** Two modes are available:
  - **Forwarder:** Outputs the most recent sensor values whenever a new sample arrives.
  - **Feature-based:** Computes EMG RMS and acceleration features in a sliding window at a fixed **1 kHz internal output rate**.
- **Visualization:** Fused data is streamed via BLE/GATT to an Android app built with Flutter and Dart.

## Requirements

- Raspberry Pi 5 running Raspberry Pi OS
- Python 3.11 with I²C and Bluetooth enabled
- BLE EMG sensor and I²C accelerometer
- Flutter 3.24 for the optional Android app

## Quick start

1. Connect the accelerometer via I²C and make the EMG sensor available over BLE.
2. Install the Python dependencies:

   `python3 -m pip install bleak==0.22.3 dbus-next==0.2.3 grpcio==1.67.0 smbus2==0.4.3 protobuf==5.28.2`

3. Configure the sensor and BLE connection settings for your hardware.
4. Start the ACC, EMG, and fusion services using the entry points provided by the project.
5. Launch the Flutter app on an Android device and connect to the Raspberry Pi over BLE.

**Note:** The 1 kHz rate refers to internal feature-based fusion, not to new samples from each sensor. The app stream was tested at 200 Hz.