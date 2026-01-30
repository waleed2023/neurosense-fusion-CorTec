import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# -------------------------
# Settings
# -------------------------
CSV_PATH = "timing_metrics.csv"
WHERE_STEP = "post"                  # "post" oder "pre"
NORMALIZE_TO_FIRST_PERCENT = True    # so wie dein Screenshot ("Change [%]")

# -------------------------
# CSV load
# -------------------------
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]
df = pd.read_csv(CSV_PATH, header=None, names=COLS)

# nur marks
df = df[df["kind"] == "mark"].copy()
df["idx"] = pd.to_numeric(df["idx"], errors="coerce")
df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["name", "idx", "ts_us"])

if df.empty:
    print("CSV enthält keine gültigen 'mark' Zeilen oder Spalten passen nicht.")
    raise SystemExit(1)

names = sorted(df["name"].unique())
print("\n--- Available marks (unique) ---")
print("\n".join(names[:80]))
if len(names) > 80:
    print(f"... ({len(names)} total)")

groups = {n: g.sort_values(["idx", "ts_us"]) for n, g in df.groupby("name")}
def have(n): return n in groups

# -------------------------
# Helpers
# -------------------------
def pick_first(candidates):
    for c in candidates:
        if have(c):
            return c
    return None

def to_percent_change(y):
    if not NORMALIZE_TO_FIRST_PERCENT:
        return y
    if len(y) == 0:
        return y
    y0 = y[0]
    if y0 == 0:
        return y * 0
    return (y - y0) / y0 * 100.0

def stretch_hold_to_length(y, target_len):
    """
    Sample-and-hold Stretch:
    - macht y auf target_len
    - kein lineares interpolieren, sondern "letzter Wert halten"
    """
    y = np.asarray(y)
    if len(y) == 0:
        return np.zeros(target_len)

    if len(y) == target_len:
        return y

    old_pos = np.linspace(0.0, 1.0, len(y))
    new_pos = np.linspace(0.0, 1.0, target_len)

    idx = np.searchsorted(old_pos, new_pos, side="right") - 1
    idx = np.clip(idx, 0, len(y) - 1)
    return y[idx]

# -------------------------
# Data builders (µs)
# -------------------------
def jitter_values_us(mark_name):
    """
    Jitter = |Δt - median(Δt)| aus einem Mark (ts_us)
    Returns: y (µs)
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
    return np.abs(dt - baseline)  # µs

def delay_values_us(entry_mark, exit_mark):
    """
    Delay = t_exit - t_entry (matched by idx)
    Returns: y (µs)
    """
    a = groups[entry_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_entry"})
    b = groups[exit_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_exit"})
    m = a.merge(b, on="idx", how="inner").sort_values("idx")
    if m.empty:
        return np.array([])
    d = (m["t_exit"] - m["t_entry"]).to_numpy()
    d = d[d >= 0]
    return d  # µs

# -------------------------
# Plot (stepped + end labels) with SAME X length
# -------------------------
def stepped_compare_same_x(title, ylabel, series_y):
    """
    series_y = [(y_array, label), ...]
    -> alle Reihen auf gleiche Länge (100%)
    """
    series_y = [(np.asarray(y), label) for (y, label) in series_y if len(y) > 0]

    if not series_y:
        print(f"[skip] Nothing to plot for: {title}")
        return

    target_len = max(len(y) for y, _ in series_y)
    x = np.arange(target_len)

    fig, ax = plt.subplots(figsize=(12, 5))

    for y, label in series_y:
        y = to_percent_change(y)
        y = stretch_hold_to_length(y, target_len)

        line = ax.step(x, y, where=WHERE_STEP, linewidth=1.8)[0]
        color = line.get_color()

        suffix = "%" if NORMALIZE_TO_FIRST_PERCENT else "µs"
        ax.text(
            x[-1], y[-1],
            f"  {label} ({y[-1]:.0f}{suffix})",
            color=color, va="center", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=color, alpha=0.9)
        )

    ax.set_title(title)
    ax.set_xlabel("Sample # (gemeinsame Länge = 100%)")
    ax.set_ylabel("Change [%]" if NORMALIZE_TO_FIRST_PERCENT else ylabel)
    ax.grid(True, alpha=0.25)
    ax.ticklabel_format(style="plain", axis="y", useOffset=False)
    ax.set_xlim(0, target_len * 1.06)
    fig.tight_layout()

# -------------------------
# Auto-pick marks (robust)
# -------------------------
EMG_JITTER_MARK = pick_first(["EMG_HAL_OUT", "EMG_HAL_ENTRY", "EMG_HAL_DATA_WITHOUT_SCALE"])
ACC_JITTER_MARK = pick_first(["ACC_HAL_OUT", "ACC_HAL_ENTRY", "ACC_HAL_DATA_WITHOUT_SCALE"])

EMG_DELAY_ENTRY = pick_first(["Fusion_Dummy_EMG_Sensor_Time_Enter", "Fusion_Dummy_EMG_Sensor_Time_EXIT", "EMG_HAL_ENTRY"])
EMG_DELAY_EXIT  = pick_first(["Fusion_Dummy_EMG_Wall_Time_Exit", "Fusion_Dummy_EMG_Wall_Time_EXIT", "EMG_HAL_OUT"])

ACC_DELAY_ENTRY = pick_first(["Fusion_Dummy_ACC_Sensor_Time_EXIT", "Fusion_Dummy_ACC_Sensor_Time_Enter", "ACC_HAL_ENTRY"])
ACC_DELAY_EXIT  = pick_first(["Fusion_Dummy_ACC_Wall_Time_Exit", "Fusion_Dummy_ACC_Wall_Time_EXIT", "ACC_HAL_OUT"])

print("\n--- Chosen marks ---")
print("EMG jitter:", EMG_JITTER_MARK)
print("ACC jitter:", ACC_JITTER_MARK)
print("EMG delay :", EMG_DELAY_ENTRY, "->", EMG_DELAY_EXIT)
print("ACC delay :", ACC_DELAY_ENTRY, "->", ACC_DELAY_EXIT)

# -------------------------
# Build series + Plot
# -------------------------
# 1) Jitter plot
jy_emg = jitter_values_us(EMG_JITTER_MARK) if EMG_JITTER_MARK else np.array([])
jy_acc = jitter_values_us(ACC_JITTER_MARK) if ACC_JITTER_MARK else np.array([])

print(f"\nJitter sizes: EMG={len(jy_emg)}  ACC={len(jy_acc)}")

stepped_compare_same_x(
    "Jitter comparison (stepped line) – EMG vs ACC",
    "Jitter [µs]",
    [(jy_emg, "EMG jitter"), (jy_acc, "ACC jitter")]
)

# 2) Delay plot
dy_emg = delay_values_us(EMG_DELAY_ENTRY, EMG_DELAY_EXIT) if (EMG_DELAY_ENTRY and EMG_DELAY_EXIT) else np.array([])
dy_acc = delay_values_us(ACC_DELAY_ENTRY, ACC_DELAY_EXIT) if (ACC_DELAY_ENTRY and ACC_DELAY_EXIT) else np.array([])

print(f"Delay sizes : EMG={len(dy_emg)}  ACC={len(dy_acc)}")

stepped_compare_same_x(
    "Delay comparison (stepped line) – EMG vs ACC",
    "Delay [µs]",
    [(dy_emg, "EMG delay"), (dy_acc, "ACC delay")]
)

plt.show()
