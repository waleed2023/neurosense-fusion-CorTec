import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# =========================
# Configuration
# =========================
CSV_PATH = r"C:\gitcheckouts\neurosense-fusion\plots\timing_metrics.csv"
COLS = ["kind", "name", "idx", "ts_us", "dt_us", "abs_dev_us", "delay_us"]

# =========================
# Analysis Functions
# =========================

def read_csv_to_groups(filename: str, cols: list) -> dict:
    """Read CSV file and return groups dictionary."""
    df = pd.read_csv(filename, header=None, names=cols)
    df = df[df["kind"] == "mark"].copy()
    
    df["idx"] = pd.to_numeric(df["idx"], errors="coerce")
    df["ts_us"] = pd.to_numeric(df["ts_us"], errors="coerce")
    df = df.dropna(subset=["name", "idx", "ts_us"])
    
    groups = {n: g.sort_values(["idx", "ts_us"]) for n, g in df.groupby("name")}
    return groups

def column_to_array(groups: dict, column_name: str, column_key: str = "ts_us") -> np.ndarray:
    """Convert a column from groups dict to numpy array."""
    if column_name in groups:
        return groups[column_name][column_key].to_numpy()
    return np.array([])

def print_statistics(data: np.ndarray, label: str, unit: str) -> None:
    """Calculate and print statistics for the given data."""
    if len(data) == 0:
        print(f"{label}: No data available")
        return
    
    mean_val = np.mean(data)
    median_val = np.median(data)
    percentiles = np.percentile(data, [0, 25, 50, 75, 100])
    
    print(f"{label} Statistics:")
    print(f"  Min (0%):      {percentiles[0]:.2f} {unit}")
    print(f"  Q1 (25%):      {percentiles[1]:.2f} {unit}")
    print(f"  Median (50%):  {percentiles[2]:.2f} {unit}")
    print(f"  Q3 (75%):      {percentiles[3]:.2f} {unit}")
    print(f"  Max (100%):    {percentiles[4]:.2f} {unit}")
    print(f"  Mean:          {mean_val:.2f} {unit}")

def show_boxplot(ax, data: np.ndarray, position: int, label: str, ylabel: str, title: str, 
                 xticks: list, xticklabels: list) -> None:
    """Show boxplot on the given axis."""
    ax.boxplot([data], positions=[position], widths=0.6, patch_artist=True,
               boxprops=dict(facecolor='lightblue', alpha=0.7),
               medianprops=dict(color='red', linewidth=2))
    
    # Configure axis
    ax.set_xticks(xticks)
    ax.set_xticklabels(xticklabels)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.2)

def show_violinplot(ax, data: np.ndarray, position: int, label: str, ylabel: str, title: str,
                    xticks: list, xticklabels: list) -> None:
    """Show violinplot on the given axis."""
    ax.violinplot([data], positions=[position], widths=0.7)
    
    # Configure axis
    ax.set_xticks(xticks)
    ax.set_xticklabels(xticklabels)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.2)

# =========================
# Main Analysis
# =========================

def main():
    # Read CSV and prepare groups
    groups = read_csv_to_groups(CSV_PATH, COLS)
    
    # Process EMG_HAL_ENTRY
    emg_hal_timestamps = column_to_array(groups, "EMG_HAL_ENTRY")
    emg_diff = np.diff(emg_hal_timestamps)
    print_statistics(emg_diff, "EMG_DIFF", "µs")
    
    fig1, ax1 = plt.subplots(figsize=(10, 6))
    show_violinplot(ax1, emg_diff, position=1, label="Violinplot", 
                    ylabel="Time Difference [µs]", title="EMG HAL Entry - Violinplot vs Boxplot",
                    xticks=[1, 2], xticklabels=["Violinplot", "Boxplot"])
    show_boxplot(ax1, emg_diff, position=2, label="Boxplot", 
                 ylabel="Time Difference [µs]", title="EMG HAL Entry - Violinplot vs Boxplot",
                 xticks=[1, 2], xticklabels=["Violinplot", "Boxplot"])
    fig1.tight_layout()
    
    # Process ACC_HAL_ENTRY
    acc_hal_timestamps = column_to_array(groups, "ACC_HAL_ENTRY")
    acc_diff = np.diff(acc_hal_timestamps)
    print_statistics(acc_diff, "ACC_DIFF", "µs")
    
    fig2, ax2 = plt.subplots(figsize=(10, 6))
    show_violinplot(ax2, acc_diff, position=1, label="Violinplot", 
                    ylabel="Time Difference [µs]", title="Accelerometer HAL Entry - Violinplot vs Boxplot",
                    xticks=[1, 2], xticklabels=["Violinplot", "Boxplot"])
    show_boxplot(ax2, acc_diff, position=2, label="Boxplot", 
                 ylabel="Time Difference [µs]", title="Accelerometer HAL Entry - Violinplot vs Boxplot",
                 xticks=[1, 2], xticklabels=["Violinplot", "Boxplot"])
    fig2.tight_layout()
    
    plt.show()

if __name__ == "__main__":
    main()

