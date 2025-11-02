import os
import math
import itertools
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # ensure non-interactive backend
import matplotlib.pyplot as plt


# -----------------------------
# Data loading and parsing
# -----------------------------

REPORT_TIME_FORMATS = ["%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"]


def read_report_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Report CSV not found at {path}")
    df = pd.read_csv(path)

    required_cols = [
        "Time", "Position", "Symbol", "Type", "Volume",
        "Price", "S / L", "T / P", "Time.1", "Price.1",
    ]
    for c in required_cols:
        if c not in df.columns:
            raise ValueError(f"Missing required report column: {c}")

    # parse times with multiple possible formats
    def parse_time(val: Any) -> pd.Timestamp:
        if pd.isna(val):
            return pd.NaT
        if isinstance(val, (pd.Timestamp, np.datetime64)):
            return pd.to_datetime(val)
        s = str(val)
        for fmt in REPORT_TIME_FORMATS:
            try:
                return pd.to_datetime(s, format=fmt)
            except Exception:
                continue
        # final attempt: pandas parser
        try:
            return pd.to_datetime(s, errors="coerce")
        except Exception:
            return pd.NaT

    df["Time"] = df["Time"].apply(parse_time)
    df["Time.1"] = df["Time.1"].apply(parse_time)

    # normalize type -> buy/sell
    df["Type"] = df["Type"].astype(str).str.lower()
    df = df[df["Type"].isin(["buy", "sell"])].copy()

    # ensure numeric columns
    for col in ["Price", "S / L", "T / P", "Price.1", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Time", "Symbol", "Type", "Price", "S / L"]).copy()

    return df


def read_ticks_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=["time", "bid", "ask"])  # allow missing months
    df = pd.read_csv(path)
    for c in ["time", "bid", "ask"]:
        if c not in df.columns:
            raise ValueError(f"Missing required ticks column: {c} in {path}")
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).copy()
    df = df.sort_values("time")
    return df


def load_ticks_for_window(symbol: str, start: pd.Timestamp, end: pd.Timestamp, root: str) -> pd.DataFrame:
    # naive loader: look 3 surrounding months
    month_keys: List[Tuple[int, int]] = []
    cur = pd.Timestamp(year=start.year, month=start.month, day=1)
    last = pd.Timestamp(year=end.year, month=end.month, day=1)
    while cur <= last:
        month_keys.append((cur.year, cur.month))
        cur = (cur + pd.offsets.MonthBegin(1))

    frames: List[pd.DataFrame] = []
    ticks_dir = os.path.join(root, "ticks")
    for y, m in month_keys:
        path = os.path.join(ticks_dir, f"Ticks_{symbol}_{y:04d}_{m:02d}.csv")
        frames.append(read_ticks_csv(path))

    if not frames:
        return pd.DataFrame(columns=["time", "bid", "ask"]).copy()
    merged = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if merged.empty:
        return merged
    # filter by window
    mask = (merged["time"] >= start) & (merged["time"] <= end)
    return merged.loc[mask].copy()


# -----------------------------
# Simulation logic
# -----------------------------

@dataclass
class ExitParams:
    scaleout_r: Optional[float] = None
    scaleout_frac: float = 0.0
    be_trigger_r: Optional[float] = None
    be_back_r: float = 0.0
    tp_r: Optional[float] = None
    trailing_start_r: Optional[float] = None
    trailing_gap_r: float = 0.7


@dataclass
class SimResult:
    r_total: float
    exit_reason: str
    n_events: int


def compute_risk(entry: float, sl: float) -> float:
    return abs(entry - sl)


def price_for_stream(row_type: str, bid: float, ask: float) -> float:
    # buy -> exit on bid, sell -> exit on ask
    if row_type == "buy":
        return bid
    return ask


