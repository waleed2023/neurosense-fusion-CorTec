import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# =========================
# Einstellungen
# =========================
CSV_PATH = "timing_metrics_dummy.csv"   # <- anpassen
MARK = "EMG_HAL_ENTRY"                  # <- anpassen
WIN_S = 1.0                             # Fenster in Sekunden

# Wenn ein Notify 2 EMG-Samples enthält -> 2 (bei dir sehr wahrscheinlich!)
SAMPLES_PER_PACKET = 2

# Dedupe: Notifies, die in kurzem Abstand doppelt geloggt werden
DEDUP_WITHIN_US = 5_000  # 5 ms

# Referenzlinien
REF_PACKET_HZ = 5.0      # Packet/Notify-Rate (bei 2 Samples/Packet entspricht das ~10Hz Sample)
REF_SAMPLE_HZ = 10.0     # Sample-Rate (10 Hz)

DEFAULT_COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]


# =========================
# Helper
# =========================
def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    # Falls ohne Header geloggt:
    if "name" not in df.columns or "ts_us" not in df.columns:
        df = pd.read_csv(path, header=None)
        n = df.shape[1]
        df.columns = DEFAULT_COLS[:n] + [f"col{i}" for i in range(len(DEFAULT_COLS[:n]), n)]

    # Optional: nur Marks verwenden
    if "kind" in df.columns:
        df["kind"] = df["kind"].astype(str)
        df = df[df["kind"] == "mark"].copy()

    df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
    df = df.dropna(subset=["name", "ts_us"])
    df["ts_us"] = df["ts_us"].astype(np.int64)
    df["name"] = df["name"].astype(str)
    return df


def dedupe_bursts(ts_us: np.ndarray, min_gap_us: int) -> np.ndarray:
    """Entfernt aufeinanderfolgende Timestamps, die innerhalb min_gap_us liegen (behält jeweils den ersten)."""
    ts = np.sort(np.asarray(ts_us, dtype=np.int64))
    if len(ts) == 0:
        return ts
    kept = [ts[0]]
    last = ts[0]
    for t in ts[1:]:
        if t - last > min_gap_us:
            kept.append(t)
            last = t
    return np.array(kept, dtype=np.int64)


def effective_rate(ts_us: np.ndarray, win_s: float, multiplier: float = 1.0):
    """Rate = counts pro Fenster * multiplier (nur volle Fenster)."""
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 2:
        return np.array([]), np.array([])

    t = (ts - ts[0]) / 1e6  # seconds
    t_end_full = np.floor(t[-1] / win_s) * win_s
    if t_end_full <= 0:
        return np.array([]), np.array([])

    edges = np.arange(0.0, t_end_full + win_s, win_s)
    counts, _ = np.histogram(t, bins=edges)
    rate = (counts * multiplier) / win_s
    centers = edges[:-1] + win_s / 2
    return centers, rate


def mean_rate_total(ts_us: np.ndarray, multiplier: float = 1.0) -> float:
    """Robuster Mean über Gesamtdauer."""
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 2:
        return float("nan")
    dur_s = (ts[-1] - ts[0]) / 1e6
    if dur_s <= 0:
        return float("nan")
    # (n-1) Intervals in dur_s; * multiplier für Samples/Packet
    return ((len(ts) - 1) * multiplier) / dur_s


def dt_median_ms(ts_us: np.ndarray) -> float:
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 2:
        return float("nan")
    dt = np.diff(ts)
    dt = dt[dt > 0]
    if len(dt) == 0:
        return float("nan")
    return float(np.median(dt) / 1000.0)


def plot_rate(x, y, title, ref_hz=None, box_lines=None):
    plt.figure(figsize=(8.5, 3.4))
    plt.plot(x, y, linewidth=1.0)

    if ref_hz is not None:
        plt.axhline(ref_hz, linestyle="--", linewidth=1.0)

    ymax = np.max(y) if len(y) else 1.0
    if ref_hz is not None:
        ymax = max(ymax, ref_hz)
    plt.ylim(0, ymax * 1.1 if ymax > 0 else 1.0)
    plt.xlim(0, x[-1] + WIN_S / 2)

    # Textbox oben rechts
    if box_lines:
        box_text = "\n".join(box_lines)
        plt.text(
            0.98, 0.98, box_text,
            transform=plt.gca().transAxes,
            ha="right", va="top",
            fontsize=9,
            bbox=dict(boxstyle="round", facecolor="white", edgecolor="0.5", alpha=0.9),
        )

    plt.title(title)
    plt.xlabel("Time [s]")
    plt.ylabel("Rate [Hz]")
    plt.grid(True, linewidth=0.3, alpha=0.4)
    plt.tight_layout()


# =========================
# Main
# =========================
def main():
    df = load_csv(CSV_PATH)

    ts_raw = df.loc[df["name"] == MARK, "ts_us"].to_numpy(np.int64)
    if len(ts_raw) == 0:
        print(f"Mark not found: {MARK}")
        return

    # Packet/Notify Timestamps: dedup gegen doppelte Notifies
    ts_pkt = dedupe_bursts(ts_raw, DEDUP_WITHIN_US)

    # Diagnostics
    dt_med_pkt = dt_median_ms(ts_pkt)
    mean_pkt_total = mean_rate_total(ts_pkt, multiplier=1.0)
    mean_samp_total = mean_rate_total(ts_pkt, multiplier=float(SAMPLES_PER_PACKET))

    print(f"--- {MARK} ---")
    print(f"raw events: {len(ts_raw)}")
    print(f"packets (after dedupe<= {DEDUP_WITHIN_US/1000:.1f}ms): {len(ts_pkt)}")
    print(f"packet Δt median: {dt_med_pkt:.3f} ms  -> ~{(1000.0/dt_med_pkt) if dt_med_pkt>0 else float('nan'):.3f} Hz")
    print(f"mean packet rate (total): {mean_pkt_total:.3f} Hz")
    print(f"mean sample rate (total, x{SAMPLES_PER_PACKET}): {mean_samp_total:.3f} Hz")

    # Packet rate plot
    x_pkt, y_pkt = effective_rate(ts_pkt, WIN_S, multiplier=1.0)
    if len(y_pkt) == 0:
        print("Not enough data for packet rate plot.")
        return

    mean_pkt_win = float(np.mean(y_pkt))
    plot_rate(
        x_pkt, y_pkt,
        title=f"Effective rate — EMG (HAL entry, packets) [{MARK}]",
        ref_hz=REF_PACKET_HZ,
        box_lines=[
            f"ref: {REF_PACKET_HZ:.1f} Hz",
            f"mean(win): {mean_pkt_win:.1f} Hz",
            f"mean(total): {mean_pkt_total:.1f} Hz",
        ],
    )

    # Sample rate (estimated): packet counts * SAMPLES_PER_PACKET
    x_s, y_s = effective_rate(ts_pkt, WIN_S, multiplier=float(SAMPLES_PER_PACKET))
    mean_s_win = float(np.mean(y_s)) if len(y_s) else float("nan")
    plot_rate(
        x_s, y_s,
        title=f"Effective rate — EMG (HAL entry, samples ≈ packets×{SAMPLES_PER_PACKET}) [{MARK}]",
        ref_hz=REF_SAMPLE_HZ,
        box_lines=[
            f"ref: {REF_SAMPLE_HZ:.1f} Hz",
            f"mean(win): {mean_s_win:.1f} Hz",
            f"mean(total): {mean_samp_total:.1f} Hz",
        ],
    )

    plt.show()


if __name__ == "__main__":
    main()
