"""JadeCap Intraday Liquidity & Volatility Model.

During the NY killzone (09:30-11:30 ET): a marked session level (PDH/PDL, Asian or
London high/low) is swept, the breakout fails, and a confirmation prints (FVG or a
market-structure shift). Enter back toward the opposite session liquidity.
SL beyond the sweep extreme; TP = nearest opposite liquidity, else 1:2.
"""
from __future__ import annotations
from core.models import Signal
from core import indicators as ind
from core.sessions import in_ny_killzone, session_levels

NAME = "jadecap"
SWEEP_LOOKBACK = 6
RR_FALLBACK = 2.0


def generate(scfg, instruments, fetch, now):
    if not in_ny_killzone(now):
        return []
    out = []
    interval = scfg.get("interval", "15m")
    for sym in scfg.get("instruments", []):
        inst = instruments[sym]
        df = fetch(inst, interval, bars=600)
        if df is None or len(df) < 40:
            continue
        levels = session_levels(df, now)
        if not levels:
            continue
        s = _detect(sym, df, interval, levels)
        if s:
            out.append(s)
    return out


def _detect(sym, df, interval, levels):
    close = float(df["close"].iloc[-1])
    recent = df.iloc[-SWEEP_LOOKBACK:]
    n = len(df)
    bar = df.index[-1].isoformat()

    buy_side = [("PDH", levels.get("PDH")), ("asian_high", levels.get("asian_high")),
                ("london_high", levels.get("london_high"))]
    sell_side = [("PDL", levels.get("PDL")), ("asian_low", levels.get("asian_low")),
                 ("london_low", levels.get("london_low"))]

    # --- bearish: sweep a high then fail back below ---
    best = None
    for name, lvl in buy_side:
        if lvl is None or lvl <= close:
            continue
        pierced = recent[recent["high"] > lvl]
        if pierced.empty:
            continue
        if best is None or lvl < best[1]:
            best = (name, lvl, float(pierced["high"].max()))
    if best is not None:
        mss_down = close < float(df["low"].iloc[-4:-1].min())
        fvg = ind.bearish_fvg(df, n - 1)
        if mss_down or fvg:
            stop = best[2]
            risk = stop - close
            if risk > 0:
                target = _nearest_below(close, [levels.get("asian_low"), levels.get("PDL"),
                                                levels.get("london_low")])
                tp = target if target is not None else close - RR_FALLBACK * risk
                return Signal(NAME, sym, "SELL", close, stop, tp, interval, bar,
                              note=f"swept {best[0]} {best[1]:g}, "
                                   f"{'FVG' if fvg else 'MSS'} confirm")

    # --- bullish: sweep a low then reclaim above ---
    best = None
    for name, lvl in sell_side:
        if lvl is None or lvl >= close:
            continue
        pierced = recent[recent["low"] < lvl]
        if pierced.empty:
            continue
        if best is None or lvl > best[1]:
            best = (name, lvl, float(pierced["low"].min()))
    if best is not None:
        mss_up = close > float(df["high"].iloc[-4:-1].max())
        fvg = ind.bullish_fvg(df, n - 1)
        if mss_up or fvg:
            stop = best[2]
            risk = close - stop
            if risk > 0:
                target = _nearest_above(close, [levels.get("asian_high"), levels.get("PDH"),
                                                levels.get("london_high")])
                tp = target if target is not None else close + RR_FALLBACK * risk
                return Signal(NAME, sym, "BUY", close, stop, tp, interval, bar,
                              note=f"swept {best[0]} {best[1]:g}, "
                                   f"{'FVG' if fvg else 'MSS'} confirm")
    return None


def _nearest_below(price, levels):
    cands = [l for l in levels if l is not None and l < price]
    return max(cands) if cands else None


def _nearest_above(price, levels):
    cands = [l for l in levels if l is not None and l > price]
    return min(cands) if cands else None