def simulate_trade(report_row: pd.Series, ticks: pd.DataFrame, params: ExitParams) -> Optional[SimResult]:
    # inputs: report_row fields: Type, Price(entry), S / L, T / P(optional), Time, Time.1 (optional close window)
    entry = float(report_row["Price"]) if not math.isnan(report_row["Price"]) else None
    sl = float(report_row["S / L"]) if not math.isnan(report_row["S / L"]) else None
    row_type = str(report_row["Type"]).lower()

    if entry is None or sl is None:
        return None
    risk = compute_risk(entry, sl)
    if risk <= 0:
        return None

    is_buy = row_type == "buy"

    # helper to compute R for a given price
    def r_multiple(price: float) -> float:
        if is_buy:
            return (price - entry) / risk
        else:
            return (entry - price) / risk

    # event prices
    sl_price = sl
    tp_price = None if params.tp_r is None else (entry + params.tp_r * risk if is_buy else entry - params.tp_r * risk)
    scale_price = None if params.scaleout_r is None else (entry + params.scaleout_r * risk if is_buy else entry - params.scaleout_r * risk)
    be_trigger_price = None if params.be_trigger_r is None else (entry + params.be_trigger_r * risk if is_buy else entry - params.be_trigger_r * risk)

    # trailing
    trailing_active = False
    trail_anchor_price = None
    trail_stop_price = None

    # state for scaleout
    remaining_frac = 1.0
    realized_r = 0.0
    events_count = 0

    if ticks.empty:
        return None

    # iterate ticks in time
    for i in range(len(ticks)):
        row = ticks.iloc[i]
        cur_price = price_for_stream(row_type, bid=row.get("bid", np.nan), ask=row.get("ask", np.nan))
        if np.isnan(cur_price):
            continue

        # compute boolean hits
        # SL hit check
        sl_hit = (cur_price <= sl_price) if is_buy else (cur_price >= sl_price)

        # trailing activation
        if (not trailing_active) and (params.trailing_start_r is not None):
            target_start = entry + params.trailing_start_r * risk if is_buy else entry - params.trailing_start_r * risk
            start_reached = (cur_price >= target_start) if is_buy else (cur_price <= target_start)
            if start_reached:
                trailing_active = True
                trail_anchor_price = cur_price
                gap = params.trailing_gap_r * risk
                trail_stop_price = trail_anchor_price - gap if is_buy else trail_anchor_price + gap
                events_count += 1

        # update trailing if active: move anchor in favorable direction
        if trailing_active:
            if (is_buy and cur_price > trail_anchor_price) or ((not is_buy) and cur_price < trail_anchor_price):
                trail_anchor_price = cur_price
                gap = params.trailing_gap_r * risk
                trail_stop_price = trail_anchor_price - gap if is_buy else trail_anchor_price + gap

        # BE logic
        if be_trigger_price is not None and ((is_buy and cur_price >= be_trigger_price) or ((not is_buy) and cur_price <= be_trigger_price)):
            be_back = params.be_back_r * risk
            be_price = entry - be_back if is_buy else entry + be_back
            # BE stop acts like a dynamic SL from now on if tighter than current stop
            if is_buy:
                sl_price = max(sl_price, be_price)
            else:
                sl_price = min(sl_price, be_price)
            be_trigger_price = None  # one-off trigger
            events_count += 1

        # scaleout logic (one-shot at exact R threshold or beyond)
        if scale_price is not None and ((is_buy and cur_price >= scale_price) or ((not is_buy) and cur_price <= scale_price)):
            part = params.scaleout_frac
            if part > 0:
                realized_r += part * r_multiple(cur_price)
                remaining_frac = max(0.0, 1.0 - part)
            scale_price = None  # only once
            events_count += 1

        # TP direct
        if tp_price is not None and ((is_buy and cur_price >= tp_price) or ((not is_buy) and cur_price <= tp_price)):
            realized_r += remaining_frac * r_multiple(tp_price)
            events_count += 1
            return SimResult(r_total=realized_r, exit_reason="tp_direct", n_events=events_count)

        # trailing stop check
        if trailing_active and trail_stop_price is not None:
            trail_hit = (cur_price <= trail_stop_price) if is_buy else (cur_price >= trail_stop_price)
            if trail_hit:
                realized_r += remaining_frac * r_multiple(trail_stop_price)
                events_count += 1
                return SimResult(r_total=realized_r, exit_reason="trail", n_events=events_count)

        # SL check after possible BE tightening
        if sl_hit:
            realized_r += remaining_frac * r_multiple(sl_price)
            events_count += 1
            return SimResult(r_total=realized_r, exit_reason="sl", n_events=events_count)

    # If we reach end without events, exit at last price
    last_row = ticks.iloc[-1]
    last_price = price_for_stream(row_type, bid=last_row.get("bid", np.nan), ask=last_row.get("ask", np.nan))
    if np.isnan(last_price):
        return None
    realized_r += remaining_frac * r_multiple(last_price)
    return SimResult(r_total=realized_r, exit_reason="end_series", n_events=events_count)


