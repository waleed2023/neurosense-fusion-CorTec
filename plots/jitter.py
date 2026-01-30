import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ===== Input =====
CSV_PATH = "timing_metrics.csv"

# ===== Tuning =====
MIN_SAMPLES_PER_MARK = 50     # Marks mit weniger Deltas werden übersprungen
SHOW_FLIERS = False            # Outlier anzeigen
# Standard-Boxplot: whiskers = 1.5 * IQR (ruhiger als z.B. 5/95-percentile)
WHIS = 1.5

# ===== Pattern: HAL vs Fusion getrennt (anpassbar!) =====
PAT_ACC_HAL   = r"ACC_HAL"
PAT_EMG_HAL   = r"EMG_HAL"
PAT_ACC_FUSION = r"Fusion.*ACC"   # z.B. "Fusion_Dummy_ACC_..."
PAT_EMG_FUSION = r"Fusion.*EMG"   # z.B. "Fusion_Dummy_EMG_..."

# ===== CSV laden =====
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]
df = pd.read_csv(CSV_PATH, header=None, names=COLS)

df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["ts_us", "name"])
df = df[df["kind"].astype(str).str.lower().eq("mark")]

def compute_jitter_ms_for_mark(mark_name: str):
    ts = df.loc[df["name"] == mark_name, "ts_us"].sort_values().to_numpy()
    if ts.size < 2:
        return None
    dt = np.diff(ts)
    dt = dt[dt > 0]  # nur sinnvolle Deltas
    if dt.size < MIN_SAMPLES_PER_MARK:
        return None
    nominal = np.median(dt)
    jitter_ms = np.abs(dt - nominal) / 1000.0
    return jitter_ms

def names_by_regex(include_pat: str, exclude_pat: str | None = None):
    inc = re.compile(include_pat, re.IGNORECASE)
    exc = re.compile(exclude_pat, re.IGNORECASE) if exclude_pat else None
    out = []
    for n in df["name"].unique():
        s = str(n)
        if inc.search(s) and (exc is None or not exc.search(s)):
            out.append(n)
    return sorted(out)

def build_jitter_df(names):
    rows = []
    for n in names:
        jit = compute_jitter_ms_for_mark(n)
        if jit is None:
            continue
        rows.append(pd.DataFrame({"name": n, "jitter_ms": jit}))
    if not rows:
        return pd.DataFrame(columns=["name", "jitter_ms"])
    return pd.concat(rows, ignore_index=True)

def make_boxplot_figure(jit_df: pd.DataFrame, title: str):
    if jit_df.empty:
        print(f"[WARN] Keine Daten für: {title}")
        return None

    # sortiert nach median jitter
    order = (
        jit_df.groupby("name")["jitter_ms"]
        .median()
        .sort_values()
        .index
        .tolist()
    )

    data = [jit_df.loc[jit_df["name"] == nm, "jitter_ms"].to_numpy() for nm in order]

    fig_h = max(5, 0.33 * len(order))
    fig, ax = plt.subplots(figsize=(12, fig_h))

    ax.boxplot(
        data,
        vert=True,
        labels=order,
        showfliers=SHOW_FLIERS,
        whis=WHIS,
        flierprops=dict(marker='o', markersize=2, linestyle='none'),  # kleine Outlier
        medianprops=dict(linewidth=2),
        boxprops=dict(linewidth=1.2),
        whiskerprops=dict(linewidth=1.2),
        capprops=dict(linewidth=1.2),
    )

    ax.set_title(title)
    ax.set_xlabel("Jitter |Δt − median(Δt)| [ms]")
    ax.grid(True, axis="x", alpha=0.3)

    info = f"Marks={len(order)} | MIN_SAMPLES_PER_MARK={MIN_SAMPLES_PER_MARK} | whis={WHIS} | outliers={SHOW_FLIERS}"
    ax.text(
        0.99, 0.99, info,
        transform=ax.transAxes,
        ha="right", va="top", fontsize=9,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.9, edgecolor="none"),
    )

    return fig

# ===== 4 Gruppen =====
acc_hal_names   = names_by_regex(PAT_ACC_HAL)
emg_hal_names   = names_by_regex(PAT_EMG_HAL)
acc_fus_names   = names_by_regex(PAT_ACC_FUSION, exclude_pat=PAT_ACC_HAL)
emg_fus_names   = names_by_regex(PAT_EMG_FUSION, exclude_pat=PAT_EMG_HAL)

acc_hal_df = build_jitter_df(acc_hal_names)
emg_hal_df = build_jitter_df(emg_hal_names)
acc_fus_df = build_jitter_df(acc_fus_names)
emg_fus_df = build_jitter_df(emg_fus_names)

# ===== 4 Figuren erzeugen (noch NICHT show) =====
make_boxplot_figure(acc_hal_df, "ACC — HAL jitter pro Mark")
make_boxplot_figure(acc_fus_df, "ACC — Fusion jitter pro Mark")
make_boxplot_figure(emg_hal_df, "EMG — HAL jitter pro Mark")
make_boxplot_figure(emg_fus_df, "EMG — Fusion jitter pro Mark")

# ===== Alle gleichzeitig anzeigen =====
plt.show()
