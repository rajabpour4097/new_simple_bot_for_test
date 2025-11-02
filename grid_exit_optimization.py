import os
import json
from typing import List, Dict, Any, Optional

import pandas as pd
import numpy as np

from exit_optimizer_core import (
    read_report_csv,
    load_ticks_for_window,
    ExitParams,
    simulate_trade,
    compute_metrics,
    monte_carlo_maxdd,
    save_equity_curve,
    save_r_distribution,
    params_to_dict,
    grid_space_iter,
)


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(PROJECT_ROOT, "ReportHistory.csv")
OUTPUT_ALL = os.path.join(PROJECT_ROOT, "exit_grid_results.csv")
OUTPUT_TOP20 = os.path.join(PROJECT_ROOT, "exit_grid_top20.csv")
OUTPUT_BEST_TXT = os.path.join(PROJECT_ROOT, "best_config.txt")
OUTPUT_BEST_EQUITY = os.path.join(PROJECT_ROOT, "equity_curve_best.png")
OUTPUT_BEST_DIST = os.path.join(PROJECT_ROOT, "r_distribution_best.png")


def get_trade_window(row: pd.Series) -> (pd.Timestamp, pd.Timestamp):
    start = row["Time"]
    # if explicit close not available, add a reasonable window (e.g., 3 days)
    end = row["Time.1"] if pd.notna(row.get("Time.1", pd.NaT)) else (start + pd.Timedelta(days=3))
    return start, end


def run_grid_search() -> None:
    df_report = read_report_csv(REPORT_PATH)
    if df_report.empty:
        print("No trades in report after filtering. Exiting.")
        return

    # grid space definition per user spec
    grid_space = {
        "scaleout_r": [None, 1.0],
        "scaleout_frac": [0.0, 0.5, 0.6],
        "be_trigger_r": [None, 0.2, 0.5, 0.7],
        "be_back_r": [0.0, 0.1],
        "tp_r": [1.2, 1.5, 2.0, None],
        "trailing_start_r": [1.5, 2.0, None],
        "trailing_gap_r": [0.4, 0.5, 0.7],
    }

    all_results: List[Dict[str, Any]] = []

    # Pre-group report trades by symbol to limit tick IO
    df_report = df_report.sort_values("Time")
    skipped_no_ticks = 0
    total_considered = 0

    for params_dict in grid_space_iter(grid_space):
        params = ExitParams(**params_dict)
        r_values: List[float] = []
        reasons: List[str] = []

        for _, row in df_report.iterrows():
            total_considered += 1
            symbol = str(row["Symbol"]).upper()
            start, end = get_trade_window(row)
            ticks = load_ticks_for_window(symbol, start, end, PROJECT_ROOT)
            if ticks.empty:
                skipped_no_ticks += 1
                continue
            sim = simulate_trade(row, ticks, params)
            if sim is None:
                continue
            r_values.append(sim.r_total)
            reasons.append(sim.exit_reason)

        metrics = compute_metrics(r_values)
        all_results.append({
            **params_dict,
            "n_trades": len(r_values),
            "skipped_no_ticks": skipped_no_ticks,
            **metrics,
            "reasons_sample": reasons[:5],
        })

    # Save full grid
    df_all = pd.DataFrame(all_results)
    df_all = df_all.sort_values(by=["average_R"], ascending=False)
    df_all.to_csv(OUTPUT_ALL, index=False)

    # Top 20
    df_top = df_all.head(20).copy()
    df_top.to_csv(OUTPUT_TOP20, index=False)

    # Best config artifacts
    if not df_all.empty:
        best = df_all.iloc[0].to_dict()
        best_params = {k: best[k] for k in [
            "scaleout_r", "scaleout_frac", "be_trigger_r", "be_back_r",
            "tp_r", "trailing_start_r", "trailing_gap_r",
        ]}

        # Re-simulate best to get full r_values
        params = ExitParams(**best_params)
        r_values: List[float] = []
        for _, row in df_report.iterrows():
            symbol = str(row["Symbol"]).upper()
            start, end = get_trade_window(row)
            ticks = load_ticks_for_window(symbol, start, end, PROJECT_ROOT)
            if ticks.empty:
                continue
            sim = simulate_trade(row, ticks, params)
            if sim is None:
                continue
            r_values.append(sim.r_total)

        mc = monte_carlo_maxdd(r_values, runs=1500)

        # write best config summary
        with open(OUTPUT_BEST_TXT, "w", encoding="utf-8") as f:
            f.write("Best Exit Parameters\n")
            f.write(json.dumps(best_params, indent=2))
            f.write("\n\nMetrics (grid):\n")
            f.write(json.dumps({
                "average_R": best.get("average_R"),
                "profit_factor": best.get("profit_factor"),
                "win_rate": best.get("win_rate"),
                "max_drawdown_R": best.get("max_drawdown_R"),
                "n_trades": best.get("n_trades"),
            }, indent=2))
            f.write("\n\nMonte Carlo MaxDD (R):\n")
            f.write(json.dumps(mc, indent=2))

        # charts
        save_equity_curve(r_values, OUTPUT_BEST_EQUITY)
        save_r_distribution(r_values, OUTPUT_BEST_DIST)

    print("Grid search complete.")
    print(f"Results -> {OUTPUT_ALL}\nTop20 -> {OUTPUT_TOP20}\nBest -> {OUTPUT_BEST_TXT}")


if __name__ == "__main__":
    run_grid_search()


