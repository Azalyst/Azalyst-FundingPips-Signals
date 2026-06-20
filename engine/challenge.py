"""
Live FundingPips 2-step paper-trade tracker (single account).

Persists its whole state as JSON between GitHub-Actions ticks and advances it by
processing any newly-CLOSED gold H1 bars since the last tick. Management is
bar-by-bar so live behaviour matches the validated backtest exactly:

  entry  : signal on bar t-1  ->  enter at bar t OPEN (managed from t+1)
  stop   : initial 2*ATR; break-even at +1R; trailing 3*ATR; 48-bar time stop
  rules  : target on REALIZED balance (+ >=3 trading days); bust on EQUITY
           (floating) crossing the 5% daily line or the 10% static floor
  phases : pass P1 (+8%) -> fresh $100k Phase 2 (+5%) -> funded (no target)

The function is pure-ish: tick(state, signals, cfg, risk) mutates and returns
(state, events). Events drive Discord + the dashboard log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import pandas as pd

from .risk import RiskConfig, RiskEngine

MAX_TRADES_KEPT = 200
MAX_CURVE_POINTS = 1500


@dataclass
class Event:
    kind: str            # opened|closed|phase_pass|bust|brake|info
    title: str
    detail: str
    data: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
def fresh_state(cfg: dict) -> dict:
    a = cfg["account"]
    B0 = a["size"]
    return {
        "brand": cfg["brand"]["name"],
        "tagline": cfg["brand"]["tagline"],
        "challenge_start": a["challenge_start"],
        "initial_balance": B0,
        "phase": "phase1",
        "phase_num": 1,
        "phase_start_iso": None,
        "balance": B0,
        "equity": B0,
        "peak_balance": B0,
        "last_bar_iso": None,
        "day_key": None,
        "day_anchor": B0,
        "trades_today": 0,
        "phase_trading_days": [],
        "position": None,
        "trades": [],
        "equity_curve": [],
        "stats": {"n_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                  "gross_win": 0.0, "gross_loss": 0.0, "best_r": 0.0, "worst_r": 0.0},
        "phase_history": [],
        "events": [],
        "updated_iso": None,
    }


def _day_key(ts: pd.Timestamp, reset_hour: int) -> str:
    """FundingPips trading day rolls at reset_hour UTC; bars before it belong to
    the previous calendar day."""
    shifted = ts - pd.Timedelta(hours=reset_hour)
    return shifted.strftime("%Y-%m-%d")


def _phase_target(state, rc: RiskConfig) -> float:
    if state["phase"] == "phase2":
        return state["initial_balance"] * (1 + rc.p2_target)
    if state["phase"] == "funded":
        return float("inf")
    return state["initial_balance"] * (1 + rc.p1_target)


def _mark_equity(state, price: float, lot_cost: float) -> float:
    pos = state["position"]
    bal = state["balance"]
    if pos is None:
        return bal
    floating = (price - pos["entry"]) * pos["dir"] * pos["size"]
    return bal + floating


# --------------------------------------------------------------------------
def tick(state: dict, signals: pd.DataFrame, cfg: dict) -> tuple[dict, list[Event]]:
    """Advance the challenge by any new closed bars. `signals` is the indicator
    frame with boolean long/short columns, indexed by tz-aware UTC time."""
    rc = RiskConfig.from_yaml(cfg)
    inst = cfg["instrument"]
    strat = cfg["strategy"]
    risk = RiskEngine(rc, lot_units=inst["lot_units"])
    reset_hour = int(cfg["account"]["daily_reset_utc_hour"])
    cost_per_unit = float(inst.get("cost_per_unit", 0.30))
    min_days = int(cfg["account"]["min_trading_days"])
    events: list[Event] = []

    if state["phase"] in ("failed",):
        state["updated_iso"] = pd.Timestamp.now(tz="UTC").isoformat()
        return state, events  # terminal until manual reset

    # which bars are new?
    last_iso = state["last_bar_iso"]
    idx = signals.index
    if last_iso is not None:
        new = signals[idx > pd.Timestamp(last_iso)]
    else:
        # cold start: seed from the most recent bar only (don't replay history)
        new = signals.iloc[-1:]
        if state["phase_start_iso"] is None:
            state["phase_start_iso"] = str(idx[-1])

    # map positions for "previous bar signal" lookups
    long_arr = signals["long"].to_numpy(bool)
    short_arr = signals["short"].to_numpy(bool)
    pos_of = {ts: i for i, ts in enumerate(idx)}

    for ts, row in new.iterrows():
        i = pos_of[ts]
        atr = float(row["atr"]); o = float(row["open"])
        hi = float(row["high"]); lo = float(row["low"]); cl = float(row["close"])

        # advance the pointer for EVERY processed bar, before any continue/break,
        # so a phase-advance or bust never leaves a bar to be reprocessed next tick.
        state["last_bar_iso"] = str(ts)

        # ---- day roll ----
        dk = _day_key(ts, reset_hour)
        if dk != state["day_key"]:
            # anchor the 5% daily line to equity carried in AT the reset instant
            # (the prior bar's close mark), not this new bar's close -- otherwise an
            # overnight gap would measure the daily line from the wrong reference.
            eq_at_reset = state["equity"]
            state["day_key"] = dk
            state["day_anchor"] = max(state["balance"], eq_at_reset)
            state["trades_today"] = 0

        # ---- manage open position against this bar ----
        pos = state["position"]
        if pos is not None:
            pos["bars"] += 1
            d = pos["dir"]
            fav = (cl - pos["entry"]) * d
            if (not pos["be"]) and fav >= pos["init_risk"] * strat["breakeven_at_r"]:
                pos["stop"] = pos["entry"]; pos["be"] = True
            new_trail = cl - d * strat["trail_mult"] * atr
            pos["stop"] = max(pos["stop"], new_trail) if d > 0 else min(pos["stop"], new_trail)

            exit_price = None; reason = None
            if d > 0 and lo <= pos["stop"]:
                exit_price = min(pos["stop"], o) if o < pos["stop"] else pos["stop"]; reason = "stop"
            elif d < 0 and hi >= pos["stop"]:
                exit_price = max(pos["stop"], o) if o > pos["stop"] else pos["stop"]; reason = "stop"
            elif pos["bars"] >= strat["time_stop_bars"]:
                exit_price = cl; reason = "time"

            if exit_price is not None:
                gross = (exit_price - pos["entry"]) * d * pos["size"]
                cost = cost_per_unit * pos["size"]
                pnl = gross - cost
                state["balance"] += pnl
                r_mult = pnl / pos["risk_usd"] if pos["risk_usd"] else 0.0
                tr = {"opened": pos["opened_iso"], "closed": str(ts),
                      "side": "BUY" if d > 0 else "SELL", "entry": round(pos["entry"], 2),
                      "exit": round(exit_price, 2), "lots": round(pos["lots"], 4),
                      "pnl": round(pnl, 2), "r": round(r_mult, 2), "reason": reason}
                state["trades"].append(tr)
                state["trades"] = state["trades"][-MAX_TRADES_KEPT:]
                _update_stats(state, pnl, r_mult)
                state["position"] = None
                events.append(Event("closed", f"Closed {tr['side']} @ {tr['exit']}",
                                    f"{pnl:+,.0f} USD ({r_mult:+.2f}R, {reason})", tr))

        # ---- mark equity + bust checks (on equity) ----
        eq = _mark_equity(state, cl, cost_per_unit)
        state["equity"] = eq
        # record one equity-curve point per processed bar (denser, back-filling chart)
        state["equity_curve"].append([str(ts), round((eq / state["initial_balance"] - 1) * 100, 3)])
        static_floor = risk.static_floor()
        daily_floor = risk.daily_floor(state["day_anchor"])
        if eq <= static_floor + 1e-9 or eq <= daily_floor + 1e-9:
            kind = "max_dd" if eq <= static_floor + 1e-9 else "daily_dd"
            state["position"] = None
            state["phase_history"].append({"phase": state["phase"], "result": f"FAILED ({kind})",
                                           "balance": round(state["balance"], 2), "date": str(ts)})
            state["phase"] = "failed"
            events.append(Event("bust", f"CHALLENGE FAILED — {kind}",
                                f"Equity ${eq:,.0f} breached the "
                                f"{'10% static' if kind=='max_dd' else '5% daily'} line."))
            break

        # ---- target check (on realized balance) ----
        state["peak_balance"] = max(state["peak_balance"], state["balance"])
        target = _phase_target(state, rc)
        if state["balance"] >= target - 1e-9 and len(state["phase_trading_days"]) >= min_days:
            _advance_phase(state, rc, ts, events)
            if state["phase"] == "passed_all":
                break
            continue  # fresh phase; don't open on the same bar

        # ---- new entry: signal on prior bar -> enter at this bar's open ----
        if state["position"] is None and i > 0 and (long_arr[i - 1] or short_arr[i - 1]):
            d = 1 if long_arr[i - 1] else -1
            stop_dist = max(strat["atr_stop_mult"] * atr, inst.get("min_stop", 0.0))
            dec = risk.size(state["phase"], state["balance"], eq, state["day_anchor"],
                            state["trades_today"], stop_dist)
            if dec.allowed:
                state["position"] = {
                    "dir": d, "entry": o, "stop": o - d * stop_dist, "init_risk": stop_dist,
                    "size": dec.size_units, "lots": dec.lots, "risk_usd": dec.risk_amount,
                    "be": False, "bars": 0, "opened_iso": str(ts),
                }
                state["trades_today"] += 1
                if dk not in state["phase_trading_days"]:
                    state["phase_trading_days"].append(dk)
                target_px = o + d * strat.get("rr_target_display", 3.0) * stop_dist
                events.append(Event("opened",
                    f"{'BUY' if d>0 else 'SELL'} {inst['symbol']} @ {o:.2f}",
                    f"stop {o - d*stop_dist:.2f} | risk ${dec.risk_amount:,.0f} "
                    f"({dec.risk_fraction*100:.2f}%) | {dec.lots:.3f} lots",
                    {"side": "BUY" if d > 0 else "SELL", "entry": round(o, 2),
                     "stop": round(o - d * stop_dist, 2), "target": round(target_px, 2),
                     "lots": round(dec.lots, 4), "risk_usd": round(dec.risk_amount, 2),
                     "risk_pct": round(dec.risk_fraction * 100, 3)}))

    # always refresh the equity mark + a curve point from the latest bar
    if len(idx):
        last_close = float(signals["close"].iloc[-1])
        state["equity"] = _mark_equity(state, last_close, cost_per_unit)
        pct = (state["equity"] / state["initial_balance"] - 1) * 100
        state["equity_curve"].append([str(idx[-1]), round(pct, 3)])
        # de-dup consecutive identical timestamps, cap length
        seen = {}
        for t, p in state["equity_curve"]:
            seen[t] = p
        state["equity_curve"] = [[t, p] for t, p in seen.items()][-MAX_CURVE_POINTS:]

    state["updated_iso"] = pd.Timestamp.now(tz="UTC").isoformat()
    state["events"] = ([{"kind": e.kind, "title": e.title, "detail": e.detail,
                         "ts": state["updated_iso"]} for e in events] + state["events"])[:30]
    return state, events


def _update_stats(state, pnl, r):
    s = state["stats"]
    s["n_trades"] += 1
    if pnl >= 0:
        s["wins"] += 1; s["gross_win"] += pnl
    else:
        s["losses"] += 1; s["gross_loss"] += -pnl
    s["win_rate"] = round(s["wins"] / s["n_trades"] * 100, 1) if s["n_trades"] else 0.0
    s["best_r"] = round(max(s["best_r"], r), 2)
    s["worst_r"] = round(min(s["worst_r"], r), 2)


def _advance_phase(state, rc: RiskConfig, ts, events):
    cur = state["phase"]
    B0 = state["initial_balance"]
    state["phase_history"].append({"phase": cur, "result": "PASSED",
                                   "balance": round(state["balance"], 2), "date": str(ts)})
    if cur == "phase1":
        events.append(Event("phase_pass", "PHASE 1 PASSED ✅ (+8%)",
                            f"Banked ${state['balance']-B0:,.0f}. Fresh $100k Phase 2 (+5%) begins."))
        state.update(phase="phase2", phase_num=2, balance=B0, equity=B0, peak_balance=B0,
                     day_anchor=B0, trades_today=0,
                     phase_trading_days=[], position=None, phase_start_iso=str(ts))
    elif cur == "phase2":
        events.append(Event("phase_pass", "PHASE 2 PASSED 🏆 — FUNDED",
                            f"Banked ${state['balance']-B0:,.0f}. Account is now FUNDED (80% split)."))
        state.update(phase="funded", phase_num=3, balance=B0, equity=B0, peak_balance=B0,
                     day_anchor=B0, trades_today=0,
                     phase_trading_days=[], position=None, phase_start_iso=str(ts))
