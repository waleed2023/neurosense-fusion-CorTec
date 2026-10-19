import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# =========================
# Einstellungen
# =========================
CSV_PATH = "timing_metrics_EMG_HALß.csv"  # <- anpassen!
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

EMG_JITTER_MARKS = ["EMG_HAL_ENTRY"]
ACC_JITTER_MARKS = ["ACC_HAL_ENTRY"]

ACC_JITTER_FUSION_MARKS = ["Fusion_Dummy_ACC_ENTER", "Fusion_Feature_Exit"]

DISPLAY = {
    "EMG_HAL_ENTRY": "EMG HAL Entry",
    "ACC_HAL_ENTRY": "Accelerometer HAL Entry",
    "Fusion_Dummy_ACC_ENTER": "Fusion ACC Enter",
    "Fusion_Feature_Exit": "Fusion Feature Exit",
}

def disp(label: str) -> str:
    return DISPLAY.get(label, label)

MAX_SCATTER_POINTS = None  # None = alle Punkte

# --- NUR für EMG: Doppel-Notify Burst zusammenfassen ---
EMG_DEDUP_WITHIN_US = 5_000  # 5 ms

def dedupe_bursts(ts: np.ndarray, min_gap_us: int) -> np.ndarray:
    """Entfernt aufeinanderfolgende Timestamps, die innerhalb min_gap_us liegen (behält jeweils den ersten)."""
    ts = np.asarray(ts, dtype=np.int64)
    if len(ts) == 0:
        return ts
    kept = [ts[0]]
    last = ts[0]
    for t in ts[1:]:
        if t - last > min_gap_us:
            kept.append(t)
            last = t
        # else: skip (Doppel-Notify / Burst)
    return np.array(kept, dtype=np.int64)

# =========================
# CSV laden + vorbereiten
# =========================
df = pd.read_csv(CSV_PATH, header=None, names=COLS)
df = df[df["kind"] == "mark"].copy()

df["idx"] = pd.to_numeric(df["idx"], errors="coerce")
df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["name", "idx", "ts_us"])

groups = {n: g.sort_values(["idx", "ts_us"]) for n, g in df.groupby("name")}

def have(n: str) -> bool:
    return n in groups and len(groups[n]) > 1

# =========================
# Metriken
# =========================
def _ts_one_per_idx(mark_name: str, pick: str = "first") -> np.ndarray:
    g = groups[mark_name][["idx", "ts_us"]].dropna().sort_values(["idx", "ts_us"])
    gb = g.groupby("idx")["ts_us"]
    if pick == "first":
        s = gb.first()
    elif pick == "last":
        s = gb.last()
    else:
        raise ValueError("pick must be 'first' or 'last'")
    return s.to_numpy()

def jitter_us(mark_name: str) -> np.ndarray:
    """Jitter = |Δt - baseline| in µs (baseline = median(Δt))."""
    pick = "first" if ("ENTRY" in mark_name or "ENTER" in mark_name) else "last"
    ts = _ts_one_per_idx(mark_name, pick=pick)
    if len(ts) < 2:
        return np.array([])

    # >>> FIX: nur EMG-Bursts deduplizieren <<<
    if mark_name in EMG_JITTER_MARKS:
        ts = np.sort(ts.astype(np.int64))
        ts = dedupe_bursts(ts, EMG_DEDUP_WITHIN_US)

    if len(ts) < 2:
        return np.array([])

    dt = np.diff(ts)
    dt = dt[dt > 0]
    return np.abs(dt - np.median(dt))

def jitter_ms(mark_name: str) -> np.ndarray:
    return jitter_us(mark_name) / 1000.0

# =========================
# Plot-Helpers
# =========================
def take_some(x: np.ndarray, max_points: int = MAX_SCATTER_POINTS) -> np.ndarray:
    x = np.asarray(x)
    if max_points is None or len(x) <= max_points:
        return x
    idx = np.random.choice(len(x), size=max_points, replace=False)
    return x[idx]

def raincloud(ax, data_list, labels, title, ylabel, unit: str, show_points=False):
    positions = np.arange(1, len(data_list) + 1)

    ax.boxplot(
        data_list,
        positions=positions,
        widths=0.10,
        showfliers=False,
        showmeans=False,
        meanline=False,
    )

    if show_points:
        for pos, x in zip(positions, data_list):
            xs = take_some(np.asarray(x))
            x_pos = pos + (np.random.rand(len(xs)) - 0.5) * 0.15
            ax.scatter(x_pos, xs, s=2, alpha=0.25, rasterized=True)

    medians = [np.nanmedian(np.asarray(x)) for x in data_list]
    leg_labels = [f"median={md:.3f} {unit}" for md in medians]
    handles = [Line2D([], [], linestyle="None", marker=None, alpha=0) for _ in leg_labels]
    ax.legend(
        handles, leg_labels,
        title="Stats",
        loc="upper right",
        frameon=True,
        handlelength=0,
        handletextpad=0,
        borderpad=0.4,
        labelspacing=0.3,
    )

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(positions)
    ax.set_xticklabels([disp(lab) for lab in labels], rotation=0, ha="center")
    ax.grid(True, axis="y", alpha=0.2)

def plot_one_window(data, labels, title, ylabel, unit, figsize=(10, 4.8)):
    fig = plt.figure(figsize=figsize)
    ax = fig.gca()
    if not data:
        ax.set_title(title + " (keine Daten)")
        ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", alpha=0.2)
    else:
        raincloud(ax, data, labels, title, ylabel, unit=unit, show_points=False)
    fig.tight_layout()
    return fig

# =========================
# Daten sammeln
# =========================
# EMG in ms (mit dedupe fix in jitter_us)
emg_j_data, emg_j_labels = [], []
for m in EMG_JITTER_MARKS:
    if have(m):
        x = jitter_ms(m)
        if len(x) > 0:
            emg_j_data.append(x)
            emg_j_labels.append(m)

# ACC in ms
acc_j_data, acc_j_labels = [], []
for m in ACC_JITTER_MARKS:
    if have(m):
        x = jitter_ms(m)
        if len(x) > 0:
            acc_j_data.append(x)
            acc_j_labels.append(m)

# Fusion/Forwarder in µs
acc_j_fusion_data, acc_j_fusion_label = [], []
for m in ACC_JITTER_FUSION_MARKS:
    if have(m):
        x = jitter_us(m)  # µs
        if len(x) > 0:
            acc_j_fusion_data.append(x)
            acc_j_fusion_label.append(m)

# =========================
# Plots (getrennte Fenster)
# =========================
plot_one_window(emg_j_data, emg_j_labels, "EMG – Jitter", "Jitter [ms]", unit="ms")
plot_one_window(acc_j_data, acc_j_labels, "Accelerometer – Jitter", "Jitter [ms]", unit="ms")
plot_one_window(acc_j_fusion_data, acc_j_fusion_label, "Forwarder Fusion", "Jitter [µs]", unit="µs")

plt.show()
