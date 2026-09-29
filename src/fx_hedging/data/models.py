"""Validated domain objects shared by the simulation and later backtester."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path
from typing import Any, Mapping


class Side(StrEnum):
    """Order side from the actor placing the order."""

    BUY = "BUY"
    SELL = "SELL"


class TradeDirection(StrEnum):
    """Aggressor direction, UNKNOWN when the source does not classify it."""

    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"
    NONE = "NONE"


class MarketRegime(StrEnum):
    """Synthetic state active when a market event is generated."""

    LOW_VOLATILITY = "LOW_VOLATILITY"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    HIGH_LIQUIDITY = "HIGH_LIQUIDITY"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    DIRECTIONAL = "DIRECTIONAL"
    MEAN_REVERTING = "MEAN_REVERTING"


def _require_aware(timestamp: datetime, name: str = "timestamp") -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_positive(value: float, name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _require_non_negative(value: float, name: str) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


def _require_pair(currency_pair: str) -> None:
    if len(currency_pair) != 6 or not currency_pair.isalpha() or not currency_pair.isupper():
        raise ValueError("currency_pair must be a six-letter uppercase code such as EURUSD")


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """One chronological quote update with an optional contemporaneous trade."""

    timestamp: datetime
    currency_pair: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    trade_price: float | None
    trade_size: float
    trade_direction: TradeDirection
    regime: MarketRegime | None = None

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        _require_pair(self.currency_pair)
        _require_positive(self.bid_price, "bid_price")
        _require_positive(self.ask_price, "ask_price")
        if self.ask_price < self.bid_price:
            raise ValueError("ask_price must be greater than or equal to bid_price")
        _require_non_negative(self.bid_size, "bid_size")
        _require_non_negative(self.ask_size, "ask_size")
        _require_non_negative(self.trade_size, "trade_size")
        if self.trade_direction is TradeDirection.NONE:
            if self.trade_price is not None or self.trade_size != 0:
                raise ValueError("quote-only events require no trade_price and zero trade_size")
        else:
            if self.trade_price is None:
                raise ValueError("trade events require trade_price")
            _require_positive(self.trade_price, "trade_price")
            if self.trade_size == 0:
                raise ValueError("trade events require positive trade_size")

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price

    def to_record(self) -> dict[str, Any]:
        """Return scalar values suitable for a Polars/Arrow row."""

        return {
            "timestamp": self.timestamp,
            "currency_pair": self.currency_pair,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "trade_price": self.trade_price,
            "trade_size": self.trade_size,
            "trade_direction": self.trade_direction.value,
            "regime": self.regime.value if self.regime is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ClientOrder:
    """Client benchmark order, expressed as positive base-currency quantity."""

    timestamp: datetime
    currency_pair: str
    quantity: float
    side: Side
    benchmark_time: datetime

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        _require_aware(self.benchmark_time, "benchmark_time")
        _require_pair(self.currency_pair)
        _require_positive(self.quantity, "quantity")
        if self.benchmark_time < self.timestamp:
            raise ValueError("benchmark_time cannot precede order timestamp")


@dataclass(frozen=True, slots=True)
class HedgeOrder:
    """Strategy request to trade a positive quantity on the specified side."""

    timestamp: datetime
    currency_pair: str
    quantity: float
    side: Side
    reason: str
    strategy: str

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        _require_pair(self.currency_pair)
        _require_positive(self.quantity, "quantity")
        if not self.reason.strip():
            raise ValueError("reason cannot be blank")
        if not self.strategy.strip():
            raise ValueError("strategy cannot be blank")


@dataclass(frozen=True, slots=True)
class Execution:
    """Fill details with independently reported simulated cost components."""

    timestamp: datetime
    currency_pair: str
    side: Side
    requested_quantity: float
    filled_quantity: float
    price: float
    spread_cost: float
    market_impact: float
    slippage_cost: float
    total_cost: float

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        _require_pair(self.currency_pair)
        _require_positive(self.requested_quantity, "requested_quantity")
        _require_non_negative(self.filled_quantity, "filled_quantity")
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("filled_quantity cannot exceed requested_quantity")
        _require_positive(self.price, "price")
        for name in ("spread_cost", "market_impact", "slippage_cost", "total_cost"):
            _require_non_negative(getattr(self, name), name)
        component_total = self.spread_cost + self.market_impact + self.slippage_cost
        tolerance = max(1e-9, abs(self.total_cost) * 1e-9)
        if abs(self.total_cost - component_total) > tolerance:
            raise ValueError("total_cost must equal its reported cost components")


@dataclass(frozen=True, slots=True)
class InventoryState:
    """Point-in-time dealer inventory; positive means long the base currency."""

    timestamp: datetime
    currency_pair: str
    quantity: float

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        _require_pair(self.currency_pair)
        if not isfinite(self.quantity):
            raise ValueError("quantity must be finite")


@dataclass(frozen=True, slots=True)
class MarketState:
    """Latest market snapshot consumed by feature and strategy layers."""

    timestamp: datetime
    currency_pair: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    regime: MarketRegime
    realized_volatility: float = 0.0

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        _require_pair(self.currency_pair)
        _require_positive(self.bid_price, "bid_price")
        _require_positive(self.ask_price, "ask_price")
        if self.ask_price < self.bid_price:
            raise ValueError("ask_price must be greater than or equal to bid_price")
        _require_non_negative(self.bid_size, "bid_size")
        _require_non_negative(self.ask_size, "ask_size")
        _require_non_negative(self.realized_volatility, "realized_volatility")

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Compact metadata and aggregate values for a completed backtest."""

    strategy: str
    currency_pair: str
    started_at: datetime
    ended_at: datetime
    final_inventory: float
    total_pnl: float
    total_execution_cost: float
    results_path: Path | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.strategy.strip():
            raise ValueError("strategy cannot be blank")
        _require_pair(self.currency_pair)
        _require_aware(self.started_at, "started_at")
        _require_aware(self.ended_at, "ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        for name in ("final_inventory", "total_pnl"):
            if not isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        _require_non_negative(self.total_execution_cost, "total_execution_cost")
        if any(not isfinite(value) for value in self.metrics.values()):
            raise ValueError("metrics must contain finite values")
