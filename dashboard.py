"""Build docs/status.json - the payload the GitHub Pages dashboard reads.

7 strategies each running an isolated FundingPips Phase 1 → Phase 2 challenge.
"""
from __future__ import annotations
import os
import json
import datetime as dt

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
STATE = os.path.join(ROOT, "state")


def _equity_curve(fleet_equity: float, now: dt.datetime) -> list:
    """Fleet equity curve (sum of all book equities)."""
    path = os.path.join(STATE, "equity_curve.json")
    curve = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                curve = json.load(f)
        except Exception:
            curve = []
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    if curve and curve[-1].get("t") == stamp:
        curve[-1]["e"] = round(fleet_equity, 2)
    else:
        curve.append({"t": stamp, "e": round(fleet_equity, 2)})
    curve = curve[-500:]
    with open(path, "w") as f:
        json.dump(curve, f)
    return curve


def build_status(books, positions, rules, prices: dict, instruments: dict, now: dt.datetime):
    os.makedirs(DOCS, exist_ok=True)
    open_ = [p for p in positions if p.status == "open"]
    closed = [p for p in positions if p.status == "closed"]

    size = rules.account_size
    max_daily = rules.max_daily_loss
    max_overall = rules.max_overall_loss

    open_by_strat = {}
    for p in open_:
        open_by_strat.setdefault(p.strategy, 0)
        open_by_strat[p.strategy] += 1

    book_cards = []
    fleet_balance = fleet_equity = fleet_trades = fleet_wins = fleet_losses = 0
    for name, b in books.items():
        # Determine phase target
        if b.phase == 1:
            target_pct = rules.profit_target_pct
            target_usd = rules.pass_threshold_phase1
            phase_label = "Phase 1"
            days_elapsed = max(1, (now.date() - rules.start).days + 1)
            phase_status = "phase1"
        elif b.phase == 2 and b.status == "active":
            target_pct = rules.phase2_target_pct
            target_usd = rules.pass_threshold_phase2
            phase_label = "Phase 2"
            days_elapsed = b.phase2_days if b.phase2_days > 0 else max(1, (now.date() - dt.date.fromisoformat(b.phase2_start)).days + 1)
            phase_status = "phase2"
        else:
            target_pct = rules.phase2_target_pct
            target_usd = rules.pass_threshold_phase2
            phase_label = "Phase 2"
            days_elapsed = b.phase2_days
            phase_status = b.status

        net = b.balance - b.account_size
        daily_used = max(0.0, b.today_start_equity - b.equity)
        overall_used = max(0.0, b.account_size - b.equity)
        progress = max(0.0, min(100.0, net / (target_usd - b.account_size) * 100)) if target_usd > b.account_size else 0.0

        phase1_days = b.phase1_days if b.phase1_days > 0 else (
            max(1, (now.date() - rules.start).days + 1) if b.phase == 1 else 0
        )

        book_cards.append({
            "strategy": name,
            "status": b.status,
            "phase": b.phase,
            "phase_label": phase_label,
            "phase_status": phase_status,
            "days_elapsed": days_elapsed,
            "phase1_days": phase1_days,
            "phase2_days": b.phase2_days,
            "phase1_passed_at": (b.phase1_passed_at or "")[:16].replace("T", " "),
            "phase2_start": (b.phase2_start or "")[:10],
            "failed_reason": b.failed_reason,
            "resolved_at": (b.resolved_at or "")[:16].replace("T", " "),
            "size": b.account_size,
            "balance": round(b.balance, 2),
            "equity": round(b.equity, 2),
            "net_pnl": round(net, 2),
            "net_pnl_pct": round(net / b.account_size * 100, 2),
            "today_pnl": round(b.daily_realized_pnl, 2),
            "peak_balance": round(b.peak_balance, 2),
            "trades": b.trades_total,
            "wins": b.wins,
            "losses": b.losses,
            "win_rate": round(100 * b.wins / b.trades_total, 1) if b.trades_total else 0.0,
            "open": open_by_strat.get(name, 0),
            "daily": {"used": round(daily_used, 2), "limit": max_daily,
                       "remaining": round(max(0.0, max_daily - daily_used), 2)},
            "overall": {"used": round(overall_used, 2), "limit": max_overall,
                         "remaining": round(max(0.0, max_overall - overall_used), 2)},
            "target": {"usd": target_usd, "pct": target_pct,
                        "progress": round(progress, 1)},
        })
        fleet_balance += b.balance
        fleet_equity += b.equity
        fleet_trades += b.trades_total
        fleet_wins += b.wins
        fleet_losses += b.losses

    order = {"passed": 0, "active": 1, "failed": 2}
    book_cards.sort(key=lambda c: (order.get(c["status"], 3), -c["net_pnl"]))

    n_books = len(books)
    fleet_size = size * n_books
    day_num = (now.date() - rules.start).days + 1

    tape = []
    for sym, cfg in instruments.items():
        px = prices.get(cfg["ticker"])
        if px is not None:
            tape.append({"sym": sym, "px": round(px, cfg.get("digits", 2))})

    status = {
        "updated": now.strftime("%Y-%m-%d %H:%M UTC"),
        "updated_iso": now.isoformat(),
        "model": "multi-phase",
        "challenge": {
            "day": max(1, day_num),
            "start": rules.start.isoformat(),
            "phase1_target_pct": rules.profit_target_pct,
            "phase2_target_pct": rules.phase2_target_pct,
        },
        "rules": {"size": size, "max_daily": max_daily, "max_overall": max_overall,
                  "floor": rules.overall_floor},
        "fleet": {
            "books": n_books,
            "passed": sum(1 for c in book_cards if c["status"] == "passed"),
            "failed": sum(1 for c in book_cards if c["status"] == "failed"),
            "active": sum(1 for c in book_cards if c["status"] == "active"),
            "phase1": sum(1 for c in book_cards if c["phase_status"] == "phase1"),
            "phase2": sum(1 for c in book_cards if c["phase_status"] == "phase2"),
            "size_total": fleet_size,
            "balance_total": round(fleet_balance, 2),
            "equity_total": round(fleet_equity, 2),
            "net_pnl": round(fleet_balance - fleet_size, 2),
            "net_pnl_pct": round((fleet_balance - fleet_size) / fleet_size * 100, 2) if fleet_size else 0.0,
            "trades": fleet_trades, "wins": fleet_wins, "losses": fleet_losses,
            "win_rate": round(100 * fleet_wins / fleet_trades, 1) if fleet_trades else 0.0,
            "open": len(open_),
        },
        "books": book_cards,
        "positions": [
            {"strategy": p.strategy, "symbol": p.symbol, "side": p.side,
             "entry": p.entry, "stop": p.stop, "target": p.target,
             "lots": round(p.lots, 4), "risk": round(p.risk_usd, 0),
             "be": p.be_moved, "opened": (p.opened_at or "")[:16].replace("T", " ")}
            for p in open_
        ],
        "closed": [
            {"strategy": p.strategy, "symbol": p.symbol, "side": p.side,
             "exit": p.exit_price, "reason": p.exit_reason,
             "pnl": round(p.pnl_usd, 2), "r": p.r_multiple,
             "closed": (p.closed_at or "")[:16].replace("T", " ")}
            for p in closed[-40:][::-1]
        ],
        "tape": tape,
        "equity_curve": _equity_curve(fleet_equity, now),
    }
    with open(os.path.join(DOCS, "status.json"), "w") as f:
        json.dump(status, f, indent=2)
    return status
