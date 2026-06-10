"""
Emanuel OB Strategy — FundingPips Challenge Backtest
Instruments: AUD/USD, GBP/JPY, XAU/USD
Method: H4 Order Block detection + H1 retest entry + H4 trend filter
FundingPips rules: $100k, 5% daily limit, 10% max DD, 8% profit target
"""
import pandas as pd, numpy as np
import warnings
warnings.filterwarnings('ignore')

# ── Challenge config (FundingPips Phase 1) ────────────────────────────────────
ACCOUNT_START   = 100_000.0
DAILY_LIMIT_USD = 5_000.0    # 5% max daily loss
MAX_DD_USD      = 10_000.0   # 10% max overall drawdown → floor = $90,000
PROFIT_TARGET   = 8_000.0    # 8% = $108,000
RISK_PCT        = 0.01       # 1% risk per trade
CHALLENGE_DAYS  = 30
MIN_TRADE_DAYS  = 3
FRIDAY_CLOSE_UTC = 21        # close all positions before Friday 21:00 UTC

# ── OB strategy config ────────────────────────────────────────────────────────
OB_ATR_MULT    = 1.5    # ATR multiplier for OB-forming move
OB_LOOKFWD     = 4      # H4 bars ahead to check for OB-forming move
OB_LOOKBACK    = 25     # max H4 bars back to search for valid OBs
SL_BUFFER_ATR  = 0.35   # ATR fraction added beyond OB edge for SL
TP_RR          = 2.0    # reward:risk ratio
MAX_PER_OB     = 2      # max entries per OB zone
MAX_OPEN       = 4      # max simultaneous open positions across all symbols

# ── Instruments ───────────────────────────────────────────────────────────────
# pip_value: USD profit per 1 pip move per 1 standard lot
INSTRUMENTS = {
    'AUD_USD': {'pip_size': 0.0001, 'pip_value': 10.0,  'file': 'candle_data/H1/AUD_USD.parquet'},
    'GBP_JPY': {'pip_size': 0.01,   'pip_value': 7.0,   'file': 'candle_data/H1/GBP_JPY.parquet'},
    'XAU_USD': {'pip_size': 0.10,   'pip_value': 10.0,  'file': 'candle_data/H1/XAU_USD.parquet'},
}

# ── Load and prepare data ─────────────────────────────────────────────────────
print("Loading candle data...")
h1_data = {}
for sym, cfg in INSTRUMENTS.items():
    df = pd.read_parquet(cfg['file'])
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)
    h1_data[sym] = df
    print(f"  {sym}: {len(df)} H1 bars  {df['time'].iloc[0].date()} – {df['time'].iloc[-1].date()}")

def make_h4(h1: pd.DataFrame) -> pd.DataFrame:
    h = h1.set_index('time').resample('4h').agg(
        open=('open','first'), high=('high','max'),
        low=('low','min'), close=('close','last')).dropna().reset_index()
    # ATR14
    h['tr'] = np.maximum(h['high']-h['low'],
               np.maximum(abs(h['high']-h['close'].shift(1)),
                          abs(h['low'] -h['close'].shift(1))))
    h['atr'] = h['tr'].rolling(14).mean()
    # EMA20 for trend
    h['ema20'] = h['close'].ewm(span=20, adjust=False).mean()
    return h

print("Building H4 data...")
h4_data = {sym: make_h4(h1_data[sym]) for sym in INSTRUMENTS}

