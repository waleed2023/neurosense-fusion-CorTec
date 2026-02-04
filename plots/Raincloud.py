import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.legend_handler import HandlerTuple


# =========================
# Einstellungen
# =========================
CSV_PATH = "acc_hal.csv"  # <- anpassen!
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

# Marks (wie in deinem Logging)
EMG_JITTER_MARKS = [
    "EMG_HAL_ENTRY"
]

EMG_JITTER_FUSION_MARKS = [
    "Fusion_Dummy_EMG_ENTER",
    "Fusion_Dummy_EMG_Exit",
]
ACC_JITTER_MARKS = [
    "ACC_HAL_ENTRY",
    "ACC_HAL_OUT"
]

ACC_JITTER_FUSION_MARKS = [
    "Fusion_Dummy_ACC_ENTER",
    "Fusion_Dummy_ACC_Exit",
]
EMG_DELAY_PAIRS = [
    ("EMG_HAL_ENTRY", "EMG_HAL_OUT", "HAL (ENTRY→OUT)"),
    ("EMG_HAL_ENTRY", "Fusion_Dummy_EMG_Exit", "End-to-end (HAL→out)"),
]
ACC_DELAY_PAIRS = [
    ("ACC_HAL_ENTRY", "ACC_HAL_OUT", "HAL (ENTRY→OUT)"),
    ("ACC_HAL_ENTRY", "Fusion_Dummy_ACC_Exit", "End-to-end (HAL→out)"),
]


DISPLAY = {
    # EMG jitter
    "EMG_HAL_ENTRY": "EMG HAL Entry",

    # EMG delay labels (hier nutzt du schon lbl, also optional)
    "HAL (ENTRY→OUT)": "HAL",
    "End-to-end (HAL→out)": "E2E",

    # ACC jitter
    "ACC_HAL_ENTRY": "ACC HAL Entry",

    # Fusion jitter
    "Fusion_Dummy_ACC_ENTER": "Fusion ACC Enter",
    "Fusion_Dummy_ACC_Exit":  "Fusion ACC Exit",
    "Fusion_Dummy_EMG_ENTER": "Fusion EMG Enter",
    "Fusion_Dummy_EMG_Exit":  "Fusion EMG Exit",
}


def disp(label: str) -> str:
    return DISPLAY.get(label, label)  # fallback: original


MAX_SCATTER_POINTS = None  # damit es nicht komplett schwarz wird

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
def _ts_one_per_idx(mark_name: str, pick: str = "first") -> np.ndarray:
    g = groups[mark_name][["idx", "ts_us"]].dropna().sort_values(["idx", "ts_us"])
    gb = g.groupby("idx")["ts_us"]
    if pick == "first":
        s = gb.first()
    elif pick == "last":
        s = gb.last()
    elif pick == "min":
        s = gb.min()
    elif pick == "max":
        s = gb.max()
    else:
        raise ValueError("pick must be first/last/min/max")
    return s.to_numpy()

def jitter_us(mark_name: str) -> np.ndarray:
    """Jitter = |Δt - baseline| in µs (pro idx genau 1 Timestamp)."""
    # ENTRY eher first/min, OUT eher last/max – wichtig: pro mark konsistent!
    pick = "first" if "ENTRY" in mark_name or "ENTER" in mark_name else "last"
    ts = _ts_one_per_idx(mark_name, pick=pick)

    if len(ts) < 2:
        return np.array([])
    dt = np.diff(ts)
    dt = dt[dt > 0]
    if len(dt) == 0:
        return np.array([])

    baseline = np.median(dt)   # robuster als average bei Ausreißern/Restdups
    return np.abs(dt - baseline)


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
    if max_points is None:
        return x
    if len(x) <= max_points:
        return x
    idx = np.random.choice(len(x), size=max_points, replace=False)
    return x[idx]

