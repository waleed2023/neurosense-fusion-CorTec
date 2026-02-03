import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# =========================
# 1) CSV laden
# =========================
CSV_PATH = "timing_metrics.csv"  # <- ggf. Pfad anpassen
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

df = pd.read_csv(CSV_PATH, header=None, names=COLS)
df = df[df["kind"] == "mark"].copy()

df["idx"] = pd.to_numeric(df["idx"], errors="coerce")
df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["name", "idx", "ts_us"])

# Für schnelleres/lesbares Arbeiten: nach name gruppieren
groups = {name: g.sort_values(["idx", "ts_us"]) for name, g in df.groupby("name")}

def has_mark(name: str) -> bool:
    return name in groups

# =========================
# 2) Jitter & Delay Funktionen
# =========================
def jitter_ms(mark_name: str) -> np.ndarray:
    """
    Jitter pro Mark:
      Δt = diff(timestamps)
      baseline = median(Δt)
      jitter = |Δt - baseline|
    Ergebnis in ms.
    """
    g = groups[mark_name]
    ts = g["ts_us"].to_numpy()

    if len(ts) < 2:
        return np.array([])

    dt = np.diff(ts)
    dt = dt[dt > 0]
    if len(dt) == 0:
        return np.array([])

    baseline = np.median(dt)
    jit_us = np.abs(dt - baseline)
    return jit_us / 1000.0


def delay_ms(entry_mark: str, out_mark: str) -> np.ndarray:
    """
    Delay zwischen zwei Marks:
      delay = t_out - t_entry (gematcht über gleiche idx)
    Ergebnis in ms.
    """
    a = groups[entry_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_entry"})
    b = groups[out_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_out"})

    m = a.merge(b, on="idx", how="inner")
    if m.empty:
        return np.array([])

    d_us = (m["t_out"] - m["t_entry"]).to_numpy()
    d_us = d_us[d_us >= 0]
    return d_us / 1000.0

# =========================
# 3) Welche Marks sollen in EMG/ACC rein?
#    (in sinnvoller Reihenfolge)
# =========================
EMG_JITTER_MARKS = [
    "EMG_HAL_DATA_WITHOUT_SCALE",
    "EMG_HAL_ENTRY",
    "EMG_HAL_OUT",
    "Fusion_Dummy_EMG_Sensor_Time_Enter",
    "Fusion_Dummy_EMG_TIME_ENTER",
    "Fusion_Dummy_EMG_Wall_Time_Exit",
]

ACC_JITTER_MARKS = [
    "ACC_HAL_DATA_WITHOUT_SCALE",
    "ACC_HAL_ENTRY",
    "ACC_HAL_OUT",
    "Fusion_Dummy_ACC_Sensor_Time_EXIT",
    "Fusion_Dummy_ACC_TIME_ENTER",
    "Fusion_Dummy_ACC_Wall_Time_Exit",
]

EMG_DELAY_PAIRS = [
    ("EMG_HAL_ENTRY", "EMG_HAL_OUT", "EMG: HAL (ENTRY → OUT)"),
    ("Fusion_Dummy_EMG_TIME_ENTER", "Fusion_Dummy_EMG_Wall_Time_Exit", "EMG: Fusion dummy (enter → exit)"),
    ("Fusion_Dummy_EMG_Sensor_Time_Enter", "Fusion_Dummy_EMG_Wall_Time_Exit", "EMG: end-to-end (sensor ts → fusion out)"),
]

ACC_DELAY_PAIRS = [
    ("ACC_HAL_ENTRY", "ACC_HAL_OUT", "ACC: HAL (ENTRY → OUT)"),
    ("Fusion_Dummy_ACC_TIME_ENTER", "Fusion_Dummy_ACC_Wall_Time_Exit", "ACC: Fusion dummy (enter → exit)"),
    ("Fusion_Dummy_ACC_Sensor_Time_EXIT", "Fusion_Dummy_ACC_Wall_Time_Exit", "ACC: end-to-end (sensor ts → fusion out)"),
]

# =========================
# 4) Plot-Helfer (einfach)
# =========================
def plot_boxplot(data_list, labels, title, ylabel):
    plt.figure()
    plt.boxplot(data_list, showfliers=True)
    plt.xticks(range(1, len(labels) + 1), labels, rotation=35, ha="right")
    plt.title(title)
    plt.ylabel(ylabel)
    plt.tight_layout()

# =========================
# 5) EMG: Jitter (ein Plot)
# =========================
emg_jitter_data = []
emg_jitter_labels = []

for m in EMG_JITTER_MARKS:
    if has_mark(m):
        j = jitter_ms(m)
        if len(j) > 0:
            emg_jitter_data.append(j)
            emg_jitter_labels.append(m)

if emg_jitter_data:
    plot_boxplot(emg_jitter_data, emg_jitter_labels,
                 title="EMG – Jitter pro Messpunkt",
                 ylabel="Jitter [ms] (|Δt − median(Δt)|)")
else:
    print("Keine EMG-Jitter-Daten gefunden.")

# =========================
# 6) EMG: Delay (ein Plot)
# =========================
emg_delay_data = []
emg_delay_labels = []

for a, b, lbl in EMG_DELAY_PAIRS:
    if has_mark(a) and has_mark(b):
        d = delay_ms(a, b)
        if len(d) > 0:
            emg_delay_data.append(d)
            emg_delay_labels.append(lbl)

if emg_delay_data:
    plot_boxplot(emg_delay_data, emg_delay_labels,
                 title="EMG – Delay pro Strecke",
                 ylabel="Delay [ms]")
else:
    print("Keine EMG-Delay-Daten gefunden.")

# =========================
# 7) ACC: Jitter (ein Plot)
# =========================
acc_jitter_data = []
acc_jitter_labels = []

for m in ACC_JITTER_MARKS:
    if has_mark(m):
        j = jitter_ms(m)
        if len(j) > 0:
            acc_jitter_data.append(j)
            acc_jitter_labels.append(m)

if acc_jitter_data:
    plot_boxplot(acc_jitter_data, acc_jitter_labels,
                 title="ACC – Jitter pro Messpunkt",
                 ylabel="Jitter [ms] (|Δt − median(Δt)|)")
else:
    print("Keine ACC-Jitter-Daten gefunden.")

# =========================
# 8) ACC: Delay (ein Plot)
# =========================
acc_delay_data = []
acc_delay_labels = []

for a, b, lbl in ACC_DELAY_PAIRS:
    if has_mark(a) and has_mark(b):
        d = delay_ms(a, b)
        if len(d) > 0:
            acc_delay_data.append(d)
            acc_delay_labels.append(lbl)

if acc_delay_data:
    plot_boxplot(acc_delay_data, acc_delay_labels,
                 title="ACC – Delay pro Strecke",
                 ylabel="Delay [ms]")
else:
    print("Keine ACC-Delay-Daten gefunden.")

plt.show()