# ── OB detection (on pre-built H4 slice) ─────────────────────────────────────
def detect_obs_slice(h4: pd.DataFrame) -> pd.DataFrame:
    obs = []
    for i in range(1, len(h4) - OB_LOOKFWD):
        row = h4.iloc[i]
        atr = row['atr']
        if pd.isna(atr) or atr == 0:
            continue
        fut = h4.iloc[i+1 : i+1+OB_LOOKFWD]
        # Bearish OB
        if row['close'] > row['open']:
            drop = row['close'] - fut['low'].min()
            if drop >= OB_ATR_MULT * atr:
                obs.append({'ob_type':'bearish','ob_high':row['high'],
                            'ob_low':row['open'],'ob_time':row['time'],'ob_i':i,'atr':atr})
        # Bullish OB
        if row['close'] < row['open']:
            rise = fut['high'].max() - row['close']
            if rise >= OB_ATR_MULT * atr:
                obs.append({'ob_type':'bullish','ob_high':row['open'],
                            'ob_low':row['low'],'ob_time':row['time'],'ob_i':i,'atr':atr})
    return pd.DataFrame(obs) if obs else pd.DataFrame(columns=['ob_type','ob_high','ob_low','ob_time','ob_i','atr'])

# ── Challenge simulator ───────────────────────────────────────────────────────
def run_challenge(start_date: str, label: str = '') -> dict:
    start = pd.Timestamp(start_date)
    end   = start + pd.Timedelta(days=CHALLENGE_DAYS)

    account   = ACCOUNT_START
    peak      = ACCOUNT_START
    day_pnl   = 0.0
    last_day  = None
    positions = []   # list of dicts
    ob_entries = {}  # ob_id → count of entries
    trade_log  = []
    equity_curve = []
    trading_days = set()
    status    = 'active'
    fail_reason = ''

    # Pre-slice data for this window (with extra lookback for OB detection)
    lookback_start = start - pd.Timedelta(days=20)

    sym_h1 = {}
    sym_h4 = {}
    for sym in INSTRUMENTS:
        h1 = h1_data[sym]
        h4 = h4_data[sym]
        sym_h1[sym] = h1[(h1['time'] >= lookback_start) & (h1['time'] < end)].reset_index(drop=True)
        sym_h4[sym] = h4[(h4['time'] >= lookback_start) & (h4['time'] < end)].reset_index(drop=True)

    # Get all H1 bars in challenge window, sorted
    all_h1 = []
    for sym in INSTRUMENTS:
        df = sym_h1[sym].copy()
        df['sym'] = sym
        all_h1.append(df)
    all_h1 = pd.concat(all_h1).sort_values('time').reset_index(drop=True)

    # Only process bars within the challenge window
    challenge_bars = all_h1[all_h1['time'] >= start]

    for _, bar in challenge_bars.iterrows():
        t   = bar['time']
        sym = bar['sym']
        cfg = INSTRUMENTS[sym]

        # ── Day rollover ──────────────────────────────────────────────────────
        bar_date = t.date()
        if last_day is not None and bar_date != last_day:
            # Check daily limit at EOD
            if day_pnl < -DAILY_LIMIT_USD:
                status = 'failed'
                fail_reason = f'Daily loss limit breached: ${day_pnl:+.0f} on {last_day}'
                break
            day_pnl = 0.0
        last_day = bar_date
        equity_curve.append({'time': t, 'equity': account, 'sym': sym})

        # ── Friday forced close ───────────────────────────────────────────────
        is_friday_close = (t.weekday() == 4 and t.hour >= FRIDAY_CLOSE_UTC)
        if is_friday_close:
            for pos in list(positions):
                if pos['sym'] == sym:
                    exit_price = bar['close']
                    pnl = _calc_pnl(pos, exit_price, cfg)
                    account += pnl
                    day_pnl += pnl
                    trade_log.append({**pos, 'exit_price': exit_price, 'exit_time': t,
                                      'pnl_usd': pnl, 'exit_reason': 'friday_close'})
                    positions.remove(pos)
            continue

        # ── Update open positions (SL / TP hits) ─────────────────────────────
        for pos in list(positions):
            if pos['sym'] != sym:
                continue
            if status != 'active':
                break
            hit, exit_price, reason = _check_fill(pos, bar)
            if hit:
                pnl = _calc_pnl(pos, exit_price, cfg)
                account += pnl
                day_pnl += pnl
                peak = max(peak, account)
                trade_log.append({**pos, 'exit_price': exit_price, 'exit_time': t,
                                  'pnl_usd': pnl, 'r_mult': pnl / (cfg['pip_value'] * pos['risk_pips']),
                                  'exit_reason': reason})
                positions.remove(pos)

                # Check drawdown
                if account < (ACCOUNT_START - MAX_DD_USD):
                    status = 'failed'
                    fail_reason = f'Max drawdown breached: equity ${account:.0f}'
                    break
                # Check daily limit
                if day_pnl < -DAILY_LIMIT_USD:
                    status = 'failed'
                    fail_reason = f'Daily loss limit breached: ${day_pnl:+.0f} on {bar_date}'
                    break
                # Check profit target
                if account >= (ACCOUNT_START + PROFIT_TARGET):
                    status = 'passed'
                    break

        if status != 'active':
            break

        # ── Skip signal generation if limits are near (safety buffer 80%) ─────
        if day_pnl < -DAILY_LIMIT_USD * 0.8:
            continue
        if account < (ACCOUNT_START - MAX_DD_USD * 0.85):
            continue
        if len([p for p in positions if p['sym'] == sym]) >= MAX_PER_OB:
            continue
        if len(positions) >= MAX_OPEN:
            continue

        # ── OB detection on H4 data available up to now ───────────────────────
        h4 = sym_h4[sym]
        h4_before = h4[h4['time'] < t].tail(OB_LOOKBACK + OB_LOOKFWD + 5)
        if len(h4_before) < 20:
            continue

        obs = detect_obs_slice(h4_before)
        if obs.empty:
            continue

        # ── H4 trend from EMA20 ───────────────────────────────────────────────
        last_h4 = h4_before.iloc[-1]
        prev_h4 = h4_before.iloc[-3] if len(h4_before) >= 3 else h4_before.iloc[0]
        h4_trend = 'bullish' if last_h4['ema20'] > prev_h4['ema20'] else 'bearish'

        # ── Check for OB retest ───────────────────────────────────────────────
        # Only look at OBs formed within lookback window and matching trend
        valid_obs = obs[
            (obs['ob_time'] >= t - pd.Timedelta(hours=4 * OB_LOOKBACK)) &
            (obs['ob_type'] == h4_trend)   # trend-aligned OBs only
        ].sort_values('ob_time', ascending=False)

        for _, ob in valid_obs.iterrows():
            ob_id = f"{sym}_{ob['ob_time']}_{ob['ob_type']}"

            # Skip if already entered this OB too many times
            if ob_entries.get(ob_id, 0) >= MAX_PER_OB:
                continue

            # Skip if OB has been invalidated (price closed beyond it on H4)
            invalidated = False
            h4_after_ob = h4_before[h4_before['time'] > ob['ob_time']]
            for _, h4r in h4_after_ob.iterrows():
                if ob['ob_type'] == 'bearish' and h4r['close'] > ob['ob_high']:
                    invalidated = True; break
                if ob['ob_type'] == 'bullish' and h4r['close'] < ob['ob_low']:
                    invalidated = True; break
            if invalidated:
                continue

            # Check if current H1 bar touches the OB zone
            touches_ob = (bar['low'] <= ob['ob_high']) and (bar['high'] >= ob['ob_low'])
            if not touches_ob:
                continue

            # ── Build the trade ───────────────────────────────────────────────
            atr = ob['atr']
            if ob['ob_type'] == 'bearish':
                entry = min(bar['high'], ob['ob_high'])
                sl    = ob['ob_high'] + SL_BUFFER_ATR * atr
                tp    = entry - TP_RR * (sl - entry)
                direction = 'SELL'
            else:
                entry = max(bar['low'], ob['ob_low'])
                sl    = ob['ob_low'] - SL_BUFFER_ATR * atr
                tp    = entry + TP_RR * (entry - sl)
                direction = 'BUY'

            sl_dist = abs(entry - sl)
            if sl_dist < cfg['pip_size']:
                continue   # degenerate SL

            risk_pips = sl_dist / cfg['pip_size']
            risk_usd  = account * RISK_PCT
            lots      = risk_usd / (risk_pips * cfg['pip_value'])
            lots      = min(lots, 50.0)   # hard cap

            # Final pre-trade risk check
            worst_open_risk = sum(p['risk_usd'] for p in positions)
            if worst_open_risk + risk_usd > DAILY_LIMIT_USD * 0.85:
                continue

            pos = {
                'sym': sym, 'direction': direction, 'ob_id': ob_id,
                'entry': entry, 'sl': sl, 'tp': tp, 'lots': lots,
                'risk_pips': risk_pips, 'risk_usd': risk_usd,
                'open_time': t, 'is_buy': direction == 'BUY',
            }
            positions.append(pos)
            ob_entries[ob_id] = ob_entries.get(ob_id, 0) + 1
            trading_days.add(bar_date)
            break   # one signal per bar per instrument

    # ── Force-close remaining positions at last bar price ─────────────────────
    for sym in INSTRUMENTS:
        last_bar = sym_h1[sym][sym_h1[sym]['time'] < end].iloc[-1] if len(sym_h1[sym]) else None
        for pos in list(positions):
            if pos['sym'] == sym and last_bar is not None:
                pnl = _calc_pnl(pos, last_bar['close'], INSTRUMENTS[sym])
                account += pnl
                trade_log.append({**pos, 'exit_price': last_bar['close'],
                                  'exit_time': last_bar['time'],
                                  'pnl_usd': pnl, 'exit_reason': 'challenge_end'})

    # ── Final status check ────────────────────────────────────────────────────
    if status == 'active':
        if account >= ACCOUNT_START + PROFIT_TARGET:
            status = 'passed'
        elif account < ACCOUNT_START - MAX_DD_USD:
            status = 'failed'; fail_reason = 'max drawdown'
        elif len(trading_days) < MIN_TRADE_DAYS:
            status = 'failed'; fail_reason = f'insufficient trading days ({len(trading_days)})'
        else:
            status = 'timeout'   # time ran out, didn't reach target

    trades_df = pd.DataFrame(trade_log) if trade_log else pd.DataFrame()
    wins = (trades_df['pnl_usd'] > 0).sum() if len(trades_df) else 0
    total = len(trades_df)
    net_pnl = account - ACCOUNT_START

    return {
        'label': label or start_date,
        'start': start_date,
        'status': status,
        'fail_reason': fail_reason,
        'account': account,
        'net_pnl': net_pnl,
        'net_pct': net_pnl / ACCOUNT_START * 100,
        'trades': total,
        'wins': wins,
        'wr': wins / total if total else 0,
        'trading_days': len(trading_days),
        'trades_df': trades_df,
    }

