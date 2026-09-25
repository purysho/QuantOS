# Stage 12.5 — Multi-Order Execution Schedules

Stage 12.5 moves execution simulation from one order to a schedule: several simulated orders across instruments, reconciled into inventory and cash.

## Inputs

- `ExecutionScheduleTarget` gives an explicit starting and target position for one instrument, keyed by `source_target_id`. A target must change the position, and one instrument may have only one target.
- `ExecutionSchedulePolicy` freezes:
  - the cash currency;
  - starting cash;
  - whether short positions are allowed;
  - whether negative cash is allowed;
  - rationale and evidence.
- `ExecutionScheduleBuilder` binds a historical-replay run, the policy, the targets and the order intents into a content-addressed `ExecutionSchedule`.

## Builder gates (fail closed)

- Every intent belongs to the run and maps to exactly one target on the same instrument.
- An intent's side must move toward its target.
- Child-order quantities must sum **exactly** to each target's required change. There is no silent over- or under-allocation.
- Every instrument must be quoted in the cash currency. There is no implicit FX.
- Short targets require `allow_short_positions`.

## Engine

`ExecutionScheduleEngine` runs intents in submission order through the unchanged Stage 12.2 `FirstCurrentReferenceFillEngine`.

The reference model has no shared-liquidity semantics between orders. A later order on an instrument therefore may not be submitted before the earlier order on that instrument stopped working. Stop time is:

- the last fill, if filled;
- activation, for IOC/FOK expiry;
- the replay horizon, for DAY/GTC expiry;
- submission, if rejected.

Overlapping working orders fail closed instead of double-counting displayed liquidity. Sequential child orders on one instrument are allowed.

Fills are then replayed in fill-time order to rebuild positions and cash, including fees and the contract multiplier.

## Result states

- `COMPLETE` — every target reached with no policy breach.
- `INCOMPLETE` — some target has unfilled quantity. Each shortfall is listed.
- `CONSTRAINT_BREACH` — cash went negative or a position went short against policy. Breaches are listed per fill and never hidden. Fills already simulated cannot be undone, so the breach is preserved as evidence.

`ExecutionScheduleResult` records:

- the result IDs of each order and each fill;
- the outcome for every target;
- starting, final and minimum cash;
- notional bought and sold;
- total fees.

It keeps `simulation_authority = HISTORICAL_REPLAY_ONLY`, with no external-order or capital authority.

## Not yet in scope

- Sizing share targets from portfolio weights. That needs a frozen capital, price and lot-rounding policy.
- Cancel/replace.
- Session calendars.
- Shared-liquidity queue semantics for concurrent orders on one instrument.
