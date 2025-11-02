import os
import argparse
import json
from typing import Dict, Any, List

import pandas as pd

from exit_optimizer_core import (
    read_report_csv,
    load_ticks_for_window,
    ExitParams,
    simulate_trade,
    compute_metrics,
    save_equity_curve,
    save_r_distribution,
)


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(PROJECT_ROOT, "ReportHistory.csv")


def get_trade_window(row: pd.Series) -> (pd.Timestamp, pd.Timestamp):
    start = row["Time"]
    end = row["Time.1"] if pd.notna(row.get("Time.1", pd.NaT)) else (start + pd.Timedelta(days=3))
    return start, end


def main():
    parser = argparse.ArgumentParser(description="Run a single exit configuration on tick data")
    parser.add_argument("--params", type=str, required=False, help="JSON string of parameters")
    parser.add_argument("--out_equity", type=str, default=os.path.join(PROJECT_ROOT, "equity_curve_best.png"))
    parser.add_argument("--out_hist", type=str, default=os.path.join(PROJECT_ROOT, "r_distribution_best.png"))
    args = parser.parse_args()

    if args.params:
        p_dict = json.loads(args.params)
    else:
        # default recommended config
        p_dict = {
            "scaleout_r": None,
            "scaleout_frac": 0.0,
            "be_trigger_r": None,
            "be_back_r": 0.0,
            "tp_r": None,
            "trailing_start_r": 1.5,
            "trailing_gap_r": 0.7,
        }

    params = ExitParams(**p_dict)
    df_report = read_report_csv(REPORT_PATH)
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

    metrics = compute_metrics(r_values)
    print(json.dumps(metrics, indent=2))
    save_equity_curve(r_values, args.out_equity)
    save_r_distribution(r_values, args.out_hist)


if __name__ == "__main__":
    main()


