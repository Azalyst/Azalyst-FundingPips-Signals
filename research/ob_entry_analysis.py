"""
Order Block detector — checks Emanuel C's entries against actual 4H OBs.
Uses H1 candle data, resamples to H4, detects OBs, then scores each trade entry.
"""
import re, json
import pandas as pd, numpy as np

# ── Load Emanuel trades from HTML ─────────────────────────────────────────────
HTML_PATH = 'outputs/signals/trade_analysis/emanuel_explorer.html'
SYMS = ['AUD/USD','GBP/JPY','XAU/USD','USTEC.v','GBP/AUD','USD/CHF',
        'GBP/USD','AUD/JPY','USD/CAD','1USO','DJ30','XAG/USD']

with open(HTML_PATH, 'r', encoding='utf-8') as f:
    html = f.read()
m = re.search(r'const TRADES\s*=\s*(\[.*?\]);', html, re.DOTALL)
trades_raw = json.loads(m.group(1))
df = pd.DataFrame(trades_raw, columns=[
    'open_ts','open_px','close_ts','close_px','sym_id','is_buy','is_win',
    'exit_code','has_sl','pnl_pct','hold_min'])
df['sym']     = df['sym_id'].apply(lambda i: SYMS[i])
df['open_dt'] = pd.to_datetime(df['open_ts'], unit='s')
df['close_dt'] = pd.to_datetime(df['close_ts'], unit='s')
df['trade_num'] = range(1, len(df)+1)
df['dir'] = df['is_buy'].map({1:'BUY', 0:'SELL', True:'BUY', False:'SELL'})

# ── Instrument file map ───────────────────────────────────────────────────────
FILE_MAP = {
    'AUD/USD': 'candle_data/H1/AUD_USD.parquet',
    'GBP/JPY': 'candle_data/H1/GBP_JPY.parquet',
    'XAU/USD': 'candle_data/H1/XAU_USD.parquet',
    'USTEC.v': 'candle_data/H1/USTEC_v.parquet',
    'GBP/AUD': 'candle_data/H1/GBP_AUD.parquet',
    'USD/CHF': 'candle_data/H1/USD_CHF.parquet',
    'GBP/USD': 'candle_data/H1/GBP_USD.parquet',
    'AUD/JPY': 'candle_data/H1/AUD_JPY.parquet',
}

# ── Order Block detector ──────────────────────────────────────────────────────
def detect_obs(h4: pd.DataFrame, atr_mult=1.5, lookforward=3) -> pd.DataFrame:
    """
    Find 4H order blocks.
    Bearish OB: last bullish candle before a strong bearish move (>= atr_mult ATR drop)
    Bullish OB: last bearish candle before a strong bullish move (>= atr_mult ATR rise)
    Returns DataFrame with cols: ob_type, ob_high, ob_low, ob_time, strength_atr
    """
    h4 = h4.copy().reset_index(drop=True)

    # ATR14
    h4['tr'] = np.maximum(
        h4['high'] - h4['low'],
        np.maximum(abs(h4['high'] - h4['close'].shift(1)),
                   abs(h4['low']  - h4['close'].shift(1))))
    h4['atr'] = h4['tr'].rolling(14).mean()

    obs = []
    for i in range(1, len(h4) - lookforward):
        row = h4.iloc[i]
        atr = row['atr']
        if pd.isna(atr) or atr == 0:
            continue

        # Check what happens in next `lookforward` bars
        future = h4.iloc[i+1 : i+1+lookforward]

        # Bearish OB: current candle is bullish; next bars drop >= atr_mult * atr
        if row['close'] > row['open']:
            drop = row['close'] - future['low'].min()
            if drop >= atr_mult * atr:
                obs.append({
                    'ob_type': 'bearish',
                    'ob_high': row['high'],
                    'ob_low':  row['open'],   # body low (more conservative entry zone)
                    'ob_time': row['time'],
                    'ob_i': i,
                    'strength_atr': drop / atr,
                })

        # Bullish OB: current candle is bearish; next bars rise >= atr_mult * atr
        if row['close'] < row['open']:
            rise = future['high'].max() - row['close']
            if rise >= atr_mult * atr:
                obs.append({
                    'ob_type': 'bullish',
                    'ob_high': row['open'],   # body high
                    'ob_low':  row['low'],
                    'ob_time': row['time'],
                    'ob_i': i,
                    'strength_atr': rise / atr,
                })

    return pd.DataFrame(obs)

# ── Check each trade against OBs ──────────────────────────────────────────────
def check_ob_hit(entry_price, entry_time, is_buy, ob_df, h4, lookback_bars=20):
    """
    Returns (hit: bool, ob_type, ob_hi, ob_lo, ob_time, dist_pct)
    Looks for OBs formed in the previous lookback_bars H4 bars before the entry.
    """
    # Find H4 index at entry time
    diffs = abs(h4['time'] - entry_time)
    idx = int(diffs.idxmin())
    start_i = max(0, idx - lookback_bars)

    # Filter OBs that were formed before entry
    relevant = ob_df[(ob_df['ob_i'] >= start_i) & (ob_df['ob_i'] < idx)]
    if relevant.empty:
        return False, None, None, None, None, None

    # Expected OB type matches trade direction
    ob_type_need = 'bearish' if not is_buy else 'bullish'
    relevant = relevant[relevant['ob_type'] == ob_type_need]
    if relevant.empty:
        return False, None, None, None, None, None

    # Check if entry price is INSIDE any OB zone
    for _, ob in relevant.sort_values('ob_i', ascending=False).iterrows():
        if ob['ob_low'] <= entry_price <= ob['ob_high']:
            dist_pct = 0.0
            return True, ob['ob_type'], ob['ob_high'], ob['ob_low'], ob['ob_time'], dist_pct

    # Check if entry price is WITHIN 1 ATR of any OB zone (near miss)
    # Get current ATR
    atr_now = h4.iloc[idx]['atr'] if 'atr' in h4.columns else 0
    for _, ob in relevant.sort_values('ob_i', ascending=False).iterrows():
        if ob_type_need == 'bearish':
            dist = entry_price - ob['ob_high']   # positive = above OB (past it)
            dist2 = ob['ob_low'] - entry_price   # positive = below OB (not reached yet)
        else:
            dist = ob['ob_low'] - entry_price    # positive = below OB (past it)
            dist2 = entry_price - ob['ob_high']  # positive = above OB (not reached yet)

        gap = min(abs(dist), abs(dist2)) if (dist < 0 or dist2 < 0) else min(dist, dist2)
        if atr_now > 0 and gap < atr_now * 0.5:
            return True, ob['ob_type'], ob['ob_high'], ob['ob_low'], ob['ob_time'], gap/ob['ob_high']*100

    return False, None, None, None, None, None

