"""Ethereum Blueprint - HTF bias + Break of Structure during the Asia session.

4H/1H bias (HH/HL=bull, LH/LL=bear). During the first 3h of the Asia session, a 15m
candle that CLOSES beyond the most recent swing level confirms the BOS. SL = prior
HL (long) / prior LH (short); TP = 1:2; managed to break-even at 1R by the engine.
The BOS candle is skipped if its range exceeds 3x ATR (stop too wide for 1:2).
"""
from __future__ import annotations
from core.models import Signal
from core import indicators as ind
from core.sessions import in_asia_entry_window

NAME = "eth_blueprint"
RR = 2.0


def generate(scfg, instruments, fetch, now):
    if not in_asia_entry_window(now):
        return []
    out = []
    interval = scfg.get("interval", "15m")
    bias_tf = scfg.get("bias_interval", "4h")
    for sym in scfg.get("instruments", []):
        inst = instruments[sym]
        bias_df = fetch(inst, bias_tf, bars=120)
        df = fetch(inst, interval, bars=200)
        if df is None or len(df) < 30 or bias_df is None or len(bias_df) < 20:
            continue
        bias = ind.trend_structure(bias_df, strength=3)
        if bias == "range":
            continue
        s = _detect(sym, df, interval, bias)
        if s:
            out.append(s)
    return out


def _detect(sym, df, interval, bias):
    highs, lows = ind.last_swings(df, strength=2, n=4)
    if len(highs) < 1 or len(lows) < 1:
        return None
    close = float(df["close"].iloc[-1])
    high = float(df["high"].iloc[-1])
    low = float(df["low"].iloc[-1])
    rng = high - low
    a = float(ind.atr(df, 14).iloc[-1])
    if a <= 0 or rng > 3 * a:        # excessively large BOS candle -> skip
        return None
    bar = df.index[-1].isoformat()

    if bias == "bull":
        resistance = highs[-1][1]
        prior_hl = lows[-1][1]
        if close > resistance and prior_hl < close:
            risk = close - prior_hl
            if risk <= 0:
                return None
            return Signal(NAME, sym, "BUY", close, prior_hl, close + RR * risk, interval, bar,
                          note=f"bull BOS close>{resistance:g}, SL@HL {prior_hl:g}")
    else:
        support = lows[-1][1]
        prior_lh = highs[-1][1]
        if close < support and prior_lh > close:
            risk = prior_lh - close
            if risk <= 0:
                return None
            return Signal(NAME, sym, "SELL", close, prior_lh, close - RR * risk, interval, bar,
                          note=f"bear BOS close<{support:g}, SL@LH {prior_lh:g}")
    return None
