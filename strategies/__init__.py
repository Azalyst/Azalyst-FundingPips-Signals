"""Strategy registry.

Each strategy module exposes:
    NAME: str
    generate(scfg, instruments, fetch, now) -> list[Signal]
where `fetch(inst_cfg, interval, bars) -> DataFrame` and `now` is tz-aware UTC.
"""
from . import rsi, ema5, eth_blueprint, smt_divergence, jadecap, quantx, ob

REGISTRY = {
    rsi.NAME: rsi,
    ema5.NAME: ema5,
    eth_blueprint.NAME: eth_blueprint,
    smt_divergence.NAME: smt_divergence,
    jadecap.NAME: jadecap,
    quantx.NAME: quantx,
    ob.NAME: ob,
}

# strategies whose open positions are managed with a break-even move at 1R
BREAKEVEN_STRATEGIES = {"eth_blueprint"}