# ── Main loop ─────────────────────────────────────────────────────────────────
SEP_START = pd.Timestamp('2023-08-25')
SEP_END   = pd.Timestamp('2023-10-01')

all_results = []

for sym, filepath in FILE_MAP.items():
    trades_sym = df[df['sym'] == sym].copy()
    if len(trades_sym) == 0:
        continue

    try:
        h1 = pd.read_parquet(filepath)
    except Exception as e:
        print(f"  {sym}: cannot load {filepath} — {e}")
        continue

    h1['time'] = pd.to_datetime(h1['time'])
    h1 = h1[(h1['time'] >= '2023-07-01') & (h1['time'] <= '2023-10-05')].reset_index(drop=True)
    if len(h1) < 50:
        print(f"  {sym}: insufficient H1 data ({len(h1)} bars)")
        continue

    # Resample H1 → H4
    h1_indexed = h1.set_index('time')
    h4 = h1_indexed.resample('4h').agg(
        open=('open','first'), high=('high','max'),
        low=('low','min'), close=('close','last')).dropna().reset_index()
    h4.rename(columns={'time':'time'}, inplace=True)

    # Detect OBs
    h4['tr'] = np.maximum(
        h4['high'] - h4['low'],
        np.maximum(abs(h4['high'] - h4['close'].shift(1)),
                   abs(h4['low']  - h4['close'].shift(1))))
    h4['atr'] = h4['tr'].rolling(14).mean()
    ob_df = detect_obs(h4, atr_mult=1.5, lookforward=4)

    print(f"\n{'='*65}")
    print(f"{sym}  ({len(trades_sym)} trades)  |  OBs detected: {len(ob_df)}")
    print(f"  Sep-2023 OBs: bearish={len(ob_df[ob_df['ob_type']=='bearish'])}, bullish={len(ob_df[ob_df['ob_type']=='bullish'])}")
    print(f"{'='*65}")
    print(f"  {'T#':>4}  {'Date':>12}  {'Dir':>5}  {'Entry':>10}  {'OB hit':>7}  {'OB zone':>22}  {'PnL%':>7}  {'W/L':>4}")
    print(f"  {'-'*90}")

    sym_hits = 0
    for _, row in trades_sym.sort_values('open_dt').iterrows():
        is_buy = bool(row['is_buy'])
        hit, ob_type, ob_hi, ob_lo, ob_time, dist = check_ob_hit(
            row['open_px'], row['open_dt'], is_buy, ob_df, h4, lookback_bars=30)

        wl = 'WIN' if row['is_win'] else 'LOSS'
        hit_str = 'IN OB' if hit else '  ---'
        zone_str = f"{ob_lo:.5f}-{ob_hi:.5f}" if hit else '              ---'
        if hit:
            sym_hits += 1

        print(f"  T{int(row['trade_num']):3d}  {row['open_dt'].strftime('%b%d %H:%M'):>12}  "
              f"{'BUY' if is_buy else 'SELL':>5}  {row['open_px']:>10.5f}  "
              f"{hit_str:>7}  {zone_str:>22}  {row['pnl_pct']:>+7.2f}%  {wl:>4}")
        all_results.append({
            'sym': sym, 'trade_num': int(row['trade_num']),
            'dir': 'BUY' if is_buy else 'SELL',
            'entry': row['open_px'], 'pnl_pct': row['pnl_pct'],
            'is_win': row['is_win'], 'ob_hit': hit,
        })

    print(f"\n  OB hit rate: {sym_hits}/{len(trades_sym)} = {sym_hits/len(trades_sym):.0%}")

# ── Summary ───────────────────────────────────────────────────────────────────
res = pd.DataFrame(all_results)
print(f"\n{'='*65}")
print("OVERALL OB HIT RATE")
print(f"{'='*65}")
print(f"  Total trades checked: {len(res)}")
print(f"  OB hit:               {res['ob_hit'].sum()} ({res['ob_hit'].mean():.0%})")
print(f"  OB miss:              {(~res['ob_hit']).sum()} ({(~res['ob_hit']).mean():.0%})")
print()
for sym in FILE_MAP:
    sub = res[res['sym']==sym]
    if len(sub)==0: continue
    hr = sub['ob_hit'].mean()
    wr_hit  = sub[sub['ob_hit']]['is_win'].mean() if sub['ob_hit'].any() else float('nan')
    wr_miss = sub[~sub['ob_hit']]['is_win'].mean() if (~sub['ob_hit']).any() else float('nan')
    print(f"  {sym:12s}: OB hit={hr:.0%}  WR@OB={wr_hit:.0%}  WR@non-OB={wr_miss:.0%}  n={len(sub)}")
