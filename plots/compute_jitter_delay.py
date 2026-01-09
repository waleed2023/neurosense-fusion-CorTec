# compute_jitter_delay.py
#
# Plots (per sensor):
#   1) delay        = OUT - ENTRY
#   2) jitter_entry = ENTRY[i] - ENTRY[i-1]
#   3) jitter_out   = OUT[i]   - OUT[i-1]
#
# Unit handling:
#   - Default: assumes timestamps are already in microseconds (us)
#   - Use --input-ms if your CSV timestamps are in milliseconds (ms)
#   - Use --output-ms to display results in ms on plots/prints

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


def read_marks(fn: Path, ts_scale: int = 1):
    """Read mark rows.

    Expected rows:
      mark,<NAME>,<IDX>,<TS>,...

    NAME must end with _HAL_ENTRY or _HAL_OUT.
    TS will be multiplied by ts_scale (e.g., ms->us: 1000).
    """
    entry = defaultdict(dict)  # entry[sensor][idx] = ts
    out = defaultdict(dict)    # out[sensor][idx]   = ts

    with fn.open(newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        for row in r:
            if not row or row[0].startswith("#"):
                continue
            if row[0] != "mark" or len(row) < 4:
                continue

            name = row[1].strip()
            try:
                idx = int(row[2])
                ts = int(row[3]) * ts_scale
            except ValueError:
                continue

            if name.endswith("_HAL_ENTRY"):
                sensor = name[: -len("_HAL_ENTRY")]
                entry[sensor][idx] = ts
            elif name.endswith("_HAL_OUT"):
                sensor = name[: -len("_HAL_OUT")]
                out[sensor][idx] = ts

    return entry, out


def compute_delay(entry, out):
    """delay[sensor] = OUT - ENTRY (for idx present in both)."""
    delay = defaultdict(list)
    for sensor in set(entry.keys()) | set(out.keys()):
        common = sorted(set(entry[sensor].keys()) & set(out[sensor].keys()))
        for idx in common:
            delay[sensor].append(float(out[sensor][idx] - entry[sensor][idx]))
    return delay


def compute_dt(series_by_idx):
    """Return dt list for consecutive indices: ts[i] - ts[i-1]."""
    idxs = sorted(series_by_idx.keys())
    if len(idxs) < 2:
        return []
    dts = []
    prev = series_by_idx[idxs[0]]
    for idx in idxs[1:]:
        cur = series_by_idx[idx]
        dts.append(float(cur - prev))
        prev = cur
    return dts


def boxplot(data_by_sensor, title: str, ylabel: str):
    sensors = sorted(data_by_sensor.keys())
    data = [data_by_sensor[s] for s in sensors]

    plt.figure()
    plt.boxplot(data, labels=sensors, showfliers=True)
    plt.title(title)
    plt.ylabel(ylabel)
    plt.grid(True, axis="y")
    plt.tight_layout()


def _scale_values(data_by_sensor, div: float):
    if div == 1.0:
        return data_by_sensor
    return {k: [v / div for v in vals] for k, vals in data_by_sensor.items()}


def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    return s[len(s) // 2]


def _print_stats(label: str, data_by_sensor, unit: str):
    for sensor in sorted(data_by_sensor.keys()):
        vals = data_by_sensor[sensor]
        if not vals:
            print(f"{sensor}: {label} n=0")
            continue
        print(
            f"{sensor}: {label} n={len(vals)}  min={min(vals):.3f}  median={_median(vals):.3f}  max={max(vals):.3f} [{unit}]"
        )


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("csv_file", type=Path, help="metrics CSV containing mark rows")

    ap.add_argument(
        "--input-ms",
        action="store_true",
        help="input timestamps are ms (convert to us internally)",
    )
    ap.add_argument(
        "--output-ms",
        action="store_true",
        help="report and plot in ms (divide by 1000)",
    )
    ap.add_argument(
        "--abs",
        action="store_true",
        help="use absolute values (useful if wallclock jumps cause negatives)",
    )
    args = ap.parse_args()

    ts_scale = 1000 if args.input_ms else 1  # ms -> us
    entry, out = read_marks(args.csv_file, ts_scale=ts_scale)

    delay_us = compute_delay(entry, out)
    jitter_entry_us = {s: compute_dt(entry[s]) for s in entry.keys()}
    jitter_out_us = {s: compute_dt(out[s]) for s in out.keys()}

    if args.abs:
        delay_us = {k: [abs(v) for v in vals] for k, vals in delay_us.items()}
        jitter_entry_us = {k: [abs(v) for v in vals] for k, vals in jitter_entry_us.items()}
        jitter_out_us = {k: [abs(v) for v in vals] for k, vals in jitter_out_us.items()}

    div = 1000.0 if args.output_ms else 1.0
    unit = "ms" if args.output_ms else "us"

    delay = _scale_values(delay_us, div)
    jitter_entry = _scale_values(jitter_entry_us, div)
    jitter_out = _scale_values(jitter_out_us, div)

    _print_stats("delay(OUT-ENTRY)", delay, unit)
    _print_stats("jitter(ENTRY->ENTRY)", jitter_entry, unit)
    _print_stats("jitter(OUT->OUT)", jitter_out, unit)

    if delay:
        boxplot(delay, "HAL Delay (OUT - ENTRY)", f"delay_{unit}")
    if jitter_entry:
        boxplot(jitter_entry, "Jitter: ENTRY -> ENTRY (inter-arrival)", f"jitter_entry_{unit}")
    if jitter_out:
        boxplot(jitter_out, "Jitter: OUT -> OUT (inter-departure)", f"jitter_out_{unit}")

    plt.show()


if __name__ == "__main__":
    main()
