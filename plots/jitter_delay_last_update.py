# plot_fusion_in_to_out_delay.py
import argparse
import csv
import math
from array import array

import matplotlib.pyplot as plt


def downsample(xs: array, max_points: int) -> array:
    if max_points <= 0 or len(xs) <= max_points:
        return xs
    stride = int(math.ceil(len(xs) / max_points))
    return xs[::stride]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="sensor_timing_metrics.csv")
    ap.add_argument("--in", dest="in_marks", nargs="+", required=True,
                    help="Mark(s) for 'data arrives' (e.g. Fusion_Feature_Accel_ENTRY or BLEManager_Fusion_in)")
    ap.add_argument("--out", dest="out_mark", required=True,
                    help="Mark for fusion out (e.g. Fusion_Feature_EXIT)")
    ap.add_argument("--max_points", type=int, default=80000, help="downsample for plotting (0=no limit)")
    args = ap.parse_args()

    in_set = set(args.in_marks)
    out_name = args.out_mark

    in_ts = []
    out_ts = []

    with open(args.csv_path, "r", encoding="utf-8", errors="ignore") as f:
        r = csv.reader(f)
        for row in r:
            if not row or row[0].strip() != "mark" or len(row) < 4:
                continue
            name = row[1].strip()
            try:
                ts_us = int(row[3])
            except Exception:
                continue
            if ts_us <= 0:
                continue

            if name in in_set:
                in_ts.append(ts_us)
            elif name == out_name:
                out_ts.append(ts_us)

    in_ts.sort()
    out_ts.sort()

    if not in_ts:
        print(f"[err] No IN marks found for: {sorted(in_set)}")
        return
    if not out_ts:
        print(f"[err] No OUT marks found for: {out_name}")
        return

    # Two-pointer: for each OUT pick last IN timestamp <= OUT
    delays_ms = array("f")
    i = 0
    last_in = None

    for t_out in out_ts:
        while i < len(in_ts) and in_ts[i] <= t_out:
            last_in = in_ts[i]
            i += 1
        if last_in is None:
            continue
        dt_us = t_out - last_in
        if dt_us >= 0:
            delays_ms.append(dt_us / 1000.0)

    if not delays_ms:
        print("[plot] No delays computed (no IN before OUT).")
        return

    delays_ms = downsample(delays_ms, args.max_points)

    # simple stats
    xs = sorted(delays_ms)
    def pct(p):
        k = int((p/100.0) * (len(xs)-1))
        return xs[k]
    print(f"[info] delays computed: {len(xs)}")
    print(f"[info] median={pct(50):.3f} ms, p95={pct(95):.3f} ms, max={xs[-1]:.3f} ms")

    # plot
    label = f"{'+'.join(args.in_marks)}  ->  {out_name}"
    plt.figure(figsize=(10, 3))
    try:
        plt.boxplot([delays_ms], vert=False, tick_labels=[label], showfliers=False)
    except TypeError:
        plt.boxplot([delays_ms], vert=False, labels=[label], showfliers=False)
    plt.title("Delay: Data arrives -> Fusion out (time-based)")
    plt.xlabel("delay [ms]")
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
