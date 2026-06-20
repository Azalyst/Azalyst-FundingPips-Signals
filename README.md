# Azalyst-Montecarlo-Quant

Azalyst Montecarlo Quant is a systematic prop-firm trading engine that converts the
behaviour of thousands of real funded traders into a statistically validated
strategy — and then races several risk throttles of it, live, to pass a FundingPips
2-step evaluation as fast as possible.

Rather than stacking discretionary setups, it reverse-engineers **what actually
passes a two-step** from a corpus of **301,472 real prop-firm trades**, isolates the
one edge that survives out-of-sample, and runs it as a **fast-pass fleet** — three
aggression levels trading the same edge in parallel, each its own $100k account,
auto-resetting on a bust — entirely on GitHub Actions, with no broker dependency.

**Live Dashboard:** [azalyst.github.io/Azalyst-Montecarlo-Quant](https://azalyst.github.io/Azalyst-Montecarlo-Quant/)
&nbsp;·&nbsp; **Fast-pass:** [research/FAST_PASS.md](research/FAST_PASS.md)
&nbsp;·&nbsp; **Method:** [research/MASTER_REPORT.md](research/MASTER_REPORT.md)
&nbsp;·&nbsp; **Playbook:** [research/PLAYBOOK.md](research/PLAYBOOK.md)

## The Azalyst Montecarlo Edge

- **Evidence-Derived Strategy.** Every one of 301,472 leaderboard trades is replayed
  through the exact FundingPips two-step rule-set. The finding is decisive — traders
  fail on the **5% daily-loss line, not the profit target**, and the discriminator
  between passers and busters is single-trade loss size, not style. The whole engine
  is built around that result.
- **Out-of-Sample Instrument Gating.** A single rule across nine instruments loses;
  the edge is gold-specific. A stability gate admits an instrument only if its
  expectancy is positive across **both halves of the training window** — which is why
  over-fit indices and FX are rejected *before* the held-out test. Currently only
  **gold (XAU/USD H1)** clears the bar.
- **Monster Mechanics, Made Rule-Correct.** The real >100%/month traders hit +8% in a
  median of two days by sizing up and letting one trade run; the 10% max-drawdown
  never caught them because it is measured from *initial* balance — only the 5% daily
  line constrains the style. The engine reproduces this with **scale-in** (add a
  tranche on a +1R winner, combined stop moved to base entry — the add rides on house
  money) and an uncapped trailing exit.
- **The Fast-Pass Fleet.** Three books — **MED 1.0% · AGGRESSIVE 1.5% · MAX 2.25%** —
  trade the same signals at different risk. Higher risk passes *faster* but busts
  *more*; each book auto-resets on a bust and logs every attempt, so the dashboard is
  a live, honest leaderboard of which aggression level gets funded most efficiently.
- **Honest, Haircut-Adjusted Numbers.** Out-of-sample (4,000-path bootstrap),
  scale-in passes ~52–78% by risk. After an adversarial regime haircut, the realistic
  figure is **~40% per attempt in a trending gold year and ≤30% if gold ranges** —
  this is a **trend-rider**, not an all-weather edge. Budget 2–3 attempts.

## How it works

| Layer | Specification |
|---|---|
| **Instrument** | Gold (XAU/USD H1) via yfinance `GC=F` — the only instrument with a train-and-test-consistent edge. |
| **Entry** | Momentum breakout: long within 0.05·ATR of the prior 20-bar high in an uptrend (symmetric short), next-bar open. |
| **Exit** | 2·ATR stop → break-even at +1R → 3·ATR trailing stop → 96-bar time stop. No fixed take-profit — one runner carries the month. |
| **Scale-in** | At +1R, add a same-size tranche; move the combined stop to the base entry. House money funds the add. |
| **Fleet** | MED 1.0% / AGGRESSIVE 1.5% / MAX 2.25% per-trade risk — each a $100k 2-step account, auto-resetting on bust. |
| **Rules** | Phase 1 +8% → Phase 2 +5%; 5% daily ($5k of initial) / 10% static max; ≥3 trading days. |

## Architecture

```
 ╔══════════════════════════════════════════════════════════════════╗
 ║                  AZALYST MONTECARLO QUANT                         ║
 ║        gold 2-step · fast-pass fleet · free data · autonomous     ║
 ╚══════════════════════════════════════════════════════════════════╝

      ┌── CRON ──┐   GitHub Actions, twice hourly (idempotent)
      │ 2×/hour  │
      └────┬─────┘
           ▼
 ┌────────────────────┐    yfinance GC=F · H1 · closed bars only · UTC
 │ DATA  engine/data  │
 └─────────┬──────────┘
           ▼
 ┌────────────────────┐    causal EMA/RSI/ATR · 20-bar breakout
 │ SIGNAL indicators  │    entry = signal[t-1] → open[t]  (no look-ahead)
 └─────────┬──────────┘
           ▼
 ┌──────────────────────────────────────────────┐
 │ FLEET  engine/fleet.py                        │
 │   MED 1.0%   AGGRESSIVE 1.5%   MAX 2.25%       │
 │   each → engine/challenge.py (one $100k book) │
 │     scale-in · risk governors · 2-step rules  │
 │     bust → log attempt → auto-reset → retry   │
 └───────────────────┬──────────────────────────┘
                     ▼
        ┌────────────┴────────────┐
        ▼                         ▼
 ┌──────────────┐         ┌────────────────────────┐
 │ DISCORD      │         │ DASHBOARD  docs/        │
 │ (optional)   │         │ regime · race · ledger  │
 └──────────────┘         │ GitHub Pages            │
                          └────────────────────────┘
```

## Method & Validation

- **Reverse-engineering** (`research/`): 3,440 trader-months replayed through the
  two-step rules; the survivorship-robust signal is the passer-vs-buster contrast.
- **Train/test discipline:** rules and parameters chosen on TRAIN (≤ 2025-06-30); the
  held-out TEST window is touched once. Gold out-of-sample: profit factor 1.46,
  +27.5% return, 7.5% max drawdown.
- **Fast-pass frontier** (`research/FAST_PASS.md`): a 4,000-path bootstrap shows
  scale-in at 1.25% dominates flat 1.75% (≈66% pass / 23% bust / median 7 trading
  days vs 55% / 36%). An adversarial review applies the honest regime haircut.

## Running

```bash
pip install -r requirements.txt

python run.py                 # one fleet tick: fetch gold → advance books → dashboard → alerts
python run.py --test-discord  # webhook connectivity test
python run.py --reset         # reset the fleet to fresh accounts
```

Automated by `.github/workflows/signals.yml` (twice hourly). Fleet state persists in
`state/challenge.json`; the dashboard feed is `docs/status.json` (GitHub Pages from
`/docs`). Set `mode: safe` (and remove `fleet:`) in `config.yaml` for the single
high-pass-rate account instead. Discord alerts are optional via a repo secret
`DISCORD_WEBHOOK_URL`.

## System Scope and Limitations

- A quantitative research **simulation** (paper trading), not a live broker
  integration. Orders are fully specified but not routed.
- Gold-only by design; the data does not support a stable mechanical edge elsewhere.
- The strategy is a **trend-rider** — pass rates are regime-dependent and the headline
  figures reflect a favourable gold up-trend. Real per-attempt odds are nearer ~40%.
- Free-data only (yfinance). A sustained outage skips the tick cleanly; state is never
  corrupted.

## License

MIT

> Educational paper-trading record derived from a research project. Backtests and
> Monte-Carlo describe the past; the future may differ. **Not financial advice.**
