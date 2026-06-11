"""5 EMA Game Changer - mean-reversion break of an "alert candle".

Sell setup on 5m: alert candle closes ABOVE 5 EMA and its LOW does not touch the EMA;
trigger when a later candle breaks the alert low -> short, SL = alert high, TP = 1:3.
Buy setup on 15m: alert candle closes BELOW 5 EMA and its HIGH does not touch the EMA;
trigger when a later candle breaks the alert high -> long, SL = alert low, TP = 1:3.
The latest closed bar must be the trigger bar (so each break fires once).
"""
from __future__ import annotations
from core.models import Signal
from core import indicators as ind

NAME = "ema5"
RR = 3.0
LOOKBACK = 25
ATR_FLOOR_MULT = 0.6     # minimum stop = 0.6 x ATR(14): keeps the alert-candle stop
                         # clear of intrabar noise so a tick can't stop us in seconds


def generate(scfg, instruments, fetch, now):
    out = []
    for sym in scfg.get("instruments", []):
        inst = instruments[sym]
        sell_tf = scfg.get("interval_sell", "5m")
        buy_tf = scfg.get("interval_buy", "15m")
        df_s = fetch(inst, sell_tf, bars=120)
        if df_s is not None and len(df_s) > 10:
            s = _detect(sym, df_s, sell_tf, "SELL")
            if s:
                out.append(s)
        df_b = fetch(inst, buy_tf, bars=120)
        if df_b is not None and len(df_b) > 10:
            s = _detect(sym, df_b, buy_tf, "BUY")
            if s:
                out.append(s)
    return out


def _detect(sym, df, interval, side):
    e = ind.ema(df["close"], 5)
    n = len(df)
    last = n - 1
    o, h, l, c = (df["open"].values, df["high"].values,
                  df["low"].values, df["close"].values)
    ev = e.values

    # find the most recent still-active alert candle before the last bar
    alert = None
    for i in range(last - 1, max(0, last - LOOKBACK), -1):
        if side == "SELL":
            is_alert = c[i] > ev[i] and l[i] > ev[i]
        else:
            is_alert = c[i] < ev[i] and h[i] < ev[i]
        if is_alert:
            # active only if no bar between i and last-1 already triggered it
            triggered_already = False
            for j in range(i + 1, last):
                if side == "SELL" and l[j] < l[i]:
                    triggered_already = True
                    break
                if side == "BUY" and h[j] > h[i]:
                    triggered_already = True
                    break
            if not triggered_already:
                alert = i
            break

    if alert is None:
        return None

    atr_floor = ATR_FLOOR_MULT * float(ind.atr(df, 14).iloc[-1])

    if side == "SELL":
        trigger_level = l[alert]
        if l[last] < trigger_level:          # latest bar breaks the alert low
            entry = trigger_level
            stop = h[alert]
            risk = stop - entry
            if risk <= 0:
                return None
            if risk < atr_floor:             # alert-candle stop too tight -> widen to noise floor
                risk = atr_floor
                stop = entry + risk
            note = f"alert candle {df.index[alert]:%m-%d %H:%M} broken"
            return Signal(NAME, sym, "SELL", entry, stop, entry - RR * risk, interval,
                          df.index[alert].isoformat(), note=note)
    else:
        trigger_level = h[alert]
        if h[last] > trigger_level:          # latest bar breaks the alert high
            entry = trigger_level
            stop = l[alert]
            risk = entry - stop
            if risk <= 0:
                return None
            if risk < atr_floor:             # alert-candle stop too tight -> widen to noise floor
                risk = atr_floor
                stop = entry - risk
            note = f"alert candle {df.index[alert]:%m-%d %H:%M} broken"
            return Signal(NAME, sym, "BUY", entry, stop, entry + RR * risk, interval,
                          df.index[alert].isoformat(), note=note)
    return None