# -----------------------------
# Metrics
# -----------------------------

def compute_metrics(r_values: List[float]) -> Dict[str, float]:
    arr = np.array(r_values, dtype=float)
    if arr.size == 0:
        return {"average_R": np.nan, "profit_factor": np.nan, "win_rate": np.nan, "max_drawdown_R": np.nan}
    avg = float(np.nanmean(arr))
    pos_sum = float(arr[arr > 0].sum())
    neg_sum = float(arr[arr <= 0].sum())
    pf = np.nan
    if neg_sum < 0:
        pf = pos_sum / abs(neg_sum) if abs(neg_sum) > 0 else np.inf
    win_rate = float((arr > 0).mean())
    # equity curve drawdown in R
    equity = np.cumsum(arr)
    peak = -np.inf
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    return {
        "average_R": avg,
        "profit_factor": float(pf),
        "win_rate": win_rate,
        "max_drawdown_R": float(max_dd),
    }


def monte_carlo_maxdd(r_values: List[float], runs: int = 1500, rng_seed: int = 42) -> Dict[str, float]:
    rng = np.random.default_rng(rng_seed)
    arr = np.array(r_values, dtype=float)
    if arr.size == 0:
        return {"p50": np.nan, "p95": np.nan, "p99": np.nan}
    dd_list = []
    for _ in range(runs):
        shuf = rng.permutation(arr)
        equity = np.cumsum(shuf)
        peak = -np.inf
        max_dd = 0.0
        for v in equity:
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
        dd_list.append(max_dd)
    q = np.quantile(dd_list, [0.5, 0.95, 0.99])
    return {"p50": float(q[0]), "p95": float(q[1]), "p99": float(q[2])}


# -----------------------------
# Plotting
# -----------------------------

def save_equity_curve(r_values: List[float], out_png: str) -> None:
    arr = np.array(r_values, dtype=float)
    equity = np.cumsum(arr) if arr.size > 0 else np.array([])
    plt.figure(figsize=(10, 4))
    plt.plot(equity, label="Equity (R)")
    plt.xlabel("Trade #")
    plt.ylabel("Cumulative R")
    plt.title("Equity Curve")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png)
    plt.close()


def save_r_distribution(r_values: List[float], out_png: str) -> None:
    arr = np.array(r_values, dtype=float)
    plt.figure(figsize=(8, 4))
    plt.hist(arr, bins=40, alpha=0.8, edgecolor="black")
    plt.xlabel("R")
    plt.ylabel("Frequency")
    plt.title("R Distribution")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_png)
    plt.close()


# -----------------------------
# Utility
# -----------------------------

def params_to_dict(p: ExitParams) -> Dict[str, Any]:
    return {
        "scaleout_r": p.scaleout_r,
        "scaleout_frac": p.scaleout_frac,
        "be_trigger_r": p.be_trigger_r,
        "be_back_r": p.be_back_r,
        "tp_r": p.tp_r,
        "trailing_start_r": p.trailing_start_r,
        "trailing_gap_r": p.trailing_gap_r,
    }


def dict_to_params(d: Dict[str, Any]) -> ExitParams:
    return ExitParams(
        scaleout_r=d.get("scaleout_r"),
        scaleout_frac=float(d.get("scaleout_frac", 0.0) or 0.0),
        be_trigger_r=d.get("be_trigger_r"),
        be_back_r=float(d.get("be_back_r", 0.0) or 0.0),
        tp_r=d.get("tp_r"),
        trailing_start_r=d.get("trailing_start_r"),
        trailing_gap_r=float(d.get("trailing_gap_r", 0.7) or 0.7),
    )


def grid_space_iter(space: Dict[str, List[Any]]):
    keys = [
        "scaleout_r",
        "scaleout_frac",
        "be_trigger_r",
        "be_back_r",
        "tp_r",
        "trailing_start_r",
        "trailing_gap_r",
    ]
    values = [space.get(k, [None]) for k in keys]
    for combo in itertools.product(*values):
        d = dict(zip(keys, combo))
        # constraints
        if d.get("tp_r") is None and d.get("trailing_start_r") is None:
            continue
        yield d


