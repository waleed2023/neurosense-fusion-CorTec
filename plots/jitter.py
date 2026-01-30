import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ===== Einstellungen =====
CSV_PATH = "timing_metrics.csv"   # ggf. anpassen
MIN_SAMPLES_PER_MARK = 50         # Marks mit weniger Samples überspringen
SHOW_FLIERS = False               # keine "odd values" Punkte
WHISKERS = (5, 95)                # ruhiger als 1.5*IQR

CLIP_FOR_VISUALS = True           # nur fürs Plotten: extreme Werte kappen
CLIP_PCTL = 99                    # z.B. p99

# ===== CSV laden =====
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]
df = pd.read_csv(CSV_PATH, header=None, names=COLS)
df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["ts_us", "name"])

# optional: nur "mark"-Zeilen
df = df[df["kind"].astype(str).str.lower().eq("mark")]

def compute_jitter_ms_for_mark(mark_name: str):
    ts = df.loc[df["name"] == mark_name, "ts_us"].sort_values().to_numpy()
    if ts.size < 2:
        return None

    dt = np.diff(ts)
    dt = dt[dt > 0]  # negative/0 raus
    if dt.size < MIN_SAMPLES_PER_MARK:
        return None

    nominal = np.median(dt)              # robustes nominal Δt
    jitter_us = np.abs(dt - nominal)     # jitter in µs
    return (jitter_us / 1000.0), nominal # jitter in ms, nominal in µs

def build_group_jitter_df(group_key: str):
    # group_key: "ACC" oder "EMG"
    names = [n for n in df["name"].unique() if group_key.lower() in str(n).lower()]
    rows = []
    nominals = {}

    for n in sorted(names):
        res = compute_jitter_ms_for_mark(n)
        if res is None:
            continue
        jitter_ms, nominal_us = res
        nominals[n] = nominal_us
        rows.append(pd.DataFrame({"name": n, "jitter_ms": jitter_ms}))

    if not rows:
        return pd.DataFrame(columns=["name", "jitter_ms"]), {}

    out = pd.concat(rows, ignore_index=True)
    return out, nominals

def plot_group_boxplots(jit_df, title):
    if jit_df.empty:
        print(f"[WARN] Keine Daten für {title} (prüfe Mark-Namen oder MIN_SAMPLES_PER_MARK).")
        return

    # Sortierung nach median jitter
    order = (
        jit_df.groupby("name")["jitter_ms"]
        .median()
        .sort_values()
        .index
        .tolist()
    )

    plot_df = jit_df.copy()
    if CLIP_FOR_VISUALS:
        cap = np.percentile(plot_df["jitter_ms"], CLIP_PCTL)
        plot_df["jitter_ms"] = np.minimum(plot_df["jitter_ms"], cap)

    data = [plot_df.loc[plot_df["name"] == nm, "jitter_ms"].to_numpy() for nm in order]

    fig_h = max(5, 0.33 * len(order))
    plt.figure(figsize=(12, fig_h))
    ax = plt.gca()

    ax.boxplot(
        data,
        vert=False,
        labels=order,
        showfliers=SHOW_FLIERS,
        whis=WHISKERS,
    )

    ax.set_title(title)
    ax.set_xlabel("Jitter |Δt − median(Δt)| [ms]")
    ax.grid(True, axis="x", alpha=0.3)

    info = f"Marks={len(order)} | MIN_SAMPLES_PER_MARK={MIN_SAMPLES_PER_MARK}"
    if CLIP_FOR_VISUALS:
        info += f" | visual clip @ p{CLIP_PCTL}"
    ax.text(
        0.99, 0.99, info,
        transform=ax.transAxes,
        ha="right", va="top",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.9, edgecolor="none")
    )



# ===== ACC Plot =====
acc_jit_df, _ = build_group_jitter_df("ACC")
plot_group_boxplots(acc_jit_df, "ACC Jitter pro Mark (ohne Outlier-Punkte)")

# ===== EMG Plot =====
emg_jit_df, _ = build_group_jitter_df("EMG")
plot_group_boxplots(emg_jit_df, "EMG Jitter pro Mark (ohne Outlier-Punkte)")
plt.tight_layout()
plt.show()