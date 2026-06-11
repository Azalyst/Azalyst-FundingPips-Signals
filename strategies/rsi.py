"""RSI strategy - Classic Wilder (14, 70/30, ATR stops) + Filtered (200MA+5MA+RSI2).

Daily bars. Classic uses a symmetric 5xATR(5) stop/target. Filtered is long-only,
with a structural stop at the recent swing low and a 1:2 target (a pragmatic,
risk-bounded stand-in for the doc's dynamic "close above 5MA" exit).
"""
from __future__ import annotations
from core.models import Signal
from core import indicators as ind

NAME = "rsi"


def generate(scfg, instruments, fetch, now):
    out = []
    interval = scfg.get("interval", "1d")
    for sym in scfg.get("instruments", []):
        inst = instruments[sym]
        df = fetch(inst, interval, bars=400)
        if df is None or len(df) < 210:
            continue
        out += _classic(sym, df, interval)
        out += _filtered(sym, df, interval)
    return out


def _classic(sym, df, interval):
    rsi14 = ind.rsi(df["close"], 14)
    atr5 = ind.atr(df, 5)
    prev, cur = rsi14.iloc[-2], rsi14.iloc[-1]
    close = float(df["close"].iloc[-1])
    a = float(atr5.iloc[-1])
    if a <= 0:
        return []
    bar = df.index[-1].isoformat()
    sigs = []
    if prev <= 30 < cur:   # crossed back above 30 -> long
        sigs.append(Signal(NAME, sym, "BUY", close, close - 5 * a, close + 5 * a,
                            interval, bar, note=f"classic: RSI14 {prev:.0f}->{cur:.0f} up thru 30"))
    if prev >= 70 > cur:   # crossed back below 70 -> short
        sigs.append(Signal(NAME, sym, "SELL", close, close + 5 * a, close - 5 * a,
                            interval, bar, note=f"classic: RSI14 {prev:.0f}->{cur:.0f} down thru 70"))
    return sigs


def _filtered(sym, df, interval):
    ma200 = ind.sma(df["close"], 200)
    ma5 = ind.sma(df["close"], 5)
    rsi2 = ind.rsi(df["close"], 2)
    close = float(df["close"].iloc[-1])
    if any(x != x for x in (ma200.iloc[-1], ma5.iloc[-1])):  # NaN guard
        return []
    uptrend = close > float(ma200.iloc[-1])
    pullback = close < float(ma5.iloc[-1])
    oversold = float(rsi2.iloc[-1]) < 20
    if not (uptrend and pullback and oversold):
        return []
    swing_low = float(df["low"].iloc[-5:].min())
    if swing_low >= close:
        return []
    risk = close - swing_low
    bar = df.index[-1].isoformat()
    return [Signal(NAME, sym, "BUY", close, swing_low, close + 2 * risk, interval, bar,
                   note=f"filtered: >200MA, <5MA, RSI2={rsi2.iloc[-1]:.0f}")]
