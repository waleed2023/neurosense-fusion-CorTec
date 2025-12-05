import csv

FN = "sensor_timing_metrics.csv"

ts = []

with open(FN, newline="", encoding="utf-8") as f:
    reader = csv.reader(f)
    for row in reader:
        if not row:
            continue
        kind, name = row[0], row[1]
        if kind != "jitter":
            continue
        if name != "EMG_SENSOR":
            continue

        ts_ms = float(row[3])  # Spalte ts_ms
        ts.append(ts_ms)

if len(ts) < 2:
    print("Zu wenig ACC_SENSOR-Daten gefunden.")
else:
    duration_ms = ts[-1] - ts[0]          # Messdauer
    n_intervals = len(ts) - 1             # N-1 Intervalle
    rate_hz = n_intervals * 1000.0 / duration_ms
    print(f"Effektive EMG Sensor -> Pi Rate: {rate_hz:.2f} Hz")
