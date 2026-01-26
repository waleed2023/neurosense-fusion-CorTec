#!/usr/bin/env python3
import csv
import sys
from collections import defaultdict
from statistics import mean, pstdev

import matplotlib.pyplot as plt


def read_marks(path: str):
    """
    Expects rows like:
      mark,<name>,<idx>,<ts_us>,...
    """
    marks = defaultdict(list)  # name -> list[(ts_us:int, idx:int)]
    with open(path, "r", encoding="utf-8") as f:
        r = csv.reader(f)
        for row in r:
            if not row or row[0] != "mark":
                continue
            if len(row) < 4:
                continue
            name = row[1].strip()
            try:
                idx = int(row[2])
                ts_us = int(row[3])
            except ValueError:
                continue
            marks[name].append((ts_us, idx))

    # sort by timestamp for stable dt calculations
    for k in list(marks.keys()):
        marks[k].sort(key=lambda x: x[0])
    return marks


def pair_delay_by_idx(marks, entry_name: str, out_name: str):
    """delay = OUT - ENTRY matched by same idx."""
    ent = {idx: ts for ts, idx in marks.get(entry_name, [])}
    out = {idx: ts for ts, idx in marks.get(out_name, [])}
    common = sorted(set(ent.keys()) & set(out.keys()))
    delays = []
    for idx in common:
        d = out[idx] - ent[idx]
        if d >= 0:
            delays.append(d)
    return delays


def match_delay_by_time(marks, later_name: str, earlier_name: str):
    """
    delay = later_ts - earlier_ts using monotonic 1:1 matching by time:
    for each later event, take the latest earlier event with ts <= later_ts,
    and consume it once.
    """
    later = [ts for ts, _idx in marks.get(later_name, [])]
    earlier = [ts for ts, _idx in marks.get(earlier_name, [])]
    delays = []

    i = 0  # pointer in earlier
    last_valid = None
    for ts_l in later:
        while i < len(earlier) and earlier[i] <= ts_l:
            last_valid = earlier[i]
            i += 1
        if last_valid is None:
            continue
        d = ts_l - last_valid
        if d >= 0:
            delays.append(d)
        last_valid = None  # consume one earlier per later (1:1)
    return delays


def interarrival_dts(marks, name: str):
    """dt list between consecutive timestamps for a given mark name."""
    ts = [t for t, _ in marks.get(name, [])]
    if len(ts) < 2:
        return []
    return [ts[i] - ts[i - 1] for i in range(1, len(ts)) if ts[i] - ts[i - 1] >= 0]


def summarize(label: str, xs_us):
    if not xs_us:
        print(f"{label}: no data")
        return
    xs_ms = [x / 1000.0 for x in xs_us]
    print(
        f"{label}: n={len(xs_ms)}  mean={mean(xs_ms):.3f} ms  std={pstdev(xs_ms):.3f} ms  "
        f"min={min(xs_ms):.3f} ms  max={max(xs_ms):.3f} ms"
    )


def boxplot(title: str, series_dict_us, ylabel: str):
    labels = []
    data = []
    for k, xs in series_dict_us.items():
        if xs:
            labels.append(k)
            data.append([x / 1000.0 for x in xs])  # us -> ms

    if not data:
        print(f"[plot] '{title}': no data to plot")
        return

    plt.figure()
    plt.boxplot(data, labels=labels, showfliers=False)
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=25, ha="right")
    plt.tight_layout()


def boxplot_one(title: str, label: str, xs_us, ylabel: str):
    if not xs_us:
        print(f"[plot] {title}: no data")
        return
    xs_ms = [x / 1000.0 for x in xs_us]  # us -> ms
    plt.figure()
    plt.boxplot([xs_ms], labels=[label], showfliers=False)
    plt.title(title)
    plt.ylabel(ylabel)
    plt.tight_layout()


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_jitter_delay.py sensor_timing_metrics.csv")
        sys.exit(1)

    path = sys.argv[1]
    marks = read_marks(path)

    # --- Delays (pair by idx) ---
    emg_hal_delay = pair_delay_by_idx(marks, "EMG_HAL_ENTRY", "EMG_HAL_OUT")
    acc_hal_delay = pair_delay_by_idx(marks, "ACC_HAL_ENTRY", "ACC_HAL_OUT")

    # --- Queue/Scheduling delays into fusion (match by time) ---
    emg_to_fusion_in = match_delay_by_time(marks, "BLEManager_Fusion_in", "EMG_HAL_OUT")
    acc_to_fusion_in = match_delay_by_time(marks, "Fusion_Feature_Accel_ENTRY", "ACC_HAL_OUT")

    # --- Jitter as inter-arrival time distributions ---
    jitter_points = [
        "EMG_HAL_ENTRY",
        "EMG_HAL_OUT",
        "ACC_HAL_ENTRY",
        "ACC_HAL_OUT",
        "BLEManager_Fusion_in",
        "Fusion_Feature_Accel_ENTRY",
        "Fusion_Feature_Accel_EXIT",
    ]
    jitters = {name: interarrival_dts(marks, name) for name in jitter_points}

# --- Delays einzeln ---
    boxplot_one("Delay EMG HAL", "EMG_HAL_OUT-ENTRY", emg_hal_delay, "Delay [ms]")
    boxplot_one("Delay ACC HAL", "ACC_HAL_OUT-ENTRY", acc_hal_delay, "Delay [ms]")
    boxplot_one("QueueDelay EMG -> Fusion_in", "EMG_OUT->Fusion_in", emg_to_fusion_in, "Delay [ms]")
    boxplot_one("QueueDelay ACC -> Fusion_in", "ACC_OUT->Fusion_in", acc_to_fusion_in, "Delay [ms]")

# --- Jitter einzeln (Inter-arrival dt) ---
    for name, dts_us in jitters.items():
        boxplot_one(f"Jitter (dt) @ {name}", name, dts_us, "dt [ms]")

    plt.show()


if __name__ == "__main__":
    main()
