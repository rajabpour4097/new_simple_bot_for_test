import os
import json
from typing import Optional, Tuple, Dict, Any


class LiveExitController:
    def __init__(self, project_root: str, best_config_path: str = "best_config.txt"):
        self.project_root = project_root
        self.best_config_path = os.path.join(project_root, best_config_path)
        self.params = self._load_params()

    def _load_params(self) -> Dict[str, Any]:
        if not os.path.exists(self.best_config_path):
            return {}
        # best_config.txt contains JSON blocks; try to find the first JSON of parameters
        try:
            with open(self.best_config_path, "r", encoding="utf-8") as f:
                txt = f.read()
            # naive parse: find first '{' ... '}' block that parses
            start = txt.find("{")
            while start != -1:
                # grow end forward to next '}' progressively
                end = txt.find("}\n", start)
                if end == -1:
                    end = txt.rfind("}")
                if end != -1:
                    snippet = txt[start:end + 1]
                    try:
                        data = json.loads(snippet)
                        # ensure expected keys
                        return {
                            "scaleout_r": data.get("scaleout_r"),
                            "scaleout_frac": float(data.get("scaleout_frac", 0.0) or 0.0),
                            "be_trigger_r": data.get("be_trigger_r"),
                            "be_back_r": float(data.get("be_back_r", 0.0) or 0.0),
                            "tp_r": data.get("tp_r"),
                            "trailing_start_r": data.get("trailing_start_r"),
                            "trailing_gap_r": float(data.get("trailing_gap_r", 0.7) or 0.7),
                        }
                    except Exception:
                        pass
                start = txt.find("{", start + 1)
        except Exception:
            pass
        return {}

    def has_params(self) -> bool:
        return bool(self.params)

    def compute_updates(
        self,
        entry: float,
        risk: float,
        direction: str,
        current_price: float,
        current_sl: Optional[float],
        current_tp: Optional[float],
        state: Dict[str, Any],
    ) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
        """
        Returns (new_sl, new_tp, new_state). Only returns values that IMPROVE risk.
        direction: 'buy' or 'sell'
        state: per-position optimizer-specific state (e.g., trailing_active)
        """
        if risk is None or risk <= 0 or not self.params:
            return None, None, state

        p = self.params
        is_buy = direction == 'buy'
        new_sl: Optional[float] = None
        new_tp: Optional[float] = None

        # initialize state
        tr_active = state.get('opt_tr_active', False)
        tr_gap_r = float(p.get('trailing_gap_r', 0.7) or 0.7)

        # 1) optional TP placement
        if p.get('tp_r') is not None:
            tp_price = entry + p['tp_r'] * risk if is_buy else entry - p['tp_r'] * risk
            new_tp = tp_price

        # 2) Break-Even logic
        be_trigger = p.get('be_trigger_r')
        be_back = float(p.get('be_back_r', 0.0) or 0.0)
        if be_trigger is not None and be_trigger >= 0:
            # compute current R
            price_profit = (current_price - entry) if is_buy else (entry - current_price)
            profit_R = price_profit / risk if risk else 0.0
            if profit_R >= be_trigger:
                be_price = entry - be_back * risk if is_buy else entry + be_back * risk
                if current_sl is None:
                    new_sl = be_price
                else:
                    if is_buy and be_price > current_sl:
                        new_sl = be_price
                    if (not is_buy) and be_price < current_sl:
                        new_sl = be_price

        # 3) Trailing start/advance
        tr_start = p.get('trailing_start_r')
        if tr_start is not None:
            tr_start_price = entry + tr_start * risk if is_buy else entry - tr_start * risk
            # activate
            if (is_buy and current_price >= tr_start_price) or ((not is_buy) and current_price <= tr_start_price):
                state['opt_tr_active'] = True
                tr_active = True

            if tr_active:
                # trail stop at gap from current price (price-anchored trailing)
                gap = tr_gap_r * risk
                trail_sl = (current_price - gap) if is_buy else (current_price + gap)
                if current_sl is None:
                    new_sl = max(new_sl, trail_sl) if new_sl is not None else trail_sl
                else:
                    if is_buy and trail_sl > current_sl:
                        new_sl = max(new_sl, trail_sl) if new_sl is not None else trail_sl
                    if (not is_buy) and trail_sl < current_sl:
                        new_sl = min(new_sl, trail_sl) if new_sl is not None else trail_sl

        return new_sl, new_tp, state



