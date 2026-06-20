#!/usr/bin/env python3
"""
Azalyst Montecarlo — one tick of the live engine.

Run by GitHub Actions on a schedule (and locally for testing):

    python run.py                 # fetch gold -> advance challenge -> dashboard -> alerts
    python run.py --test-discord  # send a webhook connectivity test only
    python run.py --reset         # reset the challenge to a fresh Phase 1 account

Flow: load config + state -> fetch closed gold H1 bars -> compute signals ->
challenge.tick() -> persist state -> write dashboard feed -> Discord on events.
Idempotent: only newly-closed bars are processed, so extra ticks are harmless.
"""
from __future__ import annotations

import sys
import os
import json
import argparse

import yaml

import json as _json
from engine import data as D
from engine import indicators as I
from engine import challenge as CH
from engine import dashboard as DB
from engine import fleet as FL
from engine.notify import Notifier

STATE_PATH = "state/challenge.json"
CONFIG_PATH = "config.yaml"


def load_cfg():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state(cfg):
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[warn] state unreadable ({e}); starting fresh")
    return CH.fresh_state(cfg)


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-discord", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    notifier = Notifier(cfg)

    if args.test_discord:
        notifier.test()
        return

    is_fleet = bool(cfg.get("fleet"))

    if args.reset:
        state = {"books": {}} if is_fleet else CH.fresh_state(cfg)
        save_state(state)
        _write_dashboard(state, cfg, is_fleet)
        print("Reset:", "fresh fleet." if is_fleet else "fresh Phase 1 account.")
        return

    state = load_state(cfg) if not is_fleet else _load_fleet_state()

    inst, strat = cfg["instrument"], cfg["strategy"]
    try:
        df = D.fetch_gold(inst["ticker"], inst["interval"], bars=400)
    except Exception as e:
        print(f"[warn] gold fetch failed ({e}); skipping tick")
        _write_dashboard(state, cfg, is_fleet)
        return
    if df is None or len(df) < 60:
        print(f"[warn] insufficient gold data ({0 if df is None else len(df)} bars); skipping tick")
        _write_dashboard(state, cfg, is_fleet)
        return
    signals = I.compute_signals(df, strat["rule"], strat["params"],
                                atr_period=strat["atr_period"])
    px = float(df["close"].iloc[-1])

    if is_fleet:
        state, all_events = FL.tick_fleet(state, signals, cfg)
        save_state(state)
        DB.write_json(FL.build_fleet_status(state, cfg, gold_price=px))
        n_ev = 0
        for name, evs in all_events.items():
            for ev in evs:
                notifier.event(ev, {"phase": name, "balance": 0, "equity": 0, "day_key": "-"})
                n_ev += 1
        print(f"fleet tick ok | gold={px:.2f} | books:")
        for b in FL.build_fleet_status(state, cfg)["books"]:
            a = b["attempts"]
            print(f"  {b['name']:11s} {b['risk_pct']:.2f}% | {b['phase_label']:8s} "
                  f"${b['balance']:,.0f} ({b['pnl_pct']:+.1f}%) | attempt #{a['current_attempt']} "
                  f"passed {a['passed']}/{a['attempts']} | pos {'yes' if b['position'] else 'flat'}")
        return

    state, events = CH.tick(state, signals, cfg)
    save_state(state)
    DB.write_status(state, cfg)
    for ev in events:
        notifier.event(ev, state)
    print(f"tick ok | phase={state['phase']} bal=${state['balance']:,.0f} "
          f"eq=${state['equity']:,.0f} gold={px:.2f} "
          f"pos={'yes' if state['position'] else 'flat'} events={len(events)}")
    for ev in events:
        print(f"  - [{ev.kind}] {ev.title} :: {ev.detail}")


def _load_fleet_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                s = _json.load(f)
            s.setdefault("books", {})
            return s
        except Exception as e:
            print(f"[warn] fleet state unreadable ({e}); starting fresh")
    return {"books": {}}


def _write_dashboard(state, cfg, is_fleet):
    if is_fleet:
        DB.write_json(FL.build_fleet_status(state, cfg))
    else:
        DB.write_status(state, cfg)


if __name__ == "__main__":
    main()
