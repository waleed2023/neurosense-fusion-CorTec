# NeuroSense Fusion

**Real time sensor acquisition, fusion and visualization on a Raspberry Pi**

This project was developed as part of my Bachelor's thesis in Embedded Systems Engineering. It combines EMG and accelerometer data from two different sensor interfaces, processes the signals on a Raspberry Pi and streams the results to an Android application for live visualization.

The focus was on handling sensor streams with different sampling rates, exchanging data between independent services and implementing a processing pipeline suitable for real time visualization.

## System Architecture

The system consists of three main parts:

**1. Sensor acquisition**

- EMG data is received from an external sensor via Bluetooth Low Energy (BLE).
- Accelerometer data is acquired over I²C.
- Each sensor is handled by a separate Python service.

**2. Communication and sensor fusion**

The acquisition services communicate using gRPC and Protocol Buffers. Sensor samples include timestamps to support processing of the different data streams.

Two processing modes are implemented:

- **Forwarder:** Uses the most recent available sensor values whenever a new sample arrives.
- **Feature based fusion:** Calculates EMG RMS and acceleration features using sliding windows, with an internal processing rate of 1 kHz.

**3. Live visualization**

Processed data is transmitted from the Raspberry Pi via BLE/GATT to an Android application developed with Flutter and Dart.

The application displays the incoming signals in real time.

## Technical Challenges

One of the main challenges was working with sensors that deliver data at different rates and through different interfaces.

The implementation separates sensor acquisition from processing and visualization. This makes it possible to handle the individual data streams independently while combining them in a common processing pipeline.

The internal feature fusion operates at 1 kHz. This does not mean that each sensor provides new measurements at that frequency. The BLE connection to the Android application was tested at a streaming rate of 200 Hz.

## Technologies

| Area | Technologies |
|---|---|
| Hardware | Raspberry Pi 5, EMG sensor, accelerometer |
| Programming | Python, Dart |
| Interfaces | BLE, GATT, I²C |
| Communication | gRPC, Protocol Buffers |
| Signal processing | RMS, sliding windows, sensor fusion |
| Visualization | Flutter |

## Getting Started

**Requirements**

- Raspberry Pi 5 with Raspberry Pi OS
- Python 3.11
- I²C and Bluetooth enabled
- Compatible BLE EMG sensor and I²C accelerometer
- Flutter 3.24 for the Android application

**Setup**

Clone the repository and install the Python dependencies using:

`python3 -m pip install -r requirements.txt`

Configure the sensor connections for your hardware, then start the acquisition and fusion services.

The Flutter application can be launched on an Android device to receive and visualize the processed data.

Running the complete system requires the corresponding sensor hardware and configuration.

## Repository Structure

- `app/` – Application and processing code
- `plots/` – Plots and experimental results
- `requirements.txt` – Python dependencies
- `Bachelor_Arbeit_Waleed_Rajab.pdf` – Bachelor's thesis with technical details and evaluation

## Project Background

This project gave me practical experience with embedded Linux, hardware interfaces, asynchronous sensor communication and signal processing. It also involved debugging communication between services and working with real sensor data rather than simulated inputs.

For more details about the implementation and evaluation, see the Bachelor's thesis included in this repository.
