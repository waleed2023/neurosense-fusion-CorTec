import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

CSV_PATH = "timing_metrics.csv"

# Dummy-Output Marks (nimmt den ersten, der in der Datei existiert)
ACC_MARKS = ["Fusion_Dummy_ACC_TRIGGER_TS", "Fusion_Dummy_ACC_Exit", "Fusion_Dummy_ACC_ENTER"]
EMG_MARKS = ["Fusion_Dummy_EMG_TRIGGER_TS", "Fusion_Dummy_EMG_Exit", "Fusion_Dummy_EMG_ENTER"]

ACC_REF_HZ = 833.0
EMG_REF_HZ = 10.0

WIN_S_ACC = 1.0
WIN_S_EMG = 1.0

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


def pick_mark(df: pd.DataFrame, candidates: list[str]) -> str:
    present = set(df["name"].unique())
    for m in candidates:
        if m in present:
            return m
    raise ValueError(f"Keiner der Marks gefunden: {candidates}")


def effective_rate(ts_us: np.ndarray, win_s: float):
    """Rate = counts pro Fenster (nur volle Fenster, damit das Ende nicht künstlich abfällt)."""
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 2:
        return np.array([]), np.array([])

    t = (ts - ts[0]) / 1e6  # seconds
    t_end_full = np.floor(t[-1] / win_s) * win_s
    if t_end_full <= 0:
        return np.array([]), np.array([])

    edges = np.arange(0.0, t_end_full + win_s, win_s)
    counts, _ = np.histogram(t, bins=edges)
    rate = counts / win_s
    centers = edges[:-1] + win_s / 2
    return centers, rate


def plot_one(df: pd.DataFrame, label: str, candidates: list[str], ref_hz: float, win_s: float):
    mark = pick_mark(df, candidates)
    ts = df.loc[df["name"] == mark, "ts_us"].to_numpy(np.int64)

    x, y = effective_rate(ts, win_s)

    # kurze Statistik in der Konsole
    if len(ts) >= 2:
        dur_s = (ts[-1] - ts[0]) / 1e6
        mean_hz = (len(ts) - 1) / dur_s if dur_s > 0 else float("nan")
        print(f"{label}: mark={mark}  n={len(ts)}  dur={dur_s:.2f}s  mean≈{mean_hz:.2f}Hz")
    else:
        print(f"{label}: mark={mark}  n={len(ts)}")

    plt.figure(figsize=(7, 3))
    if len(x):
        plt.plot(x, y, linewidth=1.0)
        plt.axhline(ref_hz, linestyle="--", linewidth=1.0)
        plt.xlim(0, x[-1] + win_s / 2)
    else:
        plt.text(0.5, 0.5, f"No data for {mark}", ha="center", va="center", transform=plt.gca().transAxes)

    plt.title(f"Effective rate (dummy output) — {label}  [{mark}]")
    plt.xlabel("Time [s]")
    plt.ylabel("Rate [Hz]")
    plt.grid(True, linewidth=0.3, alpha=0.4)
    plt.tight_layout()


def main():
    df = load_csv(CSV_PATH)

    plot_one(df, "ACC", ACC_MARKS, ACC_REF_HZ, WIN_S_ACC)
    plot_one(df, "EMG", EMG_MARKS, EMG_REF_HZ, WIN_S_EMG)

    plt.show()


if __name__ == "__main__":
    main()
