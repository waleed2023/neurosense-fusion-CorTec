# hal_delay_jitter_A_hist.py
from __future__ import annotations
import csv
from pathlib import Path
from collections import defaultdict
import matplotlib.pyplot as plt


def read_marks(fn: Path):
    entry = defaultdict(dict)  # entry[sensor][idx] = ts_ms
    out = defaultdict(dict)    # out[sensor][idx]   = ts_ms

    with fn.open(newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        for row in r:
            if not row or row[0].startswith("#"):
                continue
            if row[0] != "mark" or len(row) < 4:
                continue

            name = row[1].strip()
            idx = int(row[2])
            ts = int(row[3])

            if name.endswith("_HAL_ENTRY"):
                sensor = name[:-len("_HAL_ENTRY")]
                entry[sensor][idx] = ts
            elif name.endswith("_HAL_OUT"):
                sensor = name[:-len("_HAL_OUT")]
                out[sensor][idx] = ts

    return entry, out


def compute_delay(entry, out):
    delay = defaultdict(list)
    for sensor in set(entry.keys()) | set(out.keys()):
        common = sorted(set(entry[sensor].keys()) & set(out[sensor].keys()))
        for idx in common:
            delay[sensor].append(float(out[sensor][idx] - entry[sensor][idx]))
    return delay


def compute_jitter_A(entry, out):
    # jitter_A = Δt_out - Δt_entry (sample-to-sample)
    jitter_A = defaultdict(list)

    for sensor in set(entry.keys()) | set(out.keys()):
        common = sorted(set(entry[sensor].keys()) & set(out[sensor].keys()))
        if len(common) < 2:
            continue

        for i in range(1, len(common)):
            prev_idx = common[i - 1]
            cur_idx = common[i]

            dt_entry = entry[sensor][cur_idx] - entry[sensor][prev_idx]
            dt_out = out[sensor][cur_idx] - out[sensor][prev_idx]

            jitter_A[sensor].append(float(dt_out - dt_entry))

    return jitter_A


def hist_per_sensor(data_by_sensor, title_prefix, xlabel, bins=50, density=False):
    for sensor in sorted(data_by_sensor.keys()):
        vals = data_by_sensor[sensor]
        if not vals:
            continue
        plt.figure()
        plt.hist(vals, bins=bins, density=density)
        plt.title(f"{title_prefix} — {sensor}")
        plt.xlabel(xlabel)
        plt.ylabel("density" if density else "count")
        plt.grid(True, axis="y")
        plt.tight_layout()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_file", type=Path, help="metrics CSV containing mark rows")
    ap.add_argument("--bins", type=int, default=50, help="histogram bins")
    ap.add_argument("--density", action="store_true", help="normalize histogram (PDF)")
    ap.add_argument("--abs", action="store_true", help="use absolute jitter |Δt_out-Δt_entry|")
    args = ap.parse_args()

    entry, out = read_marks(args.csv_file)

    delay = compute_delay(entry, out)
    jitter = compute_jitter_A(entry, out)

    if args.abs:
        jitter = {k: [abs(v) for v in vals] for k, vals in jitter.items()}

    hist_per_sensor(delay, "HAL Delay (OUT - ENTRY)", "delay_ms", bins=args.bins, density=args.density)

    j_title = "HAL Jitter impact (Δt_out - Δt_entry)"
    if args.abs:
        j_title += " [absolute]"
    hist_per_sensor(jitter, j_title, "jitter_ms", bins=args.bins, density=args.density)

    plt.show()


if __name__ == "__main__":
    main()