def _check_fill(pos, bar):
    """Returns (hit, exit_price, reason)"""
    if pos['is_buy']:
        if bar['low'] <= pos['sl']:
            return True, pos['sl'], 'sl'
        if bar['high'] >= pos['tp']:
            return True, pos['tp'], 'tp'
    else:
        if bar['high'] >= pos['sl']:
            return True, pos['sl'], 'sl'
        if bar['low'] <= pos['tp']:
            return True, pos['tp'], 'tp'
    return False, None, None

def _calc_pnl(pos, exit_price, cfg):
    direction = 1 if pos['is_buy'] else -1
    move = (exit_price - pos['entry']) * direction
    pips = move / cfg['pip_size']
    return pips * cfg['pip_value'] * pos['lots']

# ── Run backtest across multiple 30-day windows ───────────────────────────────
print("\nRunning FundingPips challenge simulations...")
print("=" * 75)

windows = [
    ('2023-09-01', 'Sep-2023 (Emanuel original)'),
    ('2023-10-01', 'Oct-2023'),
    ('2023-11-01', 'Nov-2023'),
    ('2023-12-01', 'Dec-2023'),
    ('2024-01-01', 'Jan-2024'),
    ('2024-02-01', 'Feb-2024'),
    ('2024-03-01', 'Mar-2024'),
    ('2024-04-01', 'Apr-2024'),
    ('2024-05-01', 'May-2024'),
    ('2024-06-01', 'Jun-2024'),
    ('2024-07-01', 'Jul-2024'),
    ('2024-08-01', 'Aug-2024'),
    ('2024-09-01', 'Sep-2024'),
    ('2024-10-01', 'Oct-2024'),
    ('2024-11-01', 'Nov-2024'),
    ('2024-12-01', 'Dec-2024'),
    ('2025-01-01', 'Jan-2025'),
    ('2025-02-01', 'Feb-2025'),
    ('2025-03-01', 'Mar-2025'),
    ('2025-04-01', 'Apr-2025'),
    ('2025-05-01', 'May-2025'),
    ('2025-06-01', 'Jun-2025'),
    ('2025-07-01', 'Jul-2025'),
    ('2025-08-01', 'Aug-2025'),
]

