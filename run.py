#!/usr/bin/env python3
"""Azalyst FundingPips Signal Engine - orchestrator.

One invocation = one cron tick:
  1. roll the FundingPips trading day (daily-loss reset)
  2. resolve open paper positions against fresh bars (SL/TP/break-even)
  3. mark equity, enforce max-loss / daily-loss status
  4. generate fresh signals from every enabled strategy
  5. risk-gate, size, open paper trades, and alert Discord
  6. persist state + write the PnL report

Usage:
  python run.py [--dry-run] [--test-discord] [--once]
"""
from __future__ import annotations
import os
import sys
import argparse
import datetime as dt
import yaml

try:                       # keep emoji/arrows printable on Windows consoles (cp1252)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from core import data
from core.risk import FundingPipsRules
from core.sessions import now_utc
from core.state import (load_books, save_books, load_positions, save_positions,
                        load_sent, save_sent)
from core.notify import Notifier
from core import papertrade as pt
import strategies
from report import write_report
from dashboard import build_status

ROOT = os.path.dirname(os.path.abspath(__file__))
SL_REASONS = {"sl"}
EMA5_DAILY_SL_CAP = 3


def load_config():
    with open(os.path.join(ROOT, "config.yaml")) as f:
        return yaml.safe_load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting to Discord")
    ap.add_argument("--test-discord", action="store_true", help="send a connectivity test and exit")
    args = ap.parse_args()

    cfg = load_config()
    rules = FundingPipsRules(cfg)
    instruments = cfg["instruments"]
    now = now_utc()

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    notifier = Notifier(webhook, cfg["discord"]["user_id"], dry_run=args.dry_run)

    if args.test_discord:
        notifier.alert("Signal engine online",
                       f"Connectivity test OK. {now:%Y-%m-%d %H:%M UTC}. "
                       f"Account ${rules.account_size:,.0f}, risk {rules.risk_pct}%/trade.",
                       color=0x3498DB)
        return

    strat_names = list(cfg["strategies"].keys())
    books = load_books(strat_names, rules.account_size)
    positions = load_positions()
    sent = load_sent()

    def fetch(inst, interval, bars=300):
        return data.fetch_ohlc(inst, interval, bars)

    # ---- 1. daily roll (FundingPips server day), per book ----
    day = rules.server_day(now)
    for b in books.values():
        if b.current_day != day:
            b.current_day = day
            b.today_start_equity = b.equity
            b.daily_realized_pnl = 0.0
            b.sl_count = {}
            if b.phase == 2:
                b.phase2_days = max(1, (now.date() - dt.date.fromisoformat(b.phase2_start)).days + 1)

    # ---- 2. resolve open positions, attributing PnL to the owning strategy's book ----
    open_positions = [p for p in positions if p.status == "open"]
    for pos in open_positions:
        b = books.get(pos.strategy)
        if b is None:
            continue
        inst = instruments[pos.symbol]
        df = fetch(inst, pos.interval, bars=300)
        use_be = pos.strategy in strategies.BREAKEVEN_STRATEGIES
        pnl = pt.update_position(pos, df, inst["value_per_point"], use_breakeven=use_be)
        if pos.status == "closed":
            b.balance += pnl
            b.daily_realized_pnl += pnl
            b.trades_total += 1
            if pnl >= 0:
                b.wins += 1
            else:
                b.losses += 1
            if pos.exit_reason in SL_REASONS:
                b.sl_count[pos.strategy] = b.sl_count.get(pos.strategy, 0) + 1
            notifier.close(pos, b)

    # ---- 3. mark equity + resolve pass/fail, per book ----
    open_positions = [p for p in positions if p.status == "open"]
    unreal_by_strat = {}
    for pos in open_positions:
        inst = instruments[pos.symbol]
        df = fetch(inst, pos.interval, bars=50)
        if df is not None and len(df):
            mtm = pt.mark_to_market(pos, float(df["close"].iloc[-1]), inst["value_per_point"])
            unreal_by_strat[pos.strategy] = unreal_by_strat.get(pos.strategy, 0.0) + mtm

    for name, b in books.items():
        b.equity = round(b.balance + unreal_by_strat.get(name, 0.0), 2)
        b.peak_balance = max(b.peak_balance, b.balance)
        if b.status != "active":
            continue
        daily_loss = max(0.0, b.today_start_equity - b.equity)
        if b.equity <= rules.overall_floor:
            b.status, b.failed_reason, b.resolved_at = "failed", "max loss", now.isoformat()
            notifier.alert(f"{name.upper()} FAILED - max loss breached",
                           f"[{name}] equity ${b.equity:,.0f} hit the ${rules.overall_floor:,.0f} "
                           f"floor. This strategy's challenge is over - it will stop trading.",
                           color=0xE74C3C)
        elif daily_loss >= rules.max_daily_loss:
            b.status, b.failed_reason, b.resolved_at = "failed", "daily loss", now.isoformat()
            notifier.alert(f"{name.upper()} FAILED - daily loss limit",
                           f"[{name}] down ${daily_loss:,.0f} today (limit "
                           f"${rules.max_daily_loss:,.0f}). Daily drawdown breach = challenge failed.",
                           color=0xE74C3C)
        elif b.phase == 1 and b.balance >= rules.pass_threshold_phase1:
            # Phase 1 passed -> transition to Phase 2
            b.phase1_passed_at = now.isoformat()
            b.phase1_days = max(1, (now.date() - rules.start).days + 1)
            b.phase2_start = now.date().isoformat()
            b.phase = 2
            # Reset account for Phase 2
            b.account_size = rules.account_size
            b.balance = rules.account_size
            b.equity = rules.account_size
            b.peak_balance = rules.account_size
            b.today_start_equity = rules.account_size
            b.phase2_days = 0
            b.trades_total = 0
            b.wins = 0
            b.losses = 0
            notifier.alert(f"{name.upper()} PHASE 1 PASSED - {b.phase1_days} days",
                           f"[{name}] Phase 1 complete in {b.phase1_days} days. "
                           f"Balance hit ${rules.pass_threshold_phase1:,.0f} (+{rules.profit_target_pct:g}%). "
                           f"Starting Phase 2 — ${rules.account_size:,.0f} account, +{rules.phase2_target_pct:g}% target.",
                           color=0x00FFFF)
            # Close all open positions for this book
            for p in positions:
                if p.strategy == name and p.status == "open":
                    p.status = "closed"
                    p.exit_price = p.entry
                    p.closed_at = now.isoformat()
                    p.pnl_usd = 0.0
                    p.r_multiple = 0.0
                    p.exit_reason = "phase"
        elif b.phase == 2 and b.balance >= rules.pass_threshold_phase2:
            # Phase 2 passed -> challenge complete!
            b.phase2_days = max(1, (now.date() - dt.date.fromisoformat(b.phase2_start)).days + 1)
            b.status, b.resolved_at = "passed", now.isoformat()
            notifier.alert(f"{name.upper()} PHASE 2 PASSED - Challenge complete!",
                           f"[{name}] Phase 1: {b.phase1_days} days | "
                           f"Phase 2: {b.phase2_days} days. "
                           f"Total challenge passed. Balance ${b.balance:,.0f}.",
                           color=0x00FFFF)

    # ---- 4 + 5. generate, gate, size, open (only for books still ACTIVE) ----
    new_signals = []
    for name, scfg in cfg["strategies"].items():
        if not scfg.get("enabled"):
            continue
        if books[name].status != "active":      # passed/failed books stop trading
            continue
        mod = strategies.REGISTRY.get(name)
        if not mod:
            continue
        try:
            new_signals += mod.generate(scfg, instruments, fetch, now)
        except Exception as e:
            print(f"[strategy {name}] error: {e}")

    # open positions scoped per strategy (each book is its own independent account)
    open_by_strat_sym = {}
    for p in open_positions:
        open_by_strat_sym.setdefault((p.strategy, p.symbol), []).append(p)

    for sig in new_signals:
        b = books.get(sig.strategy)
        if b is None or b.status != "active":
            continue
        sig.rr = sig.compute_rr()
        key = sig.dedupe_key
        if key in sent:
            continue
        # within this strategy's own book: no duplicate / no opposite-side hedge on a symbol
        existing = open_by_strat_sym.get((sig.strategy, sig.symbol), [])
        if existing:
            continue
        # 5 EMA 3-SL daily rule (per book)
        if sig.strategy == "ema5" and b.sl_count.get("ema5", 0) >= EMA5_DAILY_SL_CAP:
            continue

        inst = instruments[sig.symbol]
        units, lots = rules.size_position(sig.stop_distance, inst["value_per_point"], inst["lot_units"])
        if units <= 0:
            continue
        ok, reason = rules.validate_trade(sig, inst, units)
        if not ok:
            print(f"[skip] {sig.strategy} {sig.symbol} {sig.side}: {reason}")
            continue
        risk_usd = rules.risk_usd()

        # worst-case open risk is scoped to THIS strategy's book only
        worst_open = sum(pt.worst_case_loss(p) for p in positions
                         if p.status == "open" and p.strategy == sig.strategy)
        ok, reason = rules.gate(b, worst_open, risk_usd, now)
        if not ok:
            print(f"[gate] blocked {sig.strategy} {sig.symbol} {sig.side}: {reason}")
            continue

        pos = pt.open_position(sig, units, lots, risk_usd, now.isoformat())
        positions.append(pos)
        open_by_strat_sym.setdefault((sig.strategy, sig.symbol), []).append(pos)
        sent.add(key)
        notifier.signal(sig, pos, b)
        print(f"[open] {sig.strategy} {sig.symbol} {sig.side} @ {sig.entry:g} "
              f"SL {sig.stop:g} TP {sig.target:g} ({lots:g} lots)")

    # ---- 6. persist + report ----
    save_books(books)
    save_positions(positions)
    save_sent(sent)
    write_report(books, positions, rules, now)
    build_status(books, positions, rules, data.last_prices(), instruments, now)
    active = sum(1 for b in books.values() if b.status == "active")
    passed = sum(1 for b in books.values() if b.status == "passed")
    failed = sum(1 for b in books.values() if b.status == "failed")
    print(f"[done] books active {active} / passed {passed} / failed {failed} | "
          f"open {len([p for p in positions if p.status=='open'])}")


if __name__ == "__main__":
    main()
