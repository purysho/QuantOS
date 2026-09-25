# Stage 14 — OpenSourceRisk/Engine Differential Adapter

Stage 11 deliberately stopped before OpenSourceRisk/Engine (ORE). Stage 14 introduces ORE only as an **independent comparison engine** behind QuantOS contracts. It is never the source of truth.

## 1. Frozen version and build

- The distribution is `open-source-risk-engine==1.8.17.0`, the ORE-SWIG Python bindings built on QuantLib under the modified BSD license. It is recorded in the license gate.
- It is an **optional extra**: `uv sync --locked --extra ore`. Wheels exist only for x86-64 Linux and Windows, so the requirement carries platform markers, and the core install stays portable (for example to macOS arm64).
- `ore_is_available()` requires the exact pinned version.
- CI installs the extra on every Python version (3.11–3.13) and sets `QUANTOS_REQUIRE_ORE=1`, so the ORE runtime tests cannot silently skip.
- The environment manifest records the installed ORE version, or `NOT_INSTALLED`.

## 2. Service boundary: process isolation

ORE's SWIG bindings share the SWIG runtime type table with the QuantLib Python package. With both loaded in one process, objects from one library are destroyed through the other's wrappers, and the interpreter crashes. This was observed on the second ORE run after QuantLib objects were created. ORE also keeps process-global QuantLib state and a global logger.

QuantOS therefore runs ORE only in a child process, `python -m quantos.ore_worker`, which never imports QuantLib or QuantOS modules. It exchanges one JSON request and one JSON response. `OREEngineRunner`:

- serializes launches;
- enforces a timeout;
- checks the reported ORE version;
- checks the ORE error list, the exact trade set and the NPV currency.

Any failure is a `ValueError`. There is no fallback to another engine.

## 3. Input mapping and lineage

`OREInputBuilder` is pure Python and testable without ORE. It writes:

- **conventions:** a continuous A365F zero convention, and a convention-defined Ibor index `CCY-FCREF-<n>M` with 0 settlement days, NullCalendar, Unadjusted, no end-of-month rule and the swap's floating day count;
- **curve configuration:** Direct zero segments with date-based quotes, `Discount`/`LogLinear` interpolation, A365F and **no extrapolation**, reproducing the Stage 11.3 curve semantics;
- **today's market, pricing engine** (`DiscountingSwapEngine`) and a **portfolio** with one swap: NullCalendar, Unadjusted, Forward, zero fixing days;
- **market data lines** from each curve pillar's zero rate.

The complete bundle is content-addressed as an `OREInputBundle`. Every differential records the bundle ID, so the exact ORE inputs can be reproduced.

Out-of-scope inputs fail closed before ORE runs:

- extrapolating or non-log-linear curves;
- curves in another currency or on another valuation date;
- same-day or past-starting swaps;
- 30/360;
- maturity beyond the last pillar;
- tampered curve artifacts.

## 4. Stage 14.1 — swap NPV differential

`OREFixedFloatSwapDifferential.compare` takes a verified Stage 11.5 `SwapPricingResult`. That result's independent reference already agreed with QuantLib. The comparison:

1. checks the result's identity and its binding to the instrument and curves;
2. has ORE price the same swap;
3. compares ORE's NPV with **both** the reference NPV and the QuantLib NPV, under a content-addressed `OREDifferentialPolicy` (absolute tolerance).

- A match records `trust_authority = REFERENCE_MATCH_ONLY`.
- A mismatch is kept as `MISMATCH` with `trust_authority = NONE`.
- Order and capital authority are `NONE` in both cases.

Pay-fixed and receive-fixed swaps, with ACT/365F or ACT/360 floating legs, match to far inside a 1e-6 tolerance on a 1,000,000 notional.

## 5. Stage 14.2 — scenario and risk-cube differential

`OREScenarioDifferential.compare` takes a Stage 11.6 `ScenarioRevaluationResult` and:

1. re-derives the shocked snapshot and market state;
2. rebuilds the base and shocked discount and forwarding curves under the frozen curve policies;
3. **proves** these are the exact curves the revaluation used, by matching content IDs;
4. has ORE price base and shocked states independently;
5. compares base NPV, shocked NPV and scenario P&L.

`cube()` aggregates the scenario cells. It is a MATCH only if every cell matches, and it reports the worst P&L difference.

## Not yet in scope

- Bonds, options and other products.
- ORE's own sensitivity and stress analytics, compared cell by cell with the Stage 11.7 cube.
- Historical VaR/ES, only where the semantics truly match.
- XVA, which comes only after the core mapping has been proven across more products.
- Each of these needs its own frozen overlap and differential tests.
