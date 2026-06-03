# Azalyst-FundingPips-Signals

Azalyst FundingPips is an autonomous, multi-strategy signal engine built to operate
prop-firm evaluations with institutional discipline. It runs six independent trading
playbooks across FX, metals, index futures, and crypto, generates BUY/SELL signals on
a fixed cron, and **paper-trades each strategy in its own isolated $100,000 FundingPips
challenge** — every strategy carries its own balance, daily/maximum-loss budget and
PASSED / FAILED / ACTIVE status, so a losing playbook fails on its own book without
dragging the others down. The result is an exact, rule-safe trade for the operator to
mirror and a fully transparent, per-strategy track record.

The platform is entirely serverless: discovery, risk-gating, execution simulation,
Discord dispatch, and dashboard publication all run from a single GitHub Actions
pipeline with no local machine required.

Live Intelligence Dashboard: [https://azalyst.github.io/Azalyst-FundingPips-Signals/](https://azalyst.github.io/Azalyst-FundingPips-Signals/)

## The Azalyst FundingPips Edge

- **Six-Playbook Signal Stack**: Six independent, fully-documented strategies (RSI,
  5 EMA mean-reversion, Ethereum Blueprint, SMT Divergence, JadeCap Liquidity, QUANT-X
  consensus) run in parallel, each gated to the instruments, timeframes, and trading
  sessions its source playbook specifies.
- **Isolated Per-Strategy Challenges**: Each of the six strategies runs its own
  independent $100,000 challenge. A strategy is marked **FAILED** the moment its book
  breaches the −10% maximum-loss floor ($90,000) or the −5% daily-loss limit, and
  **PASSED** when its balance reaches the +8% profit target ($108,000); failed and
  passed books stop trading. Every result is shown live on the dashboard.
- **Rule-Safe by Construction**: Every signal is sized to risk exactly 1% of its book
  and is *blocked before entry* unless `realized loss today + worst-case open risk +
  this trade's risk` stays inside that book's $5,000 daily and $10,000 maximum loss
  budgets. The FundingPips rules cannot be broken even if every open stop is hit.
- **Session & Structure Fidelity**: Session gates are enforced to the playbook letter —
  Ethereum Blueprint trades only the first three hours of the Asia session; JadeCap
  only inside the New York killzone (09:30–11:30 ET, DST-aware). Liquidity sweeps,
  fair-value gaps, market-structure shifts, and swing pivots are detected mechanically.
- **Noise & Leverage Discipline**: A per-instrument minimum-stop filter rejects
  sub-noise stops (the 5 EMA "minimum SL" rule), and a 30× notional cap prevents a tight
  stop from producing an un-mirrorable position — both enforced before any trade opens.
- **Transparent Track Record**: Break-even-aware paper books — one per strategy — commit
  their full state and PnL ledger back to the repository each run and render a live,
  Bloomberg-style dashboard of all six challenges, so the simulated record is auditable
  end to end.

## Strategies

| Engine | Source playbook | Instruments | Timeframe | Core logic |
|---|---|---|---|---|
| `rsi` | RSI Strategy | EURUSD, GBPUSD, XAUUSD, NAS100, SP500, BTC, ETH | Daily | Classic 14/70-30 cross with 5×ATR stop; Filtered 200MA + 5MA + RSI(2) < 20 |
| `ema5` | 5 EMA Game Changer | EURUSD, GBPUSD, XAUUSD, NAS100 | 5m sell / 15m buy | Alert candle not touching the 5 EMA → break → 1:3, 3-stop daily circuit breaker |
| `eth_blueprint` | Ethereum Blueprint | ETHUSD | 15m exec, 4H bias | Asia-session Break of Structure in HTF-bias direction, SL@HL/LH, 1:2, break-even at 1R |
| `smt_divergence` | SMT Divergence | BTC / ETH | 5m | Trade the asset that fails the correlated high/low, structural stop, 1:2 |
| `jadecap` | JadeCap Liquidity & Volatility | EURUSD, GBPUSD, NAS100, SP500 | 15m | NY-killzone sweep of session liquidity + FVG/MSS confirmation → opposite liquidity target |
| `quantx` | QUANT-X | BTCUSD | 15m | 7-indicator / 4-agent consensus (≥3 agree, Risk not HIGH) → 1:2 |

### Faithful adaptations (documented, not hidden)

- **QUANT-X** is described as an LLM (Mistral / Ollama) four-agent system on a
  four-second loop. A cron runner cannot host the LLM, so the four agents (Market, Risk,
  Liquidity, Sentiment) are reproduced as deterministic rules over the same seven
  indicators (RSI, MACD, VWAP, EMA20, EMA50, ATR, Volume). The consensus gate is kept.
- **RSI Filtered** uses a dynamic "close above 5MA" exit in the source. Here it is given
  a structural stop (recent swing low) and a 1:2 target so risk is bounded for paper
  execution.
- **15-minute cron vs 5-minute setups**: each tick evaluates the latest *closed* bars.
  Fast intrabar 5m triggers between ticks can be missed — a deliberate cost/fidelity
  tradeoff. The cadence is a single line in `config.yaml`.

## Architecture

```
            ┌── CRON ──┐
            │  15 min  │
            └────┬─────┘
                 ▼
   ┌─────────────────────────────┐
   │  DATA LAYER                 │
   │  ─────────                  │
   │  yfinance  → FX/metal/index │
   │  Bybit     → crypto (+yf fb)│
   │  closed bars only, UTC      │
   └──────────────┬──────────────┘
                  ▼
   ┌─────────────────────────────────────────────┐
   │   SIX-PLAYBOOK SIGNAL STACK (per instrument) │
   │   rsi · ema5 · eth_blueprint · smt · jadecap · quantx │
   │   session-gated · structure-aware            │
   └──────────────┬──────────────────────────────┘
                  ▼   dedupe + no-conflict
   ┌─────────────────────────────────────────────┐
   │  RISK ENGINE (FundingPips)                   │
   │  1% sizing · min-stop filter · 30x cap       │
   │  gate: daily $5k / max $10k cannot break     │
   └──────────────┬──────────────────────────────┘
                  ▼
   ┌─────────────────────────────────────────────┐
   │  PAPER BOOK                                  │
   │  open · break-even@1R · SL/TP resolve        │
   │  realized PnL · equity · daily reset         │
   └─────┬───────────────────────────┬────────────┘
         ▼                           ▼
   ┌──────────┐              ┌─────────────────┐
   │ DISCORD  │              │   DASHBOARD     │
   │ ──────── │              │   ─────────     │
   │ entry ⚡ │              │  objective bars │
   │ close ⚡ │              │  equity curve   │
   │ @ping    │              │  positions      │
   │ alerts   │              │  strategy PnL   │
   └──────────┘              └─────────────────┘
                                    │
                                    ▼
                    commit state + docs/status.json
```

## Risk Engine

Position size = `risk_usd / stop_distance`, so a stop-out loses exactly 1% of the
account. Before any trade opens, the gate verifies:

- `realized_loss_today + worst_case_open_risk + new_trade_risk ≤ daily_budget`
- `equity − worst_case_open_risk − new_trade_risk ≥ overall_floor` ($90,000)
- the trade is inside the challenge window (2026-06-03 → 2026-07-02, Day 1 = 2026-06-03)
- the stop is wider than the per-instrument noise floor and notional is within 30×

Open positions that have moved to break-even contribute zero to worst-case risk. The
5 EMA three-stop daily circuit breaker and per-symbol no-conflicting-position rules are
enforced in the orchestrator.

## Data

- **FX / metals / index futures** → yfinance (`EURUSD=X`, `GC=F`, `NQ=F`, …).
- **Crypto** → Bybit public REST (reliable on GitHub runners, where Binance is
  geo-blocked), with a yfinance fallback.

Only USD-PnL instruments are traded, so position sizing and PnL are exact in account
currency without cross-FX conversion.

## Autonomous Deployment (No Local Setup Required)

The engine is serverless. You do not need to run anything locally.

### 1. Add the Discord secret
**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Purpose |
|---|---|
| `DISCORD_WEBHOOK_URL` | Live entry/exit/alert dispatch to Discord (pings the configured user id) |

### 2. Enable Actions & Pages
- **Actions** tab → enable workflows.
- **Settings → Pages** → Source: *Deploy from a branch* → branch `main`, folder `/docs`.

### 3. Let it run
- **Every 15 minutes**, `signals.yml` resolves open paper trades, generates fresh
  signals, risk-gates and sizes them, dispatches Discord alerts, writes the PnL report,
  rebuilds `docs/status.json`, and commits state back to the repository.
- Manual run / connectivity test: **Actions → FundingPips Signals → Run workflow**
  (tick `test_discord` for a one-off ping).

## Local Use

```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python run.py --dry-run      # evaluate + paper-trade, print instead of posting
python run.py --test-discord # send a single connectivity ping
python run.py                # one live tick
```

State persists in `state/` (account, positions, dedupe, equity curve); the human-readable
ledger is `reports/report.md`; the dashboard payload is `docs/status.json`.

## Dashboard And Public Track Record

The GitHub Pages dashboard reads `docs/status.json` and renders:

- **Trading objectives** — the FundingPips daily-loss and maximum-loss bars with live
  remaining headroom and breach floors
- **Account banner** — equity, net and daily PnL, win rate, challenge day, account status
- **Equity curve** — simulated NAV against the $100,000 baseline
- open positions (with break-even flags), the closed-trade track record, and per-strategy
  realized PnL

The simulated record is a transparent research log for ongoing strategy validation.

## Core Philosophies

- **Rule Safety First**: No signal is worth a blown evaluation. The risk gate is
  adversarial — it assumes every open stop hits before it permits the next trade.
- **Playbook Fidelity**: Each engine implements its source document's instruments,
  timeframes, sessions, and exits; every necessary adaptation is documented, not hidden.
- **Execution Realism**: Stops, targets, break-even moves, and conservative same-bar fills
  (stop assumed before target) are modelled so the track record reflects tradeable reality.
- **Transparency**: State, ledger, and dashboard are committed every run; the record is
  auditable, not asserted.

## System Scope and Limitations

- Designed for quantitative research simulation and signal mirroring, not direct broker
  execution.
- A 15-minute cron evaluates closed bars; sub-bar 5m triggers between ticks are missed by
  design.
- Data layers degrade gracefully — if a feed is unreachable, the affected strategies
  no-op and the rest of the system continues.
- Spread/commission are not modelled per fill; the minimum-stop filter is the primary
  guard against noise-level entries.

## Disclaimer

Educational paper-trading tool. Signals are mechanical interpretations of the source
playbooks and do not constitute financial advice. Position sizing models a $100,000
FundingPips account at 1% risk per trade under a $5,000 daily / $10,000 maximum loss
limit. Past and simulated performance does not guarantee future results.

## License

MIT
