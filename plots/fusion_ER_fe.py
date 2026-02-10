import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

CSV_PATH = "timing_metrics_feature.csv"          # <-- anpassen
MARK = "Fusion_Feature_OUT_TS"           # nur diese Marke
WIN_S = 1.0                              # Fenstergröße in Sekunden

# Wenn du eine feste Referenz willst, setze z.B. REF_HZ = 1000.0 (bei 1ms step)
# Wenn None: Referenz wird aus median(dt) geschätzt.
REF_HZ = None

DEFAULT_COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    # Falls ohne Header geloggt wurde:
    if "name" not in df.columns or "ts_us" not in df.columns:
        df = pd.read_csv(path, header=None)
        n = df.shape[1]
        df.columns = DEFAULT_COLS[:n] + [f"col{i}" for i in range(len(DEFAULT_COLS[:n]), n)]

    df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
    df = df.dropna(subset=["name", "ts_us"])
    df["ts_us"] = df["ts_us"].astype(np.int64)
    df["name"] = df["name"].astype(str)
    return df


def effective_rate(ts_us: np.ndarray, win_s: float):
    """Rate = counts pro Fenster (nur volle Fenster, damit das Ende nicht künstlich abfällt)."""
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 2:
        return np.array([]), np.array([])

    t = (ts - ts[0]) / 1e6  # seconds since first
    t_end_full = np.floor(t[-1] / win_s) * win_s
    if t_end_full <= 0:
        return np.array([]), np.array([])

    edges = np.arange(0.0, t_end_full + win_s, win_s)
    counts, _ = np.histogram(t, bins=edges)
    rate = counts / win_s
    centers = edges[:-1] + win_s / 2
    return centers, rate


def estimate_ref_hz_from_ts(ts_us: np.ndarray):
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 3:
        return None
    dts = np.diff(ts)
    med = np.median(dts)
    if med <= 0:
        return None
    return 1e6 / med  # Hz


def main():
    df = load_csv(CSV_PATH)

    ts = df.loc[df["name"] == MARK, "ts_us"].to_numpy(np.int64)
    if len(ts) == 0:
        print(f"Mark not found: {MARK}")
        return

    # kurze Statistik
    if len(ts) >= 2:
        dur_s = (ts[-1] - ts[0]) / 1e6
        mean_hz = (len(ts) - 1) / dur_s if dur_s > 0 else float("nan")
        print(f"{MARK}: n={len(ts)}  dur={dur_s:.2f}s  mean≈{mean_hz:.2f}Hz")
    else:
        print(f"{MARK}: n={len(ts)}")

    x, y = effective_rate(ts, WIN_S)

    ref_hz = REF_HZ if REF_HZ is not None else estimate_ref_hz_from_ts(ts)

    plt.figure(figsize=(7, 3))
    if len(x):
        plt.plot(x, y, linewidth=1.0)
        if ref_hz is not None:
            plt.axhline(ref_hz, linestyle="--", linewidth=1.0)
        plt.xlim(0, x[-1] + WIN_S / 2)
    else:
        plt.text(0.5, 0.5, "Not enough data", ha="center", va="center", transform=plt.gca().transAxes)

    plt.title(f"Effective rate — {MARK}")
    plt.xlabel("Time [s]")
    plt.ylabel("Rate [Hz]")
    plt.grid(True, linewidth=0.3, alpha=0.4)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
