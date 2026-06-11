"""Write the running PnL report (markdown + json) into reports/.

Each strategy is its own isolated FundingPips Phase 1 → Phase 2 challenge.
"""
from __future__ import annotations
import os
import json
import datetime as dt

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(ROOT, "reports")

_BADGE = {"active": "ACTIVE", "passed": "PASSED", "failed": "FAILED"}


def write_report(books, positions, rules, now: dt.datetime):
    os.makedirs(REPORTS, exist_ok=True)
    closed = [p for p in positions if p.status == "closed"]
    open_ = [p for p in positions if p.status == "open"]

    book_rows = []
    for name, b in books.items():
        net = b.balance - b.account_size
        book_rows.append({
            "strategy": name,
            "status": b.status,
            "phase": b.phase,
            "phase1_days": b.phase1_days,
            "phase2_days": b.phase2_days,
            "failed_reason": b.failed_reason,
            "balance": round(b.balance, 2),
            "equity": round(b.equity, 2),
            "net_pnl": round(net, 2),
            "net_pnl_pct": round(net / b.account_size * 100, 2),
            "trades": b.trades_total,
            "wins": b.wins,
            "losses": b.losses,
            "win_rate": round(100 * b.wins / b.trades_total, 1) if b.trades_total else 0.0,
        })

    summary = {
        "updated": now.isoformat(),
        "model": "multi-phase-challenge",
        "account_size": rules.account_size,
        "phase1_target_pct": rules.profit_target_pct,
        "phase2_target_pct": rules.phase2_target_pct,
        "passed": sum(1 for r in book_rows if r["status"] == "passed"),
        "failed": sum(1 for r in book_rows if r["status"] == "failed"),
        "active": sum(1 for r in book_rows if r["status"] == "active"),
        "in_phase1": sum(1 for r in book_rows if r["phase"] == 1),
        "in_phase2": sum(1 for r in book_rows if r["phase"] == 2),
        "open_positions": len(open_),
        "books": book_rows,
    }
    with open(os.path.join(REPORTS, "report.json"), "w") as f:
        json.dump(summary, f, indent=2)

    lines = [
        "# Azalyst FundingPips — Phase 1/2 Challenges",
        f"_updated {now:%Y-%m-%d %H:%M UTC}_",
        "",
        f"**7 strategies** running independent **${rules.account_size:,.0f}** challenges. "
        f"Phase 1: +{rules.profit_target_pct:g}% | Phase 2: +{rules.phase2_target_pct:g}%. "
        f"Passed {summary['passed']} / Failed {summary['failed']} / Active {summary['active']} "
        f"(P1: {summary['in_phase1']} / P2: {summary['in_phase2']}).",
        "",
        "| Strategy | Status | Phase | Balance | Net PnL | P1 Days | P2 Days | Trades | Win% |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    order = {"passed": 0, "active": 1, "failed": 2}
    for r in sorted(book_rows, key=lambda x: (order.get(x["status"], 3), -x["net_pnl"])):
        badge = _BADGE.get(r["status"], r["status"].upper())
        if r["status"] == "failed" and r["failed_reason"]:
            badge += f" ({r['failed_reason']})"
        p1d = f"{r['phase1_days']}d" if r["phase1_days"] else "—"
        p2d = f"{r['phase2_days']}d" if r["phase2_days"] else ("—" if r["phase"] == 1 else "0d")
        lines.append(f"| {r['strategy']} | {badge} | P{r['phase']} | ${r['balance']:,.0f} | "
                     f"${r['net_pnl']:,.2f} ({r['net_pnl_pct']:+g}%) | {p1d} | {p2d} | {r['trades']} | {r['win_rate']}% |")

    if open_:
        lines += ["", "## Open positions",
                  "| Strategy | Symbol | Side | Entry | Stop | Target | Lots | Risk |",
                  "|---|---|---|---|---|---|---|---|"]
        for p in open_:
            lines.append(f"| {p.strategy} | {p.symbol} | {p.side} | {p.entry:g} | "
                         f"{p.stop:g} | {p.target:g} | {p.lots:g} | ${p.risk_usd:,.0f} |")

    if closed:
        lines += ["", "## Recent closed trades (last 15)",
                  "| Closed | Strategy | Symbol | Side | Exit | PnL | R |",
                  "|---|---|---|---|---|---|---|"]
        for p in closed[-15:]:
            ca = (p.closed_at or "")[:16].replace("T", " ")
            lines.append(f"| {ca} | {p.strategy} | {p.symbol} | {p.side} | "
                         f"{p.exit_reason} | ${p.pnl_usd:,.2f} | {p.r_multiple:+g}R |")

    with open(os.path.join(REPORTS, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
