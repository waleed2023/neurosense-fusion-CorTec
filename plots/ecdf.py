import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# =========================================================
# Pro Sensor (EMG allein, ACC allein):
#   Plot A: ECDF der Delay-Werte (Verteilung)
#   Plot B: Delay über Zeit (Binning, z.B. 1s) -> median + p95
#
# Delay = t_exit - t_entry (match über idx)
# Zeitbasis für Binning = t_exit (Wall-Time Exit wenn vorhanden)
# Einheit: µs (keine Umrechnung auf ms)
# =========================================================

CSV_PATH = "timing_metrics.csv"  # <- anpassen
BIN_SEC = 1.0                    # <- z.B. 0.5, 1.0, 2.0
P95 = 0.95
P99 = 0.99

COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

# -------------------------
# CSV laden
# -------------------------
df = pd.read_csv(CSV_PATH, header=None, names=COLS)
df = df[df["kind"] == "mark"].copy()

df["idx"] = pd.to_numeric(df["idx"], errors="coerce")
df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["name", "idx", "ts_us"])

if df.empty:
    raise SystemExit("CSV ist leer oder enthält keine gültigen 'mark' Zeilen.")

groups = {n: g.sort_values(["idx", "ts_us"]) for n, g in df.groupby("name")}
def have(n): return n in groups

def pick_first(cands):
    for c in cands:
        if have(c):
            return c
    return None

print("\n--- Available marks (top 40) ---")
print(df["name"].value_counts().head(40))

# -------------------------
# Delay + Exit-Time berechnen
# -------------------------
def delay_with_exit_time(entry_mark: str, exit_mark: str) -> pd.DataFrame:
    """
    Output: idx, t_exit_us, delay_us
    """
    a = groups[entry_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_entry_us"})
    b = groups[exit_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_exit_us"})

    m = a.merge(b, on="idx", how="inner").sort_values("idx")
    if m.empty:
        return pd.DataFrame(columns=["idx", "t_exit_us", "delay_us"])

    delay_us = (m["t_exit_us"] - m["t_entry_us"]).to_numpy()
    ok = delay_us >= 0

    return pd.DataFrame({
        "idx": m["idx"].to_numpy()[ok],
        "t_exit_us": m["t_exit_us"].to_numpy()[ok],
        "delay_us": delay_us[ok],
    })

# -------------------------
# ECDF helper
# -------------------------
def ecdf_xy(x: np.ndarray):
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    x = x[x >= 0]
    if len(x) == 0:
        return np.array([]), np.array([])
    xs = np.sort(x)
    ys = np.arange(1, len(xs) + 1) / len(xs)
    return xs, ys

def print_quantiles(name, data):
    p50 = np.quantile(data, 0.50)
    p95 = np.quantile(data, P95)
    p99 = np.quantile(data, P99)
    print(f"[{name}] p50={p50:.0f} µs  p95={p95:.0f} µs  p99={p99:.0f} µs  N={len(data)}")

# -------------------------
# Binning helper (median + p95)
# -------------------------
def bin_stats(df_delay: pd.DataFrame, *, bin_sec: float) -> pd.DataFrame:
    """
    Gruppiert nach Zeit-Bins (bin_sec) auf Basis von t_exit_us.
    Output: t_bin_start_s, median_us, p95_us, count
    """
    t0 = df_delay["t_exit_us"].min()
    t_s = (df_delay["t_exit_us"] - t0) / 1_000_000.0  # Sekunden ab Start
    bin_id = np.floor(t_s / bin_sec).astype(int)

    tmp = df_delay.copy()
    tmp["t_s"] = t_s
    tmp["bin"] = bin_id

    def p95(x): return np.quantile(x, P95)

    out = tmp.groupby("bin")["delay_us"].agg(
        median_us="median",
        p95_us=p95,
        count="count"
    ).reset_index()

    out["t_bin_start_s"] = out["bin"] * bin_sec
    return out[["t_bin_start_s", "median_us", "p95_us", "count"]]

# -------------------------
# Pro Sensor plotten
# -------------------------
def plot_sensor(sensor_name: str, entry_candidates, exit_candidates):
    entry = pick_first(entry_candidates)
    exitm = pick_first(exit_candidates)

    print(f"\n--- {sensor_name} chosen path ---")
    print(sensor_name, ":", entry, "->", exitm)

    if not (entry and exitm):
        print(f"[skip] {sensor_name}: Marks nicht gefunden.")
        return

    ddf = delay_with_exit_time(entry, exitm)
    if len(ddf) == 0:
        print(f"[skip] {sensor_name}: keine Delay-Daten (Merge über idx leer).")
        return

    delay = ddf["delay_us"].to_numpy()
    print_quantiles(sensor_name, delay)

    # ---- Plot A: ECDF ----
    xs, ys = ecdf_xy(delay)
    plt.figure(figsize=(10, 5))
    plt.step(xs, ys, where="post")
    plt.title(f"{sensor_name} – Delay ECDF")
    plt.xlabel("Delay [µs]")
    plt.ylabel("Anteil ≤ x")
    plt.grid(True, alpha=0.25)
    plt.tight_layout()

    # ---- Plot B: Binned time (median + p95) ----
    b = bin_stats(ddf, bin_sec=BIN_SEC)

    plt.figure(figsize=(10, 5))
    plt.step(b["t_bin_start_s"], b["median_us"], where="post", label="median")
    plt.step(b["t_bin_start_s"], b["p95_us"], where="post", label="p95")
    plt.title(f"{sensor_name} – Delay über Zeit (Binning = {BIN_SEC:.2f}s)")
    plt.xlabel("Zeit ab Start [s] (basierend auf Exit-Zeit)")
    plt.ylabel("Delay [µs]")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.ticklabel_format(style="plain", axis="y", useOffset=False)
    plt.tight_layout()


# EMG allein
plot_sensor(
    "EMG",
    entry_candidates=["Fusion_Dummy_EMG_Sensor_Time_Enter", "Fusion_Dummy_EMG_Sensor_Time_EXIT", "EMG_HAL_ENTRY"],
    exit_candidates=["Fusion_Dummy_EMG_Wall_Time_Exit", "Fusion_Dummy_EMG_Wall_Time_EXIT", "EMG_HAL_OUT"],
)

# ACC allein
plot_sensor(
    "ACC",
    entry_candidates=["Fusion_Dummy_ACC_Sensor_Time_EXIT", "Fusion_Dummy_ACC_Sensor_Time_Enter", "ACC_HAL_ENTRY"],
    exit_candidates=["Fusion_Dummy_ACC_Wall_Time_Exit", "Fusion_Dummy_ACC_Wall_Time_EXIT", "ACC_HAL_OUT"],
)

plt.show()
