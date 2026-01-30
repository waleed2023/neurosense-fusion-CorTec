import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

CSV_PATH = "timing_metrics.csv"
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

df = pd.read_csv(CSV_PATH, header=None, names=COLS)
df = df[df["kind"] == "mark"].copy()
df["idx"] = pd.to_numeric(df["idx"], errors="coerce")
df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["name", "idx", "ts_us"])

groups = {n: g.sort_values(["idx", "ts_us"]) for n, g in df.groupby("name")}
def have(n): return n in groups

def merged_delay(entry_mark: str, exit_mark: str) -> pd.DataFrame:
    """Gibt DataFrame mit idx und delay_ms zurück (idx-gematcht)."""
    a = groups[entry_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_entry"})
    b = groups[exit_mark][["idx", "ts_us"]].rename(columns={"ts_us": "t_exit"})
    m = a.merge(b, on="idx", how="inner").sort_values("idx")
    if m.empty:
        return pd.DataFrame(columns=["idx", "delay_ms"])
    d_us = (m["t_exit"] - m["t_entry"]).to_numpy()
    keep = d_us >= 0
    out = pd.DataFrame({
        "idx": m["idx"].to_numpy()[keep],
        "delay_ms": (d_us[keep] / 1000.0)
    })
    return out

def rolling_quantiles(y: np.ndarray, window: int = 500):
    """Rolling median & p95 (sehr simpel, mit pandas rolling)."""
    s = pd.Series(y)
    med = s.rolling(window, min_periods=window//5).median().to_numpy()
    p95 = s.rolling(window, min_periods=window//5).quantile(0.95).to_numpy()
    return med, p95

def plot_delay_heatmap(title: str, idx: np.ndarray, delay: np.ndarray):
    fig, ax = plt.subplots(1, 1, figsize=(10, 4.5))

    # 2D-Dichte: zeigt "Wolken" + Bursts sehr gut
    ax.hexbin(idx, delay, gridsize=120, mincnt=1)

    # Rolling-Linien drüber (zeigt Trend & Peaks)
    med, p95 = rolling_quantiles(delay, window=800)
    ax.plot(idx, med, linewidth=1.5, label="rolling median")
    ax.plot(idx, p95, linewidth=1.5, label="rolling p95")

    ax.set_title(title)
    ax.set_xlabel("idx")
    ax.set_ylabel("Delay [ms]")
    ax.grid(True, alpha=0.2)
    ax.legend()
    fig.tight_layout()

# -------------------------
# EMG end-to-end Delay (sensor ts -> fusion out)
# -------------------------
emg_entry = "Fusion_Dummy_EMG_Sensor_Time_Enter"
emg_exit  = "Fusion_Dummy_EMG_Wall_Time_Exit"

if have(emg_entry) and have(emg_exit):
    emg = merged_delay(emg_entry, emg_exit)
    if len(emg) > 0:
        plot_delay_heatmap("EMG – Delay Heatmap über Zeit (end-to-end)", emg["idx"].to_numpy(), emg["delay_ms"].to_numpy())
    else:
        print("EMG: keine Delay-Daten nach merge")
else:
    print("EMG: Marks fehlen im CSV")

# -------------------------
# ACC end-to-end Delay (sensor ts -> fusion out)
# -------------------------
acc_entry = "Fusion_Dummy_ACC_Sensor_Time_EXIT"
acc_exit  = "Fusion_Dummy_ACC_Wall_Time_Exit"

if have(acc_entry) and have(acc_exit):
    acc = merged_delay(acc_entry, acc_exit)
    if len(acc) > 0:
        plot_delay_heatmap("ACC – Delay Heatmap über Zeit (end-to-end)", acc["idx"].to_numpy(), acc["delay_ms"].to_numpy())
    else:
        print("ACC: keine Delay-Daten nach merge")
else:
    print("ACC: Marks fehlen im CSV")

plt.show()
