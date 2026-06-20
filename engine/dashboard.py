"""Write the dashboard feed (docs/status.json) the GitHub-Pages page reads.

Pure projection of the persisted challenge state into a compact, render-friendly
shape. The HTML (docs/index.html) is static and fetches this JSON.
"""
from __future__ import annotations
import json
import os
import pandas as pd

from .risk import RiskConfig, RiskEngine
from .challenge import attempt_stats


def build_status(state: dict, cfg: dict) -> dict:
    rc = RiskConfig.from_yaml(cfg)
    risk = RiskEngine(rc, lot_units=cfg["instrument"]["lot_units"])
    B0 = state["initial_balance"]
    phase = state["phase"]

    if phase == "phase2":
        target_pct = rc.p2_target * 100
    elif phase in ("funded", "passed_all"):
        target_pct = 0.0
    else:
        target_pct = rc.p1_target * 100
    target_bal = B0 * (1 + target_pct / 100) if target_pct else B0

    progress = 0.0
    if target_pct:
        progress = max(0.0, min(100.0, (state["balance"] - B0) / (target_bal - B0) * 100))

    day_anchor = state.get("day_anchor", B0)
    daily_floor = risk.daily_floor(day_anchor)
    static_floor = risk.static_floor()
    daily_room = max(0.0, state["equity"] - daily_floor)
    max_room = max(0.0, state["equity"] - static_floor)

    pos = state.get("position")
    position = None
    if pos:
        position = {
            "side": "BUY" if pos["dir"] > 0 else "SELL",
            "entry": round(pos["entry"], 2), "stop": round(pos["stop"], 2),
            "lots": round(pos["lots"], 4), "bars": pos["bars"],
            "be": pos["be"], "risk_usd": round(pos["risk_usd"], 2),
        }

    mode = state.get("mode", "safe")
    astats = attempt_stats(state)
    return {
        "brand": state.get("brand", "Azalyst Montecarlo Quant"),
        "tagline": state.get("tagline", ""),
        "updated_iso": state.get("updated_iso"),
        "updated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "instrument": cfg["instrument"]["symbol"],
        "mode": mode,
        "attempts": {**astats, "log": state.get("attempts", [])[:12]},
        "challenge": {
            "phase": phase,
            "phase_num": state.get("phase_num", 1),
            "phase_label": {"phase1": "Phase 1", "phase2": "Phase 2",
                            "funded": "Funded", "failed": "Failed",
                            "passed_all": "Passed"}.get(phase, phase),
            "target_pct": target_pct,
            "target_balance": round(target_bal, 2),
            "progress_pct": round(progress, 1),
            "trading_days": len(state.get("phase_trading_days", [])),
            "min_trading_days": cfg["account"]["min_trading_days"],
            "start": state.get("challenge_start"),
        },
        "account": {
            "size": B0,
            "balance": round(state["balance"], 2),
            "equity": round(state["equity"], 2),
            "pnl": round(state["balance"] - B0, 2),
            "pnl_pct": round((state["balance"] / B0 - 1) * 100, 2),
            "peak_pct": round((state["peak_balance"] / B0 - 1) * 100, 2),
        },
        "rules": {
            "daily_floor": round(daily_floor, 2), "daily_room": round(daily_room, 2),
            "static_floor": round(static_floor, 2), "max_room": round(max_room, 2),
            "daily_loss_stop_pct": cfg["risk"]["daily_loss_stop_pct"],
            "max_daily_loss_pct": cfg["account"]["max_daily_loss_pct"],
            "max_overall_loss_pct": cfg["account"]["max_overall_loss_pct"],
        },
        "position": position,
        "stats": state.get("stats", {}),
        "trades": list(reversed(state.get("trades", [])))[:25],
        "equity_curve": state.get("equity_curve", [])[-500:],
        "events": state.get("events", [])[:12],
        "phase_history": state.get("phase_history", []),
        "strategy": {
            "rule": cfg["strategy"]["rule"], "params": cfg["strategy"]["params"],
            "p1_risk_pct": rc.phase1_risk * 100,   # reflects the active mode
            "p2_risk_pct": rc.phase2_risk * 100,
        },
    }


def write_status(state: dict, cfg: dict, path: str = "docs/status.json"):
    return write_json(build_status(state, cfg), path)


def write_json(status: dict, path: str = "docs/status.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    return status
