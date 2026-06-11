"""SMT Divergence - BTC/ETH correlation break.

Bearish SMT: one asset makes a Higher High while the other makes a Lower High ->
SHORT the asset that FAILED (lower high). Bullish SMT: one makes a Lower Low while
the other makes a Higher Low -> LONG the asset that failed (higher low). Per the doc
entry is a few candles after the signal; on a 15m cron the confirmed pivot is already
>=2 bars old, so we enter at the current close. SL beyond the signal pivot, TP 1:2.
"""
from __future__ import annotations
from core.models import Signal
from core import indicators as ind

NAME = "smt_divergence"
RR = 2.0
FRESH_BARS = 12          # the diverging pivot must be recent
STRENGTH = 2


def generate(scfg, instruments, fetch, now):
    pair = scfg.get("pair", ["BTCUSD", "ETHUSD"])
    interval = scfg.get("interval", "5m")
    if len(pair) != 2:
        return []
    a_sym, b_sym = pair
    da = fetch(instruments[a_sym], interval, bars=200)
    db = fetch(instruments[b_sym], interval, bars=200)
    if da is None or db is None or len(da) < 40 or len(db) < 40:
        return []

    out = []
    out += _check_highs(a_sym, b_sym, da, db, interval)
    out += _check_lows(a_sym, b_sym, da, db, interval)
    return out


def _two_swings(df, kind):
    highs, lows = ind.last_swings(df, strength=STRENGTH, n=3)
    pts = highs if kind == "high" else lows
    return pts[-2:] if len(pts) >= 2 else []


def _fresh(df, ts):
    pos = df.index.get_indexer([ts])[0]
    return (len(df) - 1 - pos) <= FRESH_BARS and (len(df) - 1 - pos) >= 2


def _check_highs(a_sym, b_sym, da, db, interval):
    sa, sb = _two_swings(da, "high"), _two_swings(db, "high")
    if len(sa) < 2 or len(sb) < 2:
        return []
    a_hh = sa[-1][1] > sa[-2][1]
    b_hh = sb[-1][1] > sb[-2][1]
    # one HH, the other LH -> short the weak (LH) one
    if a_hh and not b_hh and _fresh(db, sb[-1][0]):
        return [_short(b_sym, db, sb[-1], interval, f"{a_sym} HH / {b_sym} LH")]
    if b_hh and not a_hh and _fresh(da, sa[-1][0]):
        return [_short(a_sym, da, sa[-1], interval, f"{b_sym} HH / {a_sym} LH")]
    return []


def _check_lows(a_sym, b_sym, da, db, interval):
    sa, sb = _two_swings(da, "low"), _two_swings(db, "low")
    if len(sa) < 2 or len(sb) < 2:
        return []
    a_ll = sa[-1][1] < sa[-2][1]
    b_ll = sb[-1][1] < sb[-2][1]
    if a_ll and not b_ll and _fresh(db, sb[-1][0]):
        return [_long(b_sym, db, sb[-1], interval, f"{a_sym} LL / {b_sym} HL")]
    if b_ll and not a_ll and _fresh(da, sa[-1][0]):
        return [_long(a_sym, da, sa[-1], interval, f"{b_sym} LL / {a_sym} HL")]
    return []


def _short(sym, df, pivot, interval, note):
    if pivot is None:
        return None
    entry = float(df["close"].iloc[-1])
    sig_high = float(pivot[1])
    stop = sig_high * 1.0008 if sym.startswith(("BTC", "ETH", "XRP")) else sig_high
    if stop <= entry:
        stop = entry + (entry - df["low"].iloc[-3:].min())
    risk = stop - entry
    if risk <= 0:
        return None
    return Signal(NAME, sym, "SELL", entry, stop, entry - RR * risk, interval,
                  pivot[0].isoformat(), note=f"SMT {note}")


def _long(sym, df, pivot, interval, note):
    if pivot is None:
        return None
    entry = float(df["close"].iloc[-1])
    sig_low = float(pivot[1])
    stop = sig_low * 0.9992 if sym.startswith(("BTC", "ETH", "XRP")) else sig_low
    if stop >= entry:
        stop = entry - (df["high"].iloc[-3:].max() - entry)
    risk = entry - stop
    if risk <= 0:
        return None
    return Signal(NAME, sym, "BUY", entry, stop, entry + RR * risk, interval,
                  pivot[0].isoformat(), note=f"SMT {note}")
