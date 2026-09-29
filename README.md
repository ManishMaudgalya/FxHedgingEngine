# FX Adaptive Hedging Engine

A research-oriented, event-driven framework for studying how an FX dealer can
hedge inventory created by client benchmark orders. The central question is
whether market-state-aware hedging can reduce inventory risk without paying an
unacceptable execution-cost penalty.

> Status: Milestone 1 is implemented, with an additional IBKR market-data
> adapter. Synthetic outputs must not be interpreted as empirical claims, and
> IBKR paper behavior must not be interpreted as live execution quality.

## Research hypotheses

- **H1:** Faster hedging reduces inventory risk.
- **H2:** Faster hedging increases execution cost.
- **H3:** Adaptive hedging improves the cost/risk trade-off.
- **H4:** Market-state information improves hedge decisions.

## Milestone 1

The current code provides:

- validated domain objects for market events, orders, executions, market state,
  inventory state, and backtest results;
- a deterministic EUR/USD event generator with low/high volatility,
  low/high liquidity, directional, and mean-reverting regimes;
- a YAML-controlled command that writes a typed Parquet dataset; and
- unit and integration tests for validation, reproducibility, quote integrity,
  regime coverage, and Parquet round-tripping.

Later milestones will add client-order inventory flows, causal features,
baseline hedging strategies, execution costs, the event-driven backtester,
optimization, adaptive/ML strategies, walk-forward evaluation, and a dashboard.

## IBKR market data

The project can now capture live EUR/USD bid/ask ticks from a paper Trader
Workstation or IB Gateway session. The IBKR adapter writes the same core schema
as the synthetic generator, so later feature and strategy code does not depend
on the source.

1. Install the official TWS API Python client supplied by IBKR into the active
   virtual environment.
2. Log into paper TWS or IB Gateway.
3. In API settings, enable socket clients and verify the displayed socket port.
4. Update `configs/ibkr_paper.yaml` to match that port.
5. Capture data:

```bash
python scripts/capture_ibkr_data.py \
  --config configs/ibkr_paper.yaml \
  --duration 300
```

The default output is `data/raw/ibkr_eurusd_ticks.parquet`. The configuration
contains no credentials; authentication remains inside TWS/IB Gateway. This
adapter is market-data-only and submits no orders. Live tick-by-tick data needs
the relevant IBKR market-data permission; delayed data cannot substitute for
tick-by-tick requests.

For the system design, research workflow, IBKR setup, and limitations, read
[`local_reference/project_guide.md`](local_reference/project_guide.md).

## Quick start

Python 3.12 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/generate_synthetic_data.py --config configs/default.yaml
pytest
```

The default command writes `data/synthetic/eurusd_events.parquet`. Override
selected settings from the command line:

```bash
python scripts/generate_synthetic_data.py \
  --config configs/default.yaml \
  --output data/synthetic/sample.parquet \
  --num-events 10000 \
  --seed 7
```

## Synthetic data schema

Each row contains `timestamp`, `currency_pair`, `bid_price`, `ask_price`,
`bid_size`, `ask_size`, `trade_price`, `trade_size`, `trade_direction`, and
`regime`. Timestamps are UTC and rows are chronological. A missing
`trade_price` represents a quote-only event and is paired with zero trade size
and `NONE` direction.

## Sign convention

Inventory is denominated in base currency. Positive inventory means long EUR;
negative inventory means short EUR. A client buying EUR from the dealer will
therefore reduce dealer inventory in Milestone 2.

## Methodology and assumptions

The simulator is a development and hypothesis-testing fixture, not a calibrated
market model. Regimes alter stochastic volatility, quote depth, spreads, and
drift/reversion rules. See [docs/assumptions.md](docs/assumptions.md) for the
explicit boundary between domain conventions and simulation assumptions.

## Repository layout

```text
configs/                 Reproducible YAML settings
data/                    Raw, processed, and synthetic data locations
src/fx_hedging/data/     Domain models and synthetic generator
scripts/                 User-facing workflow commands
tests/                   Unit and integration tests
docs/                    Methodology and development notes
```

## Development principles

All strategies will share one interface, decisions will only use information
available at the event timestamp, experiment parameters will remain external to
strategy code, and final comparisons will use chronological out-of-sample data.
