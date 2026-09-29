# AI-Assisted Development Log

## Milestone 1 foundation

**Task:** Create domain models, a deterministic synthetic EUR/USD generator,
Parquet output, configuration, and tests from the project specification.

**AI contribution:** Drafted the initial package structure, validation rules,
simulation process, command-line workflow, test suite, and documentation.

**Human review:** Pending. Review the inventory sign convention, domain-field
semantics, regime parameter ranges, and simulation assumptions before using the
data for research conclusions.

**Validation:** Automated unit and integration tests cover domain validation,
fixed-seed reproducibility, chronological ordering, quote/trade consistency,
regime coverage, isolation from NumPy global random state, and Parquet output.

**Changes made:** Milestone 1 only. No hedging logic, execution-cost calibration,
optimization, prediction model, or performance claims were added.

## IBKR market-data adapter

**Task:** Introduce the user's paper TWS/IB Gateway as a real market-data source
without coupling downstream research code to IBKR callbacks.

**AI contribution:** Reviewed official IBKR documentation, added a lazy-loaded
socket adapter, callback-to-domain translation, YAML settings, a capture command,
unit tests, and a detailed project guide.

**Human review:** Confirm the socket port displayed in the user's paper TWS,
install IBKR's official Python client, confirm EUR/USD permissions, and inspect a
sample capture before treating it as research input.

**Validation:** Offline tests cover connection configuration, callback mapping,
invalid/sentinel quotes, missing quote context, non-fabricated trade direction,
and compatibility with the common Parquet schema. A live integration test is
not possible without the user's authenticated TWS/IB Gateway session.
