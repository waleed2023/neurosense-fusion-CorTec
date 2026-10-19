import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

CSV_PATH = "timing_metrics_dummy.csv"
DEFAULT_COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

ACC_ENTRY = "ACC_HAL_ENTRY"
EMG_ENTRY = "EMG_HAL_ENTRY"

ACC_REF_HZ = 833.0
EMG_REF_HZ = 10.0

WIN_S = 1.0
EMG_MERGE_DUP_US = 5000
ACC_MERGE_DUP_US = 0

ZOOM_PAD_HZ = 20.0
MIN_Y = 0.0


LABELS = {
    "ACC_HAL_ENTRY": "ACC (HAL entry)",
    "EMG_HAL_ENTRY": "EMG (HAL entry)",
}


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "name" not in df.columns or "ts_us" not in df.columns:
        df = pd.read_csv(path, header=None)
        n = df.shape[1]
        df.columns = DEFAULT_COLS[:n] + [f"col{i}" for i in range(len(DEFAULT_COLS[:n]), n)]
    df["name"] = df["name"].astype(str)
    df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
    df = df.dropna(subset=["name", "ts_us"])
    df["ts_us"] = df["ts_us"].astype(np.int64)
    return df


def merge_close(ts_us: np.ndarray, gap_us: int) -> np.ndarray:
    if gap_us <= 0 or len(ts_us) == 0:
        return np.sort(ts_us)
    ts = np.sort(ts_us)
    keep = [ts[0]]
    for t in ts[1:]:
        if int(t) - int(keep[-1]) > gap_us:
            keep.append(t)
    return np.array(keep, dtype=np.int64)


def effective_rates_hz(ts_us: np.ndarray, win_s: float) -> np.ndarray:
    if len(ts_us) < 2:
        return np.array([])
    t = (ts_us - ts_us[0]) / 1e6
    duration = t[-1]
    if duration <= win_s:
        return np.array([])
    bins = np.arange(0, duration + win_s, win_s)
    counts, _ = np.histogram(t, bins=bins)
    return counts / win_s


def boxplot_effective_rate(df: pd.DataFrame, mark: str, ref_hz: float, merge_gap_us: int, title: str):
    pretty = LABELS.get(mark, mark)
    ts = df.loc[df["name"] == mark, "ts_us"].to_numpy(np.int64)
    ts = merge_close(ts, merge_gap_us)
    rates = effective_rates_hz(ts, WIN_S)

    if len(rates) == 0:
        print(f"[skip] zu wenig Daten für {mark}")
        return

    mean = float(np.mean(rates))
    median = float(np.median(rates))
    print(f"{mark}: mean={mean:.1f} Hz, median={median:.1f} Hz, windows={len(rates)}")

    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    ax.boxplot([rates], labels=[pretty], showmeans=True, showfliers=True)  # <- keine Ausreißer

    ax.axhline(ref_hz, linestyle="--", linewidth=1.0)
    ax.set_title(title)
    ax.set_ylabel("Effective rate [Hz]")
    ax.grid(True, axis="y", linewidth=0.3, alpha=0.4)

    # Zoom: um median herum
    y0 = max(MIN_Y, median - ZOOM_PAD_HZ)
    y1 = median + ZOOM_PAD_HZ
    y0 = min(y0, ref_hz - ZOOM_PAD_HZ / 2)
    y1 = max(y1, ref_hz + ZOOM_PAD_HZ / 2)
    ax.set_ylim(y0, y1)

    # Info-Box OHNE win
    info = f"ref: {ref_hz:.1f} Hz\nmedian: {median:.1f} Hz\nmean: {mean:.1f} Hz"
    ax.text(
        0.98, 0.98, info,
        transform=ax.transAxes,
        ha="right", va="top",
        fontsize=9,
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.9, linewidth=0.8),
    )

    plt.tight_layout()
    plt.show()


df = load_csv(CSV_PATH)
boxplot_effective_rate(df, ACC_ENTRY, ACC_REF_HZ, ACC_MERGE_DUP_US,
                       "Effective rate – Accelerometer (HAL entry)")
boxplot_effective_rate(df, EMG_ENTRY, EMG_REF_HZ, EMG_MERGE_DUP_US,
                       "Effective rate – EMG (HAL entry)")
