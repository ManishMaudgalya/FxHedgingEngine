"""Deterministic synthetic EUR/USD quote and trade event generator.

The process is deliberately interpretable and configurable. It creates plausible
development data but is not calibrated to institutional FX observations.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import polars as pl
import yaml

from fx_hedging.data.models import MarketEvent, MarketRegime, TradeDirection


ALL_REGIMES: tuple[MarketRegime, ...] = tuple(MarketRegime)


@dataclass(frozen=True, slots=True)
class SyntheticMarketConfig:
    """Configuration for the synthetic market process."""

    currency_pair: str = "EURUSD"
    seed: int = 42
    start_time: datetime = datetime(2026, 1, 2, 8, 0, tzinfo=timezone.utc)
    num_events: int = 5_000
    event_interval_ms: int = 100
    initial_mid_price: float = 1.10000
    price_tick: float = 0.00001
    base_volatility_bps: float = 0.10
    low_volatility_multiplier: float = 0.45
    high_volatility_multiplier: float = 2.50
    base_spread_bps: float = 0.70
    base_depth: float = 5_000_000.0
    trade_probability: float = 0.72
    min_regime_duration: int = 150
    max_regime_duration: int = 450
    directional_drift_bps: float = 0.035
    mean_reversion_strength: float = 0.035
    regimes: tuple[MarketRegime, ...] = ALL_REGIMES
    output_path: Path = Path("data/synthetic/eurusd_events.parquet")

    def __post_init__(self) -> None:
        if self.currency_pair != "EURUSD":
            raise ValueError("Milestone 1 supports EURUSD only")
        if self.num_events <= 0:
            raise ValueError("num_events must be positive")
        if self.event_interval_ms <= 0:
            raise ValueError("event_interval_ms must be positive")
        for name in (
            "initial_mid_price",
            "price_tick",
            "base_volatility_bps",
            "low_volatility_multiplier",
            "high_volatility_multiplier",
            "base_spread_bps",
            "base_depth",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 <= self.trade_probability <= 1:
            raise ValueError("trade_probability must be between zero and one")
        if self.min_regime_duration <= 0:
            raise ValueError("min_regime_duration must be positive")
        if self.max_regime_duration < self.min_regime_duration:
            raise ValueError("max_regime_duration cannot be below min_regime_duration")
        if self.directional_drift_bps < 0 or self.mean_reversion_strength < 0:
            raise ValueError("drift and mean-reversion parameters must be non-negative")
        if self.start_time.tzinfo is None or self.start_time.utcoffset() is None:
            raise ValueError("start_time must be timezone-aware")
        if not self.regimes:
            raise ValueError("at least one regime is required")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> SyntheticMarketConfig:
        """Construct a config from YAML-compatible scalar values."""

        allowed = {item.name for item in fields(cls)}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown synthetic market settings: {sorted(unknown)}")
        parsed = dict(values)
        if "start_time" in parsed and isinstance(parsed["start_time"], str):
            parsed["start_time"] = datetime.fromisoformat(parsed["start_time"].replace("Z", "+00:00"))
        if "output_path" in parsed:
            parsed["output_path"] = Path(parsed["output_path"])
        if "regimes" in parsed:
            parsed["regimes"] = tuple(MarketRegime(value) for value in parsed["regimes"])
        return cls(**parsed)


class SyntheticMarketGenerator:
    """Generate deterministic market events from a local NumPy RNG."""

    def __init__(self, config: SyntheticMarketConfig) -> None:
        self.config = config

    def generate(self) -> list[MarketEvent]:
        """Generate a fresh, reproducible sequence for the configured seed."""

        cfg = self.config
        rng = np.random.default_rng(cfg.seed)
        regimes = self._regime_schedule(rng)
        mid = cfg.initial_mid_price
        anchor = cfg.initial_mid_price
        direction = float(rng.choice(np.array([-1.0, 1.0])))
        events: list[MarketEvent] = []

        for index, regime in enumerate(regimes):
            if index and regime is not regimes[index - 1]:
                anchor = mid
                direction = float(rng.choice(np.array([-1.0, 1.0])))

            volatility = cfg.base_volatility_bps
            spread_multiplier = 1.0
            depth_multiplier = 1.0
            drift_bps = 0.0

            if regime is MarketRegime.LOW_VOLATILITY:
                volatility *= cfg.low_volatility_multiplier
            elif regime is MarketRegime.HIGH_VOLATILITY:
                volatility *= cfg.high_volatility_multiplier
                spread_multiplier = 1.35
            elif regime is MarketRegime.HIGH_LIQUIDITY:
                depth_multiplier = 2.25
                spread_multiplier = 0.72
            elif regime is MarketRegime.LOW_LIQUIDITY:
                depth_multiplier = 0.35
                spread_multiplier = 1.85
                volatility *= 1.35
            elif regime is MarketRegime.DIRECTIONAL:
                drift_bps = direction * cfg.directional_drift_bps
                volatility *= 1.20
            elif regime is MarketRegime.MEAN_REVERTING:
                deviation_bps = (anchor - mid) / max(mid, cfg.price_tick) * 10_000.0
                drift_bps = cfg.mean_reversion_strength * deviation_bps

            return_bps = drift_bps + rng.normal(0.0, volatility)
            mid = max(cfg.price_tick * 10, mid * (1.0 + return_bps / 10_000.0))

            spread_noise = float(rng.lognormal(mean=-0.5 * 0.18**2, sigma=0.18))
            spread = max(
                cfg.price_tick,
                mid * cfg.base_spread_bps * spread_multiplier * spread_noise / 10_000.0,
            )
            bid = self._round_to_tick(mid - spread / 2.0)
            ask = self._round_to_tick(mid + spread / 2.0)
            if ask <= bid:
                ask = bid + cfg.price_tick

            depth_center = cfg.base_depth * depth_multiplier
            imbalance = float(np.clip(rng.normal(0.0, 0.22), -0.8, 0.8))
            total_depth = depth_center * float(rng.lognormal(mean=0.0, sigma=0.28)) * 2.0
            bid_size = max(1_000.0, total_depth * (1.0 + imbalance) / 2.0)
            ask_size = max(1_000.0, total_depth * (1.0 - imbalance) / 2.0)

            trade_price: float | None = None
            trade_size = 0.0
            trade_direction = TradeDirection.NONE
            if rng.random() < cfg.trade_probability:
                buy_probability = float(np.clip(0.5 + 0.20 * imbalance + 1.5 * drift_bps, 0.05, 0.95))
                is_buy = bool(rng.random() < buy_probability)
                trade_direction = TradeDirection.BUY if is_buy else TradeDirection.SELL
                trade_price = ask if is_buy else bid
                visible_size = ask_size if is_buy else bid_size
                participation = float(np.clip(rng.lognormal(mean=-2.0, sigma=0.65), 0.01, 0.85))
                trade_size = max(1_000.0, visible_size * participation)

            events.append(
                MarketEvent(
                    timestamp=cfg.start_time + timedelta(milliseconds=index * cfg.event_interval_ms),
                    currency_pair=cfg.currency_pair,
                    bid_price=bid,
                    ask_price=ask,
                    bid_size=round(bid_size, 2),
                    ask_size=round(ask_size, 2),
                    trade_price=trade_price,
                    trade_size=round(trade_size, 2),
                    trade_direction=trade_direction,
                    regime=regime,
                )
            )
        return events

    def to_frame(self, events: Iterable[MarketEvent] | None = None) -> pl.DataFrame:
        """Convert events to a typed Polars frame without regenerating when supplied."""

        rows = [event.to_record() for event in (events if events is not None else self.generate())]
        return pl.DataFrame(
            rows,
            schema={
                "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
                "currency_pair": pl.String,
                "bid_price": pl.Float64,
                "ask_price": pl.Float64,
                "bid_size": pl.Float64,
                "ask_size": pl.Float64,
                "trade_price": pl.Float64,
                "trade_size": pl.Float64,
                "trade_direction": pl.String,
                "regime": pl.String,
            },
        )

    def write_parquet(self, path: Path | str | None = None) -> Path:
        """Generate events and atomically replace the requested Parquet file."""

        destination = Path(path) if path is not None else self.config.output_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        self.to_frame().write_parquet(temporary, compression="zstd", statistics=True)
        temporary.replace(destination)
        return destination

    def _regime_schedule(self, rng: np.random.Generator) -> list[MarketRegime]:
        schedule: list[MarketRegime] = []
        remaining = list(self.config.regimes)
        last: MarketRegime | None = None
        while len(schedule) < self.config.num_events:
            if not remaining:
                remaining = list(self.config.regimes)
            candidates = [regime for regime in remaining if regime is not last] or remaining
            regime = candidates[int(rng.integers(0, len(candidates)))]
            remaining.remove(regime)
            duration = int(
                rng.integers(
                    self.config.min_regime_duration,
                    self.config.max_regime_duration + 1,
                )
            )
            schedule.extend([regime] * min(duration, self.config.num_events - len(schedule)))
            last = regime
        return schedule

    def _round_to_tick(self, value: float) -> float:
        tick = self.config.price_tick
        return round(round(value / tick) * tick, 10)


def load_config(path: Path | str) -> SyntheticMarketConfig:
    """Load the ``synthetic_market`` section from a YAML file."""

    with Path(path).open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    values = document.get("synthetic_market")
    if not isinstance(values, Mapping):
        raise ValueError("configuration must contain a synthetic_market mapping")
    return SyntheticMarketConfig.from_mapping(values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic EUR/USD events")
    parser.add_argument("--config", type=Path, default=Path("configs/default.yaml"))
    parser.add_argument("--output", type=Path, help="override the configured output path")
    parser.add_argument("--num-events", type=int, help="override the configured event count")
    parser.add_argument("--seed", type=int, help="override the configured random seed")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    overrides: dict[str, Any] = {}
    if args.output is not None:
        overrides["output_path"] = args.output
    if args.num_events is not None:
        overrides["num_events"] = args.num_events
    if args.seed is not None:
        overrides["seed"] = args.seed
    if overrides:
        config = replace(config, **overrides)
    destination = SyntheticMarketGenerator(config).write_parquet()
    print(f"Generated {config.num_events:,} EUR/USD events at {destination}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

