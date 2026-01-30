import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# =========================
# 1) CSV laden
# =========================
CSV_PATH = "timing_metrics.csv"   # <- ggf. anpassen
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

df = pd.read_csv(CSV_PATH, header=None, names=COLS)
df = df[df["kind"] == "mark"].copy()

df["idx"] = pd.to_numeric(df["idx"], errors="coerce")
df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["name", "idx", "ts_us"])

groups = {name: g.sort_values(["idx", "ts_us"]) for name, g in df.groupby("name")}
def has_mark(name: str) -> bool:
    return name in groups

# =========================
# 2) Jitter & Delay
# =========================
def jitter_ms(mark_name: str) -> np.ndarray:
    """
    Jitter = |Δt - median(Δt)|, in ms
    """
    ts = groups[mark_name]["ts_us"].to_numpy()
    if len(ts) < 2:
        return np.array([])

    dt = np.diff(ts)
    dt = dt[dt > 0]
    if len(dt) == 0:
        return np.array([])

    baseline = np.median(dt)
    return np.abs(dt - baseline) / 1000.0


def delay_ms(entry_mark: str, out_mark: str) -> np.ndarray:
    """
    Delay = t_out - t_entry (gleiche idx), in ms
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
# 3) Marks / Paare (aus deiner CSV passend)
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
# 4) Plot-Helfer
# =========================
def plot_hist(data, title, xlabel, bins=80):
    """
    Einfaches Histogramm (ein Plot).
    """
    plt.figure()
    plt.hist(data, bins=bins)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Count")
    plt.tight_layout()

# =========================
# 5) EMG – Jitter Histograms (pro Mark ein Plot)
# =========================
for m in EMG_JITTER_MARKS:
    if not has_mark(m):
        print(f"[skip] fehlt: {m}")
        continue

    j = jitter_ms(m)
    if len(j) == 0:
        print(f"[skip] zu wenig Daten: {m}")
        continue

    plot_hist(j, title=f"EMG – Jitter Histogram: {m}", xlabel="Jitter [ms]")

# =========================
# 6) EMG – Delay Histograms (pro Paar ein Plot)
# =========================
for a, b, lbl in EMG_DELAY_PAIRS:
    if not (has_mark(a) and has_mark(b)):
        print(f"[skip] fehlt pair: {a} -> {b}")
        continue

    d = delay_ms(a, b)
    if len(d) == 0:
        print(f"[skip] keine Daten: {lbl}")
        continue

    plot_hist(d, title=f"EMG – Delay Histogram: {lbl}", xlabel="Delay [ms]")

# =========================
# 7) ACC – Jitter Histograms (pro Mark ein Plot)
# =========================
for m in ACC_JITTER_MARKS:
    if not has_mark(m):
        print(f"[skip] fehlt: {m}")
        continue

    j = jitter_ms(m)
    if len(j) == 0:
        print(f"[skip] zu wenig Daten: {m}")
        continue

    plot_hist(j, title=f"ACC – Jitter Histogram: {m}", xlabel="Jitter [ms]")

# =========================
# 8) ACC – Delay Histograms (pro Paar ein Plot)
# =========================
for a, b, lbl in ACC_DELAY_PAIRS:
    if not (has_mark(a) and has_mark(b)):
        print(f"[skip] fehlt pair: {a} -> {b}")
        continue

    d = delay_ms(a, b)
    if len(d) == 0:
        print(f"[skip] keine Daten: {lbl}")
        continue

    plot_hist(d, title=f"ACC – Delay Histogram: {lbl}", xlabel="Delay [ms]")

plt.show()
