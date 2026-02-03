import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

CSV_PATH = "timing_metrics.csv"   # ggf. anpassen

cols = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]
df = pd.read_csv(CSV_PATH, header=None, names=cols)

df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
df = df.dropna(subset=["ts_us", "name"])
df["name"] = df["name"].astype(str)

OUTLIER_FACTOR = 5.0  # lange Pausen raus (reconnect/stop etc.)

def compute_jitter_by_name(df_in: pd.DataFrame) -> dict[str, np.ndarray]:
    out = {}
    for name, g in df_in.groupby("name"):
        g = g.sort_values("ts_us")
        dt = g["ts_us"].diff().astype("float64")
        dt = dt[dt > 0]
        if len(dt) < 3:
            continue

        expected = float(dt.median())
        lo, hi = expected / OUTLIER_FACTOR, expected * OUTLIER_FACTOR
        dt_f = dt[(dt >= lo) & (dt <= hi)]

        out[name] = (dt_f - expected).abs().to_numpy()
    return out

def violin_like_gallery(j_by_name: dict[str, np.ndarray], title: str):
    if not j_by_name:
        print(f"{title}: keine Daten")
        return

    names = sorted(j_by_name.keys(), key=lambda n: len(j_by_name[n]), reverse=True)
    data = [j_by_name[n] for n in names]
    pos = np.arange(1, len(names) + 1)

    # pro Violin dieselben Quantile (wie im Matplotlib-Beispiel via `quantiles=...`) :contentReference[oaicite:1]{index=1}
    quantiles = [[0.25, 0.5, 0.75] for _ in data]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.violinplot(
        data,
        pos,
        points=100,            # wie im Beispiel: KDE-Auflösung :contentReference[oaicite:2]{index=2}
        widths=0.7,
        showmeans=True,
        showmedians=True,
        showextrema=True,
        bw_method="silverman", # wie im Beispiel (alternativ: 0.5) :contentReference[oaicite:3]{index=3}
        quantiles=quantiles    # Quantil-Linien im Violin :contentReference[oaicite:4]{index=4}
        # side="both"          # optional: "low"/"high" wie im Beispiel :contentReference[oaicite:5]{index=5}
    )

    ax.set_title(title)
    ax.set_ylabel("Jitter [µs]")
    ax.set_xticks(pos)
    ax.set_xticklabels(names, rotation=35, ha="right")
    fig.tight_layout()

# --- Split ACC vs EMG (nach Name) ---
acc_df = df[df["name"].str.contains("ACC", case=False, na=False)]
emg_df = df[df["name"].str.contains("EMG", case=False, na=False)]

violin_like_gallery(compute_jitter_by_name(acc_df), "Jitter: ACC (Matplotlib-Gallery Violin)")
violin_like_gallery(compute_jitter_by_name(emg_df), "Jitter: EMG (Matplotlib-Gallery Violin)")

plt.show()