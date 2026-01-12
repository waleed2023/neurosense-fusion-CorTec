# compute_jitter_delay.py
#
# Reads a CSV metrics log (rows starting with "mark") and plots:
#   1) HAL delay              = HAL_OUT - HAL_ENTRY
#   2) HAL jitter (entry)     = HAL_ENTRY[i] - HAL_ENTRY[i-1]
#   3) HAL jitter (out)       = HAL_OUT[i]   - HAL_OUT[i-1]
#   4) Fusion jitter (in)     = FUSION_IN[i] - FUSION_IN[i-1]
#   5) Fusion jitter (out)    = FUSION_OUT[i]- FUSION_OUT[i-1]
#
# Supported mark names (examples):
#   EMG_HAL_ENTRY, EMG_HAL_OUT
#   ACC_HAL_ENTRY, ACC_HAL_OUT
#   BLEManager_Fusion_in, ACCManager_Fusion_in
#   FusionManager_out   (also supports *_Fusion_out if you add it later)
#
# Unit handling:
#   - Default: assumes timestamps are already in microseconds (us)
#   - Use --input-ms if your CSV timestamps are in milliseconds (ms)
#   - Use --output-ms to display results in ms on plots/prints

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, DefaultDict

import matplotlib.pyplot as plt


# Observation point keys used internally
HAL_ENTRY = "HAL_ENTRY"
HAL_OUT = "HAL_OUT"
FUSION_IN = "FUSION_IN"
FUSION_OUT = "FUSION_OUT"


def read_marks(fn: Path, ts_scale: int = 1):
    """Read mark rows into point->sensor->idx->ts.

    Expected rows:
      mark,<NAME>,<IDX>,<TS>,...

    TS will be multiplied by ts_scale (e.g., ms->us: 1000).

    Recognized NAME patterns:
      <SENSOR>_HAL_ENTRY, <SENSOR>_HAL_OUT
      <SENSOR>_Fusion_in, <SENSOR>_Fusion_out
    plus some legacy/special names:
      FusionManager_out  -> (FUSION_OUT, "Fusion")
      FusionManager_in   -> (FUSION_IN,  "Fusion")
    """
    marks: Dict[str, DefaultDict[str, dict]] = {
        HAL_ENTRY: defaultdict(dict),
        HAL_OUT: defaultdict(dict),
        FUSION_IN: defaultdict(dict),
        FUSION_OUT: defaultdict(dict),
    }

    special = {
        "FusionManager_out": (FUSION_OUT, "Fusion"),
        "FusionManager_in": (FUSION_IN, "Fusion"),
    }

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

            if name in special:
                point, sensor = special[name]
                marks[point][sensor][idx] = ts
                continue

            if name.endswith("_HAL_ENTRY"):
                sensor = name[: -len("_HAL_ENTRY")]
                marks[HAL_ENTRY][sensor][idx] = ts
            elif name.endswith("_HAL_OUT"):
                sensor = name[: -len("_HAL_OUT")]
                marks[HAL_OUT][sensor][idx] = ts
            elif name.endswith("_Fusion_in"):
                sensor = name[: -len("_Fusion_in")]
                marks[FUSION_IN][sensor][idx] = ts
            elif name.endswith("_Fusion_out"):
                sensor = name[: -len("_Fusion_out")]
                marks[FUSION_OUT][sensor][idx] = ts

    return marks


def compute_delay(entry, out):
    """delay[sensor] = OUT - ENTRY (for idx present in both)."""
    delay = defaultdict(list)
    for sensor in set(entry.keys()) | set(out.keys()):
        common = sorted(set(entry[sensor].keys()) & set(out[sensor].keys()))
        for idx in common:
            delay[sensor].append(float(out[sensor][idx] - entry[sensor][idx]))
    return delay


def compute_dt(series_by_idx):
    """Return dt list for consecutive indices: ts[i] - ts[i-1] (idx order)."""
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
    marks = read_marks(args.csv_file, ts_scale=ts_scale)

    hal_entry = marks[HAL_ENTRY]
    hal_out = marks[HAL_OUT]
    fusion_in = marks[FUSION_IN]
    fusion_out = marks[FUSION_OUT]

    delay_us = compute_delay(hal_entry, hal_out)
    jitter_hal_entry_us = {s: compute_dt(hal_entry[s]) for s in hal_entry.keys()}
    jitter_hal_out_us = {s: compute_dt(hal_out[s]) for s in hal_out.keys()}
    jitter_fusion_in_us = {s: compute_dt(fusion_in[s]) for s in fusion_in.keys()}
    jitter_fusion_out_us = {s: compute_dt(fusion_out[s]) for s in fusion_out.keys()}

    if args.abs:
        delay_us = {k: [abs(v) for v in vals] for k, vals in delay_us.items()}
        jitter_hal_entry_us = {k: [abs(v) for v in vals] for k, vals in jitter_hal_entry_us.items()}
        jitter_hal_out_us = {k: [abs(v) for v in vals] for k, vals in jitter_hal_out_us.items()}
        jitter_fusion_in_us = {k: [abs(v) for v in vals] for k, vals in jitter_fusion_in_us.items()}
        jitter_fusion_out_us = {k: [abs(v) for v in vals] for k, vals in jitter_fusion_out_us.items()}

    div = 1000.0 if args.output_ms else 1.0
    unit = "ms" if args.output_ms else "us"

    delay = _scale_values(delay_us, div)
    jitter_hal_entry = _scale_values(jitter_hal_entry_us, div)
    jitter_hal_out = _scale_values(jitter_hal_out_us, div)
    jitter_fusion_in = _scale_values(jitter_fusion_in_us, div)
    jitter_fusion_out = _scale_values(jitter_fusion_out_us, div)

    _print_stats("delay(HAL_OUT - HAL_ENTRY)", delay, unit)
    _print_stats("jitter(HAL_ENTRY -> HAL_ENTRY)", jitter_hal_entry, unit)
    _print_stats("jitter(HAL_OUT -> HAL_OUT)", jitter_hal_out, unit)
    _print_stats("jitter(FUSION_IN -> FUSION_IN)", jitter_fusion_in, unit)
    _print_stats("jitter(FUSION_OUT -> FUSION_OUT)", jitter_fusion_out, unit)

    if delay:
        boxplot(delay, "HAL Delay (OUT - ENTRY)", f"delay_{unit}")
    if jitter_hal_entry:
        boxplot(jitter_hal_entry, "Jitter: HAL ENTRY -> HAL ENTRY (inter-arrival)", f"jitter_hal_entry_{unit}")
    if jitter_hal_out:
        boxplot(jitter_hal_out, "Jitter: HAL OUT -> HAL OUT (inter-departure)", f"jitter_hal_out_{unit}")
    if jitter_fusion_in:
        boxplot(jitter_fusion_in, "Jitter: Fusion IN -> Fusion IN (inter-arrival)", f"jitter_fusion_in_{unit}")
    if jitter_fusion_out:
        boxplot(jitter_fusion_out, "Jitter: Fusion OUT -> Fusion OUT (inter-departure)", f"jitter_fusion_out_{unit}")

    plt.show()


if __name__ == "__main__":
    main()
