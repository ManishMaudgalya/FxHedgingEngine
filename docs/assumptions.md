# Assumptions and Scope

## Domain conventions

- EUR/USD quantities are denominated in EUR, the base currency.
- Positive dealer inventory means long EUR; negative means short EUR.
- Trade direction is the aggressor side. A buy trade occurs at the simulated ask
  and a sell trade occurs at the simulated bid.
- All timestamps are timezone-aware UTC values and events are chronological.

## Synthetic-market assumptions

The Milestone 1 generator is intentionally a transparent engineering fixture.
It has not been calibrated against venue-level FX data and must not be treated
as evidence about real execution quality, volatility, liquidity, or impact.

- Mid-price changes combine Gaussian noise with regime-specific drift.
- Directional regimes apply a fixed signed drift for the regime block.
- Mean-reverting regimes pull toward the mid-price observed at regime entry.
- Spreads are positive lognormal perturbations of a configured baseline.
- Displayed depth is a lognormal draw divided by a bounded imbalance.
- Trade direction depends on the same contemporaneous imbalance and drift used
  to create the current event; no future values are used.
- Trade size is a bounded fraction of same-side displayed depth.
- Regime durations are random but bounded and reproducible from the local seed.

These choices provide controlled variation for later tests. They are simulation
assumptions, not an empirically validated microstructure model.

## IBKR observations

- IBKR data is treated as an external observation source, not ground truth for
  the whole decentralized FX market.
- Tick-by-tick timestamps supplied by the callback have one-second resolution;
  callback arrival order is retained for events sharing a timestamp.
- Bid/ask observations have no synthetic regime label. Regimes will be derived
  causally by the feature layer in a later milestone.
- The `AllLast` callback does not classify aggressor direction. When enabled,
  captured trades are marked `UNKNOWN`; the collector never invents a side.
- Paper-account fills are simulated and will not be used as evidence of live
  execution quality.
