import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# =========================
# Einstellungen
# =========================
CSV_PATH = "timing_metrics.csv"  # <- anpassen!
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

# Marks (wie in deinem Logging)
EMG_JITTER_MARKS = [
    "EMG_HAL_DATA_WITHOUT_SCALE",
    "EMG_HAL_ENTRY",
    "EMG_HAL_OUT",
    "Fusion_Dummy_EMG_ENTER",
    "Fusion_Dummy_EMG_Exit",
]
ACC_JITTER_MARKS = [
    "ACC_HAL_DATA_WITHOUT_SCALE",
    "ACC_HAL_ENTRY",
    "ACC_HAL_OUT",
    "Fusion_Dummy_ACC_Exit",
    "Fusion_Dummy_ACC_ENTER",
]

EMG_DELAY_PAIRS = [
    ("EMG_HAL_ENTRY", "EMG_HAL_OUT", "HAL (ENTRY→OUT)"),
    ("EMG_HAL_ENTRY", "Fusion_Dummy_EMG_Exit", "End-to-end (HAL→out)"),
]
ACC_DELAY_PAIRS = [
    ("ACC_HAL_ENTRY", "ACC_HAL_OUT", "HAL (ENTRY→OUT)"),
    ("ACC_HAL_ENTRY", "Fusion_Dummy_ACC_Exit", "End-to-end (HAL→out)"),
]

MAX_SCATTER_POINTS = 5000  # damit es nicht komplett schwarz wird

# =========================
# CSV laden + vorbereiten
# =========================
df = pd.read_csv(CSV_PATH, header=None, names=COLS)
df = df[df["kind"] == "mark"].copy()

df["idx"] = pd.to_numeric(df["idx"], errors="coerce")
df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["name", "idx", "ts_us"])

# Pro Markname gruppieren
groups = {n: g.sort_values(["idx", "ts_us"]) for n, g in df.groupby("name")}

def have(n: str) -> bool:
    return n in groups and len(groups[n]) > 0

# =========================
# Metriken
# =========================
def jitter_us(mark_name: str) -> np.ndarray:
    """Jitter = |Δt - baseline| in µs."""
    ts = groups[mark_name]["ts_us"].to_numpy()
    if len(ts) < 2:
        return np.array([])
    dt = np.diff(ts)
    dt = dt[dt > 0]
    if len(dt) == 0:
        return np.array([])
    #baseline = np.median(dt)
    #baseline = np.average(dt)
    return dt

def jitter_ms(mark_name: str) -> np.ndarray:
    return jitter_us(mark_name) / 1000.0

def delay_us(entry_mark: str, exit_mark: str) -> np.ndarray:
    """
    Delay = t_exit - t_entry in µs.
    """
    # 1) idx-match (wie bisher)
    a = groups[entry_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_entry"})
    b = groups[exit_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_exit"})
    m = a.merge(b, on="idx", how="inner")

    if not m.empty:
        d = (m["t_exit"] - m["t_entry"]).to_numpy()
        d = d[d >= 0]
        if len(d) > 0:
            return d

    # 2) Fallback: zeitbasiert matchen (letzter Entry vor Exit)
    a_t = groups[entry_mark][["ts_us"]].rename(columns={"ts_us": "t_entry"}).sort_values("t_entry")
    b_t = groups[exit_mark][["ts_us"]].rename(columns={"ts_us": "t_exit"}).sort_values("t_exit")

    m2 = pd.merge_asof(
        b_t, a_t,
        left_on="t_exit",
        right_on="t_entry",
        direction="backward"   # letzter entry <= exit
    ).dropna(subset=["t_entry"])

    d = (m2["t_exit"] - m2["t_entry"]).to_numpy()
    return d[d >= 0]

def delay_ms(entry_mark: str, exit_mark: str) -> np.ndarray:
    return delay_us(entry_mark, exit_mark) / 1000.0

# =========================
# Plot-Helpers (Raincloud)
# =========================
def take_some(x: np.ndarray, max_points: int = MAX_SCATTER_POINTS) -> np.ndarray:
    x = np.asarray(x)
    if len(x) <= max_points:
        return x
    idx = np.random.choice(len(x), size=max_points, replace=False)
    return x[idx]

def raincloud(ax, data_list, labels, title, ylabel):
    positions = np.arange(1, len(data_list) + 1)

    ax.violinplot(
        data_list, positions=positions,
        showmeans=False, showmedians=False, showextrema=False
    )
    ax.boxplot(data_list, positions=positions, widths=0.20, showfliers=False)

    for i, x in enumerate(data_list, start=1):
        xs = take_some(np.asarray(x))
        x_pos = i + (np.random.rand(len(xs)) - 0.5) * 0.15
        ax.scatter(x_pos, xs, s=6, alpha=0.25)

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.grid(True, axis="y", alpha=0.2)

def plot_one_window(data, labels, title, ylabel, figsize=(10, 4.8)):
    fig = plt.figure(figsize=figsize)
    ax = fig.gca()

    if not data:
        ax.set_title(title + " (keine Daten)")
        ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", alpha=0.2)
    else:
        raincloud(ax, data, labels, title, ylabel)

    fig.tight_layout()
    return fig

# =========================
# Daten sammeln
# =========================
# EMG in ms
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

# ACC in µs
acc_j_data, acc_j_labels = [], []
for m in ACC_JITTER_MARKS:
    if have(m):
        x = jitter_us(m)
        if len(x) > 0:
            acc_j_data.append(x)
            acc_j_labels.append(m)

acc_d_data, acc_d_labels = [], []
for a, b, lbl in ACC_DELAY_PAIRS:
    if have(a) and have(b):
        x = delay_us(a, b)
        if len(x) > 0:
            acc_d_data.append(x)
            acc_d_labels.append(lbl)

# =========================
# 4 getrennte Fenster
# =========================
plot_one_window(emg_j_data, emg_j_labels, "EMG – Jitter (Raincloud)", "Jitter [ms]")
plot_one_window(emg_d_data, emg_d_labels, "EMG – Delay (Raincloud)",  "Delay [ms]")

plot_one_window(acc_j_data, acc_j_labels, "ACC – Jitter (Raincloud)", "Jitter [µs]")
plot_one_window(acc_d_data, acc_d_labels, "ACC – Delay (Raincloud)",  "Delay [µs]")

#plt.show()

