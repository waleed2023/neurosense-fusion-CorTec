import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# -------------------------
# CSV laden (wie bisher)
# -------------------------
CSV_PATH = "timing_metrics.csv"  # <- anpassen
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

df = pd.read_csv(CSV_PATH, header=None, names=COLS)
df = df[df["kind"] == "mark"].copy()
df["idx"] = pd.to_numeric(df["idx"], errors="coerce")
df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["name", "idx", "ts_us"])

groups = {n: g.sort_values(["idx", "ts_us"]) for n, g in df.groupby("name")}
def have(n): return n in groups

# -------------------------
# Datenfunktionen
# -------------------------
def jitter_ms(mark_name: str) -> np.ndarray:
    """Jitter = |Δt - median(Δt)| pro mark, in ms."""
    ts = groups[mark_name]["ts_us"].to_numpy()
    if len(ts) < 2:
        return np.array([])
    dt = np.diff(ts)
    dt = dt[dt > 0]
    if len(dt) == 0:
        return np.array([])
    baseline = np.median(dt)
    return np.abs(dt - baseline) / 1000.0

def delay_ms(entry_mark: str, exit_mark: str) -> np.ndarray:
    """Delay = t_exit - t_entry (über idx gematcht), in ms."""
    a = groups[entry_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_entry"})
    b = groups[exit_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_exit"})
    m = a.merge(b, on="idx", how="inner")
    if m.empty:
        return np.array([])
    d = (m["t_exit"] - m["t_entry"]).to_numpy()
    d = d[d >= 0]
    return d / 1000.0

def take_some(x: np.ndarray, max_points: int = 1500) -> np.ndarray:
    """Für die Punktwolke: nicht alle Punkte plotten, sonst wird’s schwarz."""
    if len(x) <= max_points:
        return x
    idx = np.random.choice(len(x), size=max_points, replace=False)
    return x[idx]

# -------------------------
# Raincloud helper
# -------------------------
def raincloud(ax, data_list, labels, title, ylabel):
    """
    'Raincloud' light:
      - violin: zeigt Dichte
      - box: zeigt median/quartile
      - points: zeigt echte samples (kleine Zufalls-Auswahl)
    """
    positions = np.arange(1, len(data_list) + 1)

    # Violin (Dichte)
    parts = ax.violinplot(data_list, positions=positions, showmeans=False, showmedians=False, showextrema=False)

    # Box (Median/Quartile)
    ax.boxplot(data_list, positions=positions, widths=0.18, showfliers=False)

    # Punkte (subset)
    for i, x in enumerate(data_list, start=1):
        xs = take_some(np.asarray(x))
        # kleines "Jitter" auf der x-Achse, damit Punkte nicht übereinander liegen
        x_pos = i + (np.random.rand(len(xs)) - 0.5) * 0.15
        ax.scatter(x_pos, xs, s=6, alpha=0.25)

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.grid(True, axis="y", alpha=0.2)

# -------------------------
# Was wir plotten wollen (passt zu deiner CSV)
# -------------------------
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
    ("EMG_HAL_ENTRY", "EMG_HAL_OUT", "HAL (ENTRY→OUT)"),
    ("Fusion_Dummy_EMG_TIME_ENTER", "Fusion_Dummy_EMG_Wall_Time_Exit", "Fusion (enter→exit)"),
    ("Fusion_Dummy_EMG_Sensor_Time_Enter", "Fusion_Dummy_EMG_Wall_Time_Exit", "End-to-end (sensor→out)"),
]
ACC_DELAY_PAIRS = [
    ("ACC_HAL_ENTRY", "ACC_HAL_OUT", "HAL (ENTRY→OUT)"),
    ("Fusion_Dummy_ACC_TIME_ENTER", "Fusion_Dummy_ACC_Wall_Time_Exit", "Fusion (enter→exit)"),
    ("Fusion_Dummy_ACC_Sensor_Time_EXIT", "Fusion_Dummy_ACC_Wall_Time_Exit", "End-to-end (sensor→out)"),
]

# -------------------------
# EMG Figure (2 Subplots: Jitter + Delay)
# -------------------------
emg_j_data, emg_j_labels = [], []
for m in EMG_JITTER_MARKS:
    if have(m):
        x = jitter_ms(m)
        if len(x) > 0:
            emg_j_data.append(x)
            emg_j_labels.append(m)

emg_d_data, emg_d_labels = [], []
for a, b, lbl in EMG_DELAY_PAIRS:
    if have(a) and have(b):
        x = delay_ms(a, b)
        if len(x) > 0:
            emg_d_data.append(x)
            emg_d_labels.append(lbl)

fig, ax = plt.subplots(2, 1, figsize=(10, 7))
if emg_j_data:
    raincloud(ax[0], emg_j_data, emg_j_labels, "EMG – Jitter (Raincloud)", "Jitter [ms]")
else:
    ax[0].set_title("EMG – Jitter (keine Daten)")

if emg_d_data:
    raincloud(ax[1], emg_d_data, emg_d_labels, "EMG – Delay (Raincloud)", "Delay [ms]")
else:
    ax[1].set_title("EMG – Delay (keine Daten)")

fig.tight_layout()

# -------------------------
# ACC Figure (2 Subplots: Jitter + Delay)
# -------------------------
acc_j_data, acc_j_labels = [], []
for m in ACC_JITTER_MARKS:
    if have(m):
        x = jitter_ms(m)
        if len(x) > 0:
            acc_j_data.append(x)
            acc_j_labels.append(m)

acc_d_data, acc_d_labels = [], []
for a, b, lbl in ACC_DELAY_PAIRS:
    if have(a) and have(b):
        x = delay_ms(a, b)
        if len(x) > 0:
            acc_d_data.append(x)
            acc_d_labels.append(lbl)

fig, ax = plt.subplots(2, 1, figsize=(10, 7))
if acc_j_data:
    raincloud(ax[0], acc_j_data, acc_j_labels, "ACC – Jitter (Raincloud)", "Jitter [ms]")
else:
    ax[0].set_title("ACC – Jitter (keine Daten)")

if acc_d_data:
    raincloud(ax[1], acc_d_data, acc_d_labels, "ACC – Delay (Raincloud)", "Delay [ms]")
else:
    ax[1].set_title("ACC – Delay (keine Daten)")

fig.tight_layout()

plt.show()
