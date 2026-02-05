import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

EMG_ENTRY = "Fusion_Dummy_EMG_Exit"
DEFAULT_COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

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

def sliding_rate_plot(ts_us: np.ndarray, win_s: float = 1.0, ref_hz: float = 10.0):
    ts = np.sort(ts_us.astype(np.int64))
    if len(ts) < 2:
        print("Zu wenig Daten (weniger als 2 Timestamps).")
        return

    # Zeit relativ zum Start (Sekunden)
    t = (ts - ts[0]) / 1e6
    t_end = float(t[-1])

    # Nur volle Fenster plotten (verhindert Drop im letzten unvollständigen Window)
    t_end_full = np.floor(t_end / win_s) * win_s
    if t_end_full <= 0:
        print("Zu kurze Messdauer für Sliding-Window-Plot.")
        return

    edges = np.arange(0.0, t_end_full + win_s, win_s)
    counts, _ = np.histogram(t, bins=edges)

    rate = counts / win_s
    centers = edges[:-1] + win_s / 2
    avg_rate = float(np.mean(rate)) if len(rate) else float("nan")

    # ---- Plot (thesis style) ----
    fig, ax = plt.subplots(figsize=(10, 3))  # neutrales Seitenverhältnis
    ax.plot(centers, rate, linewidth=1.0, label=f"rate ({win_s:.1f}s window)")

    # Referenz + Mittelwert
    ax.axhline(ref_hz, linestyle="--", linewidth=1.0, label=f"ref {ref_hz:.0f} Hz")
    ax.axhline(avg_rate, linestyle=":", linewidth=1.0, label=f"mean {avg_rate:.1f} Hz")

    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Rate [Hz]")
    ax.set_title("Effective rate (EMG)")
    ax.set_xlim(0, t_end_full)

    # Kleine, neutrale Info-Box (oben rechts)
    txt = f"ref: {ref_hz:.0f} Hz\nmean: {avg_rate:.1f} Hz"
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

    # Optional: Legende weglassen (Box reicht). Wenn du willst, aktivieren:
    # ax.legend(loc="lower right", fontsize=8, frameon=False)

    plt.tight_layout()
    plt.show()

def main():
    if len(sys.argv) < 2:
        print("Usage: python plot_rate.py <timing_metrics.csv>")
        sys.exit(1)

    df = load_csv(sys.argv[1])
    ts = df.loc[df["name"] == EMG_ENTRY, "ts_us"].to_numpy(np.int64)

    if len(ts) == 0:
        print(f"Keine Daten für mark '{EMG_ENTRY}' gefunden.")
        sys.exit(1)

    sliding_rate_plot(ts, win_s=1.0, ref_hz=10)

if __name__ == "__main__":
    main()
