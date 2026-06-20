"""
Fleet runner — several aggression levels live in parallel on gold.

Each book is its own $100k 2-step account at a different per-trade risk, all
trading the SAME gold signals, each auto-resetting on bust. Reuses the
single-account challenge.tick per book (no duplicated rule logic), so a book is
just the base config with its risk band swapped in.
"""
from __future__ import annotations

import copy
import pandas as pd

from .challenge import tick, fresh_state, attempt_stats
from .risk import RiskConfig


def book_cfg(cfg: dict, book: dict) -> dict:
    """Base config with this book's aggressive risk band injected."""
    bc = copy.deepcopy(cfg)
    bc.pop("fleet", None)
    bc["mode"] = "aggressive"
    agg = bc.setdefault("aggressive", {})
    agg["phase1_risk_pct"] = book["phase1_risk_pct"]
    agg["phase2_risk_pct"] = book["phase2_risk_pct"]
    agg["scale_in"] = book.get("scale_in", True)
    agg.setdefault("add_at_r", 1.0)
    agg.setdefault("time_stop_bars", 96)
    agg.setdefault("daily_loss_stop_pct", 4.5)
    agg.setdefault("total_dd_brake_pct", 9.5)
    agg.setdefault("max_trades_per_day", 3)
    agg.setdefault("lockin_band_pct", 0.0)
    agg["auto_reset_on_bust"] = True
    agg["perpetual"] = True
    return bc


def tick_fleet(state: dict, signals: pd.DataFrame, cfg: dict):
    """Advance every book. Returns (state, {book_name: [events]})."""
    state.setdefault("books", {})
    all_events = {}
    for book in cfg["fleet"]:
        name = book["name"]
        bcfg = book_cfg(cfg, book)
        bstate = state["books"].get(name)
        if bstate is None:
            bstate = fresh_state(bcfg)
            bstate["book"] = name
        bstate, evs = tick(bstate, signals, bcfg)
        bstate["book"] = name
        state["books"][name] = bstate
        all_events[name] = evs
    state["updated_iso"] = pd.Timestamp.now(tz="UTC").isoformat()
    return state, all_events


def _book_view(name: str, bstate: dict, bcfg: dict) -> dict:
    rc = RiskConfig.from_yaml(bcfg)
    B0 = bstate["initial_balance"]
    phase = bstate["phase"]
    if phase == "phase2":
        target_pct = rc.p2_target * 100
    elif phase in ("funded", "failed"):
        target_pct = 0.0
    else:
        target_pct = rc.p1_target * 100
    target_bal = B0 * (1 + target_pct / 100) if target_pct else B0
    progress = max(0.0, min(100.0, (bstate["balance"] - B0) / (target_bal - B0) * 100)) if target_pct else 0.0
    ast = attempt_stats(bstate)
    pos = bstate.get("position")
    # days the current attempt has been running
    days = None
    start = bstate.get("attempt_start_iso") or bstate.get("phase_start_iso")
    last = bstate.get("last_bar_iso")
    if start and last:
        try:
            days = round((pd.Timestamp(last) - pd.Timestamp(start)).total_seconds() / 86400, 1)
        except Exception:
            days = None
    return {
        "name": name,
        "risk_pct": round(rc.phase1_risk * 100, 2),
        "phase": phase,
        "phase_label": {"phase1": "Phase 1", "phase2": "Phase 2", "funded": "Funded",
                        "failed": "Failed"}.get(phase, phase),
        "balance": round(bstate["balance"], 0),
        "equity": round(bstate["equity"], 0),
        "pnl_pct": round((bstate["balance"] / B0 - 1) * 100, 2),
        "progress_pct": round(progress, 0),
        "target_pct": target_pct,
        "days_running": days,
        "attempts": ast,
        "position": ({"side": "BUY" if pos["dir"] > 0 else "SELL",
                      "lots": round(pos["lots"], 3)} if pos else None),
        "attempt_log": bstate.get("attempts", [])[:8],
        "stats": bstate.get("stats", {}),
        "events": bstate.get("events", [])[:4],
        "curve": bstate.get("equity_curve", [])[-90:],
    }


def build_fleet_status(state: dict, cfg: dict, gold_price=None) -> dict:
    books = []
    for book in cfg["fleet"]:
        name = book["name"]
        bstate = state.get("books", {}).get(name)
        if not bstate:
            continue
        books.append(_book_view(name, bstate, book_cfg(cfg, book)))
    return {
        "brand": cfg["brand"]["name"],
        "tagline": cfg["brand"]["tagline"],
        "mode": "fleet",
        "instrument": cfg["instrument"]["symbol"],
        "gold_price": round(gold_price, 2) if gold_price else None,
        "updated_iso": state.get("updated_iso"),
        "updated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "books": books,
    }
