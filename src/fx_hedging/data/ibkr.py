"""IBKR TWS/IB Gateway adapter for capturing EUR/USD tick-by-tick data.

The official ``ibapi`` package is loaded lazily so the research package and its
offline tests remain usable without a running Trader Workstation installation.
No credentials are accepted or stored: authentication happens in TWS or IB
Gateway before this client opens a local socket connection.
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import polars as pl
import yaml

from fx_hedging.data.models import MarketEvent, TradeDirection


class IbkrApiUnavailableError(RuntimeError):
    """Raised when the official IBKR Python API is not installed."""


class IbkrConnectionError(RuntimeError):
    """Raised when TWS/IB Gateway cannot provide a usable market-data stream."""


@dataclass(frozen=True, slots=True)
class IbkrMarketDataConfig:
    """Connection, FX contract, and capture settings for IBKR market data."""

    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 17
    base_currency: str = "EUR"
    quote_currency: str = "USD"
    exchange: str = "IDEALPRO"
    duration_seconds: float = 60.0
    connection_timeout_seconds: float = 10.0
    include_trades: bool = False
    output_path: Path = Path("data/raw/ibkr_eurusd_ticks.parquet")

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("host cannot be blank")
        if not 1 <= self.port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        if self.client_id < 0:
            raise ValueError("client_id must be non-negative")
        for name in ("base_currency", "quote_currency"):
            value = getattr(self, name)
            if len(value) != 3 or not value.isalpha() or not value.isupper():
                raise ValueError(f"{name} must be a three-letter uppercase currency code")
        if self.base_currency == self.quote_currency:
            raise ValueError("base_currency and quote_currency must differ")
        if not self.exchange.strip():
            raise ValueError("exchange cannot be blank")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if self.connection_timeout_seconds <= 0:
            raise ValueError("connection_timeout_seconds must be positive")

    @property
    def currency_pair(self) -> str:
        return f"{self.base_currency}{self.quote_currency}"

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> IbkrMarketDataConfig:
        allowed = {item.name for item in fields(cls)}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown IBKR market-data settings: {sorted(unknown)}")
        parsed = dict(values)
        if "output_path" in parsed:
            parsed["output_path"] = Path(parsed["output_path"])
        return cls(**parsed)


class IbkrTickAssembler:
    """Convert asynchronous IBKR callbacks into the project's event schema.

    Bid/ask ticks are complete quote-only events. Optional last-trade callbacks
    reuse the most recently received quote. IBKR does not provide aggressor side
    in that callback, so direction is deliberately recorded as UNKNOWN rather
    than inferred and silently presented as fact.
    """

    def __init__(self, currency_pair: str = "EURUSD") -> None:
        self.currency_pair = currency_pair
        self._latest_quote: tuple[float, float, float, float] | None = None
        self._events: list[MarketEvent] = []
        self._lock = threading.Lock()
        self.dropped_trades_without_quote = 0

    @property
    def events(self) -> tuple[MarketEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def on_bid_ask(
        self,
        epoch_seconds: int,
        bid_price: float,
        ask_price: float,
        bid_size: float,
        ask_size: float,
    ) -> None:
        """Append a validated quote event, ignoring IBKR sentinel values."""

        values = (bid_price, ask_price, bid_size, ask_size)
        if not all(math.isfinite(value) for value in values):
            return
        if bid_price <= 0 or ask_price <= 0 or ask_price < bid_price:
            return
        if bid_size < 0 or ask_size < 0:
            return
        event = MarketEvent(
            timestamp=datetime.fromtimestamp(epoch_seconds, tz=timezone.utc),
            currency_pair=self.currency_pair,
            bid_price=float(bid_price),
            ask_price=float(ask_price),
            bid_size=float(bid_size),
            ask_size=float(ask_size),
            trade_price=None,
            trade_size=0.0,
            trade_direction=TradeDirection.NONE,
            regime=None,
        )
        with self._lock:
            self._latest_quote = (event.bid_price, event.ask_price, event.bid_size, event.ask_size)
            self._events.append(event)

    def on_last(self, epoch_seconds: int, price: float, size: float) -> None:
        """Append a trade event when a prior quote is available."""

        if not math.isfinite(price) or not math.isfinite(size) or price <= 0 or size <= 0:
            return
        with self._lock:
            quote = self._latest_quote
            if quote is None:
                self.dropped_trades_without_quote += 1
                return
            bid_price, ask_price, bid_size, ask_size = quote
            self._events.append(
                MarketEvent(
                    timestamp=datetime.fromtimestamp(epoch_seconds, tz=timezone.utc),
                    currency_pair=self.currency_pair,
                    bid_price=bid_price,
                    ask_price=ask_price,
                    bid_size=bid_size,
                    ask_size=ask_size,
                    trade_price=float(price),
                    trade_size=float(size),
                    trade_direction=TradeDirection.UNKNOWN,
                    regime=None,
                )
            )

    def to_frame(self) -> pl.DataFrame:
        rows = [event.to_record() for event in self.events]
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


class IbkrMarketDataCollector:
    """Manage the TWS socket lifecycle and persist tick-by-tick events."""

    BID_ASK_REQUEST_ID = 10_001
    LAST_REQUEST_ID = 10_002
    INFORMATIONAL_CODES = {2104, 2106, 2107, 2108, 2158}

    def __init__(self, config: IbkrMarketDataConfig) -> None:
        self.config = config
        self.assembler = IbkrTickAssembler(config.currency_pair)
        self.errors: list[tuple[int, str]] = []
        self._fatal_error = threading.Event()

    def collect(self) -> pl.DataFrame:
        """Capture ticks for the configured duration and return them in memory."""

        app, contract = self._build_ibkr_objects()
        reader: threading.Thread | None = None
        try:
            app.connect(self.config.host, self.config.port, self.config.client_id)
            reader = threading.Thread(target=app.run, name="ibkr-api-reader", daemon=True)
            reader.start()
            if not app.ready.wait(self.config.connection_timeout_seconds):
                raise IbkrConnectionError(
                    "TWS/IB Gateway handshake timed out; verify it is running, API socket access "
                    "is enabled, and the configured paper-trading port matches TWS"
                )

            app.reqTickByTickData(self.BID_ASK_REQUEST_ID, contract, "BidAsk", 0, False)
            if self.config.include_trades:
                app.reqTickByTickData(self.LAST_REQUEST_ID, contract, "AllLast", 0, False)

            deadline = time.monotonic() + self.config.duration_seconds
            while time.monotonic() < deadline:
                if self._fatal_error.wait(timeout=min(0.2, max(0.0, deadline - time.monotonic()))):
                    details = "; ".join(f"{code}: {message}" for code, message in self.errors)
                    raise IbkrConnectionError(f"IBKR rejected the market-data request: {details}")
        finally:
            if app.isConnected():
                app.cancelTickByTickData(self.BID_ASK_REQUEST_ID)
                if self.config.include_trades:
                    app.cancelTickByTickData(self.LAST_REQUEST_ID)
                app.disconnect()
            if reader is not None:
                reader.join(timeout=2.0)

        frame = self.assembler.to_frame()
        if frame.is_empty():
            details = "; ".join(f"{code}: {message}" for code, message in self.errors)
            suffix = f" Errors: {details}" if details else ""
            raise IbkrConnectionError(
                "IBKR returned no tick data. Confirm EUR/USD is visible in TWS and that the "
                f"account has the required live market-data permission.{suffix}"
            )
        return frame

    def write_parquet(self, path: Path | str | None = None) -> Path:
        destination = Path(path) if path is not None else self.config.output_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        self.collect().write_parquet(temporary, compression="zstd", statistics=True)
        temporary.replace(destination)
        return destination

    def _build_ibkr_objects(self) -> tuple[Any, Any]:
        try:
            from ibapi.client import EClient
            from ibapi.contract import Contract
            from ibapi.wrapper import EWrapper
        except ModuleNotFoundError as exc:
            raise IbkrApiUnavailableError(
                "The official IBKR Python API is not installed. Download the TWS API package "
                "from IBKR and install its pythonclient into this virtual environment."
            ) from exc

        collector = self

        class App(EClient, EWrapper):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:
                EClient.__init__(self, self)
                self.ready = threading.Event()

            def nextValidId(self, orderId: int) -> None:  # noqa: N802 - IBKR callback name
                del orderId
                self.ready.set()

            def tickByTickBidAsk(  # noqa: N802 - IBKR callback name
                self,
                reqId: int,
                tick_time: int,
                bidPrice: float,
                askPrice: float,
                bidSize: Any,
                askSize: Any,
                tickAttribBidAsk: Any,
            ) -> None:
                del reqId, tickAttribBidAsk
                collector.assembler.on_bid_ask(
                    tick_time,
                    float(bidPrice),
                    float(askPrice),
                    float(bidSize),
                    float(askSize),
                )

            def tickByTickAllLast(  # noqa: N802 - IBKR callback name
                self,
                reqId: int,
                tickType: int,
                tick_time: int,
                price: float,
                size: Any,
                tickAttribLast: Any,
                exchange: str,
                specialConditions: str,
            ) -> None:
                del reqId, tickType, tickAttribLast, exchange, specialConditions
                collector.assembler.on_last(tick_time, float(price), float(size))

            def error(self, reqId: int, *args: Any) -> None:
                del reqId
                if len(args) >= 3 and isinstance(args[1], int):
                    error_code, error_message = int(args[1]), str(args[2])
                elif len(args) >= 2:
                    error_code, error_message = int(args[0]), str(args[1])
                else:
                    return
                collector.errors.append((error_code, error_message))
                if error_code not in collector.INFORMATIONAL_CODES:
                    collector._fatal_error.set()

        contract = Contract()
        contract.symbol = self.config.base_currency
        contract.secType = "CASH"
        contract.exchange = self.config.exchange
        contract.currency = self.config.quote_currency
        return App(), contract


def load_ibkr_config(path: Path | str) -> IbkrMarketDataConfig:
    with Path(path).open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    values = document.get("ibkr_market_data")
    if not isinstance(values, Mapping):
        raise ValueError("configuration must contain an ibkr_market_data mapping")
    return IbkrMarketDataConfig.from_mapping(values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture EUR/USD ticks from IBKR TWS/IB Gateway")
    parser.add_argument("--config", type=Path, default=Path("configs/ibkr_paper.yaml"))
    parser.add_argument("--duration", type=float, help="capture duration in seconds")
    parser.add_argument("--output", type=Path, help="override the configured Parquet path")
    parser.add_argument("--host", help="override the configured TWS/IB Gateway host")
    parser.add_argument("--port", type=int, help="override the configured socket port")
    parser.add_argument("--client-id", type=int, help="override the configured client ID")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_ibkr_config(args.config)
    overrides = {
        name: value
        for name, value in {
            "duration_seconds": args.duration,
            "output_path": args.output,
            "host": args.host,
            "port": args.port,
            "client_id": args.client_id,
        }.items()
        if value is not None
    }
    if overrides:
        config = replace(config, **overrides)
    try:
        destination = IbkrMarketDataCollector(config).write_parquet()
    except (IbkrApiUnavailableError, IbkrConnectionError, OSError) as exc:
        print(f"IBKR capture failed: {exc}", file=sys.stderr)
        return 2
    print(f"Captured {config.currency_pair} tick data at {destination}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

