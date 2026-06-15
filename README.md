# Azalyst FundingPips — Phase 1/2 Challenge Engine

Autonomous multi-strategy signal engine running **7 independent $100,000 FundingPips
prop-firm challenges**. Each strategy trades its own isolated account with
**Phase 1 (+8%) → Phase 2 (+5%)** tracking and real-time day counters.

📊 **Dashboard:** [azalyst.github.io/Azalyst-FundingPips-Signals](https://azalyst.github.io/Azalyst-FundingPips-Signals/)

## Challenge Structure

| Phase | Target | Loss Limits |
|---|---|---|
| Phase 1 | +8% ($108,000) | $5,000 daily / $10,000 max |
| Phase 2 | +5% ($105,000) | $5,000 daily / $10,000 max |

When Phase 1 passes (balance hits $108k), the strategy auto-transitions to Phase 2
with a fresh $100k account. The dashboard tracks **days to pass each phase**.

## Strategies (7)

| # | Strategy | Description |
|---|---|---|
| 1 | **RSI** | Classic Wilder RSI(14) 70/30 + Filtered 200MA/5MA/RSI2 |
| 2 | **EMA5** | 5 EMA pullback entries on 5m/15m |
| 3 | **Ethereum Blueprint** | Asia-session ETH scalp with 4H bias |
| 4 | **SMT Divergence** | BTC/ETH SMT divergence on 5m |
| 5 | **JadeCap** | NY-session liquidity sweeps on 15m |
| 6 | **QUANT-X** | BTC 15m momentum breakout |
| 7 | **Rebel Funding ML** | Advanced XGBoost ML Strategy trained on dynamic spreads and momentum metrics |

## Running

```bash
pip install -r requirements.txt

python run.py              # single tick
python run.py --dry-run    # no Discord alerts
python run.py --test-discord
python reset.py            # fresh challenge reset
```

Automated via GitHub Actions every 15 minutes.

## Disclaimer

Educational paper-trading record. Not financial advice.
