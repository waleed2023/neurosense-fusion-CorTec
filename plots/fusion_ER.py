import sys
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

DEFAULT_COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

DEFAULT_ACC_MARK = "Fusion_Dummy_ACC_Exit"
DEFAULT_EMG_MARK = "Fusion_Dummy_EMG_Exit"

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    # Kein Header? -> neu einlesen mit default cols
    if "name" not in df.columns:
        df = pd.read_csv(path, header=None)
        n = df.shape[1]
        df.columns = DEFAULT_COLS[:n] + [f"col{i}" for i in range(len(DEFAULT_COLS[:n]), n)]

    df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
    df = df.dropna(subset=["name", "ts_us"])
    df["ts_us"] = df["ts_us"].astype(np.int64)
    return df

def compute_overall_rate(ts_us: np.ndarray) -> float:
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 2:
        return float("nan")
    duration_s = (ts[-1] - ts[0]) / 1e6
    if duration_s <= 0:
        return float("nan")
    # (n-1) intervals over duration
    return float((len(ts) - 1) / duration_s)

def dt_stats_us(ts_us: np.ndarray) -> dict:
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 2:
        return {"median_dt_us": np.nan, "mean_dt_us": np.nan, "min_dt_us": np.nan, "max_dt_us": np.nan}
    d = np.diff(ts)
    return {
        "median_dt_us": float(np.median(d)),
        "mean_dt_us": float(np.mean(d)),
        "min_dt_us": float(np.min(d)),
        "max_dt_us": float(np.max(d)),
    }

def sliding_rate(ts_us: np.ndarray, win_s: float) -> tuple[np.ndarray, np.ndarray, float]:
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 2:
        return np.array([]), np.array([]), float("nan")

    t = (ts - ts[0]) / 1e6  # seconds
    t_end = float(t[-1])

    t_end_full = np.floor(t_end / win_s) * win_s
    if t_end_full <= 0:
        return np.array([]), np.array([]), float("nan")

    edges = np.arange(0.0, t_end_full + win_s, win_s)
    counts, _ = np.histogram(t, bins=edges)

    rate = counts / win_s
    centers = edges[:-1] + win_s / 2
    avg_rate = float(np.mean(rate)) if len(rate) else float("nan")
    return centers, rate, avg_rate

def plot_rate(ts_us: np.ndarray, title: str, win_s: float, ref_hz: float, save_path: str | None = None):
    centers, rate, avg_rate = sliding_rate(ts_us, win_s=win_s)

    if len(rate) == 0:
        print(f"[{title}] Zu wenig Daten für Sliding-Window-Plot.")
        return

    fig, ax = plt.subplots(figsize=(10, 3))
    ax.plot(centers, rate, linewidth=1.0)

    ax.axhline(ref_hz, linestyle="--", linewidth=1.0)
    ax.axhline(avg_rate, linestyle=":", linewidth=1.0)

    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Rate [Hz]")
    ax.set_title(title)
    ax.set_xlim(0, float(centers[-1] + win_s / 2))

    txt = f"ref: {ref_hz:.0f} Hz\nmean(win): {avg_rate:.1f} Hz"
    ax.text(
        0.98, 0.98, txt,
        transform=ax.transAxes,
        ha="right", va="top",
        fontsize=9,
        bbox=dict(
            boxstyle="square,pad=0.25",
            facecolor="white",
            edgecolor="black",
            linewidth=0.8
        )
    )

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=300)
    plt.show()

def extract_ts(df: pd.DataFrame, mark: str) -> np.ndarray:
    ts = df.loc[df["name"] == mark, "ts_us"].to_numpy(np.int64)
    return ts

def print_summary(label: str, ts_us: np.ndarray):
    n = len(ts_us)
    overall = compute_overall_rate(ts_us)
    stats = dt_stats_us(ts_us)
    print(f"\n=== {label} ===")
    print(f"samples: {n}")
    print(f"overall rate (from ts): {overall:.2f} Hz")
    print(f"dt_us median/mean: {stats['median_dt_us']:.1f} / {stats['mean_dt_us']:.1f}")
    print(f"dt_us min/max: {stats['min_dt_us']:.1f} / {stats['max_dt_us']:.1f}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="timing_metrics.csv (fusion dummy)")
    ap.add_argument("--win", type=float, default=1.0, help="window size in seconds (default 1.0)")
    ap.add_argument("--acc_mark", default=DEFAULT_ACC_MARK)
    ap.add_argument("--emg_mark", default=DEFAULT_EMG_MARK)
    ap.add_argument("--acc_ref", type=float, default=700.0, help="ACC reference Hz (default 700)")
    ap.add_argument("--emg_ref", type=float, default=10.0, help="EMG reference Hz (default 10)")
    ap.add_argument("--save", action="store_true", help="save plots as PNG next to CSV")
    args = ap.parse_args()

    df = load_csv(args.csv)

    acc_ts = extract_ts(df, args.acc_mark)
    emg_ts = extract_ts(df, args.emg_mark)

    if len(acc_ts) == 0:
        print(f"Keine Daten für ACC mark '{args.acc_mark}' gefunden.")
    else:
        print_summary("ACC (Dummy Output)", acc_ts)
        save_path = None
        if args.save:
            save_path = args.csv.replace(".csv", "_acc_rate.png")
        plot_rate(acc_ts, title="Effective rate (ACC dummy output)", win_s=args.win, ref_hz=args.acc_ref, save_path=save_path)

    if len(emg_ts) == 0:
        print(f"Keine Daten für EMG mark '{args.emg_mark}' gefunden.")
    else:
        print_summary("EMG (Dummy Output)", emg_ts)
        save_path = None
        if args.save:
            save_path = args.csv.replace(".csv", "_emg_rate.png")
        plot_rate(emg_ts, title="Effective rate (EMG dummy output)", win_s=args.win, ref_hz=args.emg_ref, save_path=save_path)

if __name__ == "__main__":
    main()