def raincloud(ax, data_list, labels, title, ylabel, show_points=False):
    positions = np.arange(1, len(data_list) + 1)

    # Mittelwert pro Gruppe (pro Box)
    # --- Legend-Box: Mittelwert + Median pro Gruppe ---
    means   = [np.nanmean(np.asarray(x))   for x in data_list]
    medians = [np.nanmedian(np.asarray(x)) for x in data_list]

    handles = []
    leg_labels = []
    for lab, m, md in zip(labels, means, medians):
        h_mean = Line2D([0], [0], marker="D", linestyle="None", markersize=4)
        h_med  = Line2D([0], [0], linestyle="-", linewidth=1.5)
        handles.append((h_mean, h_med))
        leg_labels.append(f"{disp(lab)}: μ={m:.3f} | med={md:.3f}")


    ax.legend(
        handles, leg_labels,
        handler_map={tuple: HandlerTuple(ndivide=None)},
        loc="upper right",
        frameon=True,
        title="Stats"
    )


    ax.boxplot(
        data_list,
        positions=positions,
        widths=0.08,
        showfliers=False,
        showmeans=True,
        meanline=False,  # <- Marker (nicht Linie)
        meanprops=dict(marker="D", markersize=4),
    )
    

    if show_points:
        for pos, x in zip(positions, data_list):
            xs = take_some(np.asarray(x))
            x_pos = pos + (np.random.rand(len(xs)) - 0.5) * 0.15
            ax.scatter(x_pos, xs, s=2, alpha=0.25, rasterized=True)

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(positions)

    ax.set_xticklabels([disp(lab) for lab in labels], rotation=0, ha="center")

    ax.grid(True, axis="y", alpha=0.2)



# histogram

def plot_hist_window(data_list, labels, title, xlabel, bins="auto", density=False, figsize=(10, 4.8)):
    fig = plt.figure(figsize=figsize)
    ax = fig.gca()

    if not data_list:
        ax.set_title(title + " (keine Daten)")
        ax.set_xlabel(xlabel)
        ax.grid(True, axis="y", alpha=0.2)
        fig.tight_layout()
        return fig

    # Histogramme (überlagert)
    for x, lab in zip(data_list, labels):
        x = np.asarray(x)
        ax.hist(x, bins=bins, density=density, alpha=0.35, label=lab)

        # Stats-Linien pro Gruppe
        mu  = np.nanmean(x)
        med = np.nanmedian(x)
        ax.axvline(mu,  linestyle="--", linewidth=1.2)
        ax.axvline(med, linestyle="-",  linewidth=1.2)

    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Dichte" if density else "Count")
    ax.grid(True, axis="y", alpha=0.2)
    ax.legend(loc="best")

    fig.tight_layout()
    return fig


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

# for fusion
acc_j_fusion_data, acc_j_fusion_label = [], []
for m in ACC_JITTER_FUSION_MARKS:
    if have(m):
        x = jitter_us(m)
        if len(x) > 0:
            acc_j_fusion_data.append(x)
            acc_j_fusion_label.append(m)

# =========================
# 4 getrennte Fenster
# =========================
plot_one_window(emg_j_data, emg_j_labels, "EMG – Jitter (Raincloud)", "Jitter [ms]")
#plot_one_window(emg_d_data, emg_d_labels, "EMG – Delay (Raincloud)",  "Delay [ms]")

plot_one_window(acc_j_data, acc_j_labels, "ACC – Jitter (Raincloud)", "Jitter [µs]")
#plot_one_window(acc_d_data, acc_d_labels, "ACC – Delay (Raincloud)",  "Delay [µs]")

plot_one_window(acc_j_fusion_data, acc_j_fusion_label, "Forwarder Fusion", "Jitter [us]")



# plot_hist_window(emg_j_data, emg_j_labels, "EMG – Jitter (Histogram)", "Jitter [ms]")
# plot_hist_window(emg_d_data, emg_d_labels, "EMG – Delay (Histogram)",  "Delay [ms]")

# plot_hist_window(acc_j_data, acc_j_labels, "ACC – Jitter (Histogram)", "Jitter [µs]")
# plot_hist_window(acc_d_data, acc_d_labels, "ACC – Delay (Histogram)",  "Delay [µs]")

# plot_hist_window(acc_j_fusion_data, acc_j_fusion_label, "Forwarder Fusion (Histogram)", "Jitter [µs]")
plt.show()

