"""QUANT-X - multi-agent consensus (mechanical adaptation of the AI pipeline).

The doc describes 4 AI agents + 7 indicators committing BUY/SELL/HOLD only on
consensus. The LLM layer (Mistral/Ollama) can't run on a cron runner, so this is a
faithful rules-based analog: 7 indicators feed 4 agent verdicts; the master commits
only when >=3 agents agree (and Risk isn't HIGH). SL=1.5xATR, TP=3xATR (1:2).
"""
from __future__ import annotations
from core.models import Signal
from core import indicators as ind

NAME = "quantx"


def generate(scfg, instruments, fetch, now):
    out = []
    interval = scfg.get("interval", "15m")
    for sym in scfg.get("instruments", []):
        inst = instruments[sym]
        df = fetch(inst, interval, bars=300)
        if df is None or len(df) < 60:
            continue
        s = _decide(sym, df, interval)
        if s:
            out.append(s)
    return out


def _decide(sym, df, interval):
    close = float(df["close"].iloc[-1])
    rsi = float(ind.rsi(df["close"], 14).iloc[-1])
    _, _, hist = ind.macd(df["close"])
    macd_hist = float(hist.iloc[-1])
    vwap = float(ind.session_vwap(df).iloc[-1])
    ema20 = float(ind.ema(df["close"], 20).iloc[-1])
    ema50 = float(ind.ema(df["close"], 50).iloc[-1])
    a = float(ind.atr(df, 14).iloc[-1])
    vol = float(df["volume"].iloc[-1])
    vol_avg = float(df["volume"].iloc[-20:].mean())
    if a <= 0:
        return None

    # 4 agents -> +1 bull / -1 bear / 0 neutral
    market = 1 if (ema20 > ema50 and macd_hist > 0) else (-1 if (ema20 < ema50 and macd_hist < 0) else 0)
    sentiment = 1 if (rsi > 55 and close > vwap) else (-1 if (rsi < 45 and close < vwap) else 0)
    liquidity = 1 if vol > vol_avg else 0          # volume confirms participation
    risk_high = (a / close) > 0.05                 # too volatile -> stand aside

    direction = market + sentiment
    agree_bull = sum(v == 1 for v in (market, sentiment)) + (1 if liquidity and direction > 0 else 0)
    agree_bear = sum(v == -1 for v in (market, sentiment)) + (1 if liquidity and direction < 0 else 0)

    bar = df.index[-1].isoformat()
    if risk_high:
        return None
    if agree_bull >= 3:
        stop = close - 1.5 * a
        return Signal(NAME, sym, "BUY", close, stop, close + 3 * a, interval, bar,
                      note=f"agents bull (RSI {rsi:.0f}, MACD+{macd_hist:.2f}, >VWAP)")
    if agree_bear >= 3:
        stop = close + 1.5 * a
        return Signal(NAME, sym, "SELL", close, stop, close - 3 * a, interval, bar,
                      note=f"agents bear (RSI {rsi:.0f}, MACD {macd_hist:.2f}, <VWAP)")
    return None
