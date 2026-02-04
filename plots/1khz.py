import sys
import numpy as np
import pandas as pd

# ===== Marks =====
EMG_ENTRY  = "EMG_HAL_ENTRY"
EMG_FUSION = "Fusion_Dummy_EMG_Exit"
ACC_ENTRY  = "ACC_HAL_ENTRY"
ACC_FUSION = "Fusion_Dummy_ACC_Exit"

# ===== “Double points” merge threshold =====
# Alles innerhalb dieser Zeit wird als ein Event gezählt (z.B. EMG-Pärchen)
GROUP_MS = 5.0
GROUP_US = int(GROUP_MS * 1000)

DEFAULT_COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    # Kein Header? -> neu einlesen mit default cols
    if "name" not in df.columns:
        df = pd.read_csv(path, header=None)
        n = df.shape[1]
        df.columns = DEFAULT_COLS[:n] + [f"col{i}" for i in range(len(DEFAULT_COLS[:n]), n)]

    # ts_us finden/setzen
    if "ts_us" not in df.columns:
        for c in df.columns:
            lc = str(c).lower()
            if "ts" in lc and "us" in lc:
                df = df.rename(columns={c: "ts_us"})
                break

    if "name" not in df.columns or "ts_us" not in df.columns:
        raise SystemExit(f"CSV Spalten nicht erkannt. Gefunden: {list(df.columns)}")

    return df


def merge_close_events(ts_us: np.ndarray, threshold_us: int) -> np.ndarray:
    """Merge events that are closer than threshold_us by keeping only the first in each group."""
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) == 0:
        return ts
    keep = [ts[0]]
    for t in ts[1:]:
        if (t - keep[-1]) > threshold_us:
            keep.append(t)
    return np.array(keep, dtype=np.int64)


def effective_rate(ts_us: np.ndarray):
    ts = np.sort(ts_us.astype(np.int64))
    n = len(ts)
    if n < 2:
        return float("nan"), n, float("nan"), float("nan")

    duration_s = (ts[-1] - ts[0]) / 1e6
    rate_hz = (n - 1) / duration_s if duration_s > 0 else float("nan")

    dt = np.diff(ts)
    dt = dt[dt > 0]
    median_dt_us = float(np.median(dt)) if len(dt) else float("nan")

    return float(rate_hz), n, float(duration_s), median_dt_us


def report(df: pd.DataFrame, mark: str, do_merge: bool):
    m = df[df["name"] == mark]
    ts = m["ts_us"].to_numpy()

    if do_merge:
        ts = merge_close_events(ts, GROUP_US)

    rate_hz, n, dur_s, med_dt = effective_rate(ts)
    suffix = f" (grouped <= {GROUP_MS}ms)" if do_merge else ""
    print(f"{mark}{suffix}: samples={n}, duration_s={dur_s:.2f}, rate_hz={rate_hz:.2f}, median_dt_us={med_dt:.0f}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python 1khz.py <timing_metrics.csv>")
        sys.exit(1)

    df = load_csv(sys.argv[1])

    print("=== EMG ===")
    report(df, EMG_ENTRY,  do_merge=False)   # EMG: merge double points
    report(df, EMG_FUSION, do_merge=False)  # Fusion: normal

    print("\n=== ACC ===")
    report(df, ACC_ENTRY,  do_merge=False)
    report(df, ACC_FUSION, do_merge=False)


if __name__ == "__main__":
    main()