results = []
for start, label in windows:
    # Check if we have enough data
    if pd.Timestamp(start) > pd.Timestamp('2026-04-01'):
        continue
    r = run_challenge(start, label)
    # Enforce minimum trading days
    if r['status'] == 'passed' and r['trading_days'] < MIN_TRADE_DAYS:
        r['status'] = 'days_short'
        r['fail_reason'] = f"only {r['trading_days']} trading days (need {MIN_TRADE_DAYS})"
    icon = 'PASS' if r['status'] == 'passed' else ('FAIL' if r['status'] in ('failed','days_short') else 'TIME')
    print(f"  [{icon}] {r['label']:<30}  {r['status'].upper():<10}  "
          f"PnL={r['net_pct']:>+6.1f}%  trades={r['trades']:>3}  "
          f"WR={r['wr']:>5.1%}  days={r['trading_days']:>2}"
          + (f"  [{r['fail_reason']}]" if r['fail_reason'] else ''))
    results.append(r)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("SUMMARY")
print("=" * 75)
n = len(results)
passed  = sum(1 for r in results if r['status'] == 'passed')
failed  = sum(1 for r in results if r['status'] in ('failed','days_short'))
timeout = sum(1 for r in results if r['status'] in ('timeout',))
avg_pnl = np.mean([r['net_pct'] for r in results])
avg_wr  = np.mean([r['wr'] for r in results if r['trades'] > 0])

print(f"  Windows tested: {n}")
print(f"  PASSED:         {passed} ({passed/n:.0%})")
print(f"  FAILED:         {failed} ({failed/n:.0%})")
print(f"  TIMEOUT:        {timeout} ({timeout/n:.0%})")
print(f"  Avg net PnL:    {avg_pnl:+.1f}%")
print(f"  Avg WR:         {avg_wr:.1%}")
print()

# Best/worst
best  = max(results, key=lambda r: r['net_pct'])
worst = min(results, key=lambda r: r['net_pct'])
print(f"  Best:   {best['label']}  {best['net_pct']:+.1f}%  ({best['status']})")
print(f"  Worst:  {worst['label']}  {worst['net_pct']:+.1f}%  ({worst['status']})")

# Save summary CSV
import csv, io
rows = [{'period': r['label'], 'status': r['status'], 'net_pct': round(r['net_pct'],2),
         'trades': r['trades'], 'wr': round(r['wr']*100,1), 'trading_days': r['trading_days'],
         'fail_reason': r['fail_reason']} for r in results]
out_path = 'outputs/signals/trade_analysis/ob_fp_backtest_results.csv'
pd.DataFrame(rows).to_csv(out_path, index=False)
print(f"\n  Results saved to: {out_path}")
