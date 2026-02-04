import sys
import numpy as np
import pandas as pd

ACC_ENTRY  = "ACC_HAL_ENTRY"
ACC_FUSION = "Fusion_Dummy_ACC_Exit"
EMG_ENTRY  = "EMG_HAL_ENTRY"
EMG_FUSION = "Fusion_Dummy_EMG_Exit"

# EMG optional: "Packet-Rate" durch Merge (z.B. 5ms)
EMG_GROUP_US = 5000

DEFAULT_COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

def load_csv(path):
    df = pd.read_csv(path)
    if "name" not in df.columns:   # meistens: kein Header
        df = pd.read_csv(path, header=None)
        n = df.shape[1]
        df.columns = DEFAULT_COLS[:n] + [f"col{i}" for i in range(len(DEFAULT_COLS[:n]), n)]
    df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
    df = df.dropna(subset=["name", "ts_us"])
    return df

def get_ts(df, mark):
    ts = df.loc[df["name"] == mark, "ts_us"].to_numpy(dtype=np.int64)
    ts.sort()
    return ts

def overall_rate(ts):
    if len(ts) < 2:
        return float("nan"), float("nan")
    dur_s = (ts[-1] - ts[0]) / 1e6
    hz = (len(ts) - 1) / dur_s if dur_s > 0 else float("nan")
    return hz, dur_s

def merge_close(ts, thr_us):
    if len(ts) == 0:
        return ts
    keep = [ts[0]]
    for t in ts[1:]:
        if (t - keep[-1]) > thr_us:
            keep.append(t)
    return np.array(keep, dtype=np.int64)

def report(df, mark, desired_hz=None, ts_override=None, label=None):
    ts = ts_override if ts_override is not None else get_ts(df, mark)
    hz, dur = overall_rate(ts)
    name = label if label else mark
    print(f"{name}: samples={len(ts)}, duration_s={dur:.2f}, rate_hz={hz:.2f}")
    if desired_hz is not None and not np.isnan(dur):
        exp = desired_hz * dur
        print(f"  expected@{desired_hz}Hz ≈ {exp:.0f} samples | missing ≈ {exp - len(ts):.0f}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python rates.py <timing_metrics.csv>")
        sys.exit(1)

    df = load_csv(sys.argv[1])

    print("=== ACC (Sample-Rate) ===")
    report(df, ACC_ENTRY,  desired_hz=833)
    report(df, ACC_FUSION)

    print("\n=== EMG (Sample-Rate) ===")
    report(df, EMG_ENTRY, desired_hz=10)          # 10Hz Sample-Rate
    report(df, EMG_FUSION)

    print("\n=== EMG (Packet/Burst-Rate, optional) ===")
    ts_emg = get_ts(df, EMG_ENTRY)
    ts_pkt = merge_close(ts_emg, EMG_GROUP_US)
    report(df, EMG_ENTRY, ts_override=ts_pkt, label=f"{EMG_ENTRY} grouped<=5ms")

if __name__ == "__main__":
    main()
