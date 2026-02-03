import pandas as pd

df = pd.read_csv("timing_metrics.csv", header=None,
                 names=["kind","name","idx","ts_us","c4","c5","c6"])

def rate(mark):
    x = df[df["name"] == mark].sort_values("ts_us")
    dt = x["ts_us"].diff().dropna()
    hz = 1_000_000 / dt.mean()
    print(mark, "samples:", len(x), "rate(Hz):", round(hz, 2),
          "median_dt(us):", round(dt.median(), 1),
          "min/max_dt(us):", int(dt.min()), "/", int(dt.max()))

rate("Fusion_Dummy_ACC_Exit")
rate("Fusion_Dummy_EMG_Exit")
