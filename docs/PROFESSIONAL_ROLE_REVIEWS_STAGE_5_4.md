# Professional Role Reviews — Stage 5.4

Stage 5.4 adds durable reviews from six professional roles:

1. Fundamental Analyst
2. Quant Researcher
3. Portfolio Manager
4. Risk Officer
5. Execution Trader
6. Red Team

This is a structured disagreement system, not a majority vote.

## Role contracts

Each role has mandatory review dimensions.

### Fundamental Analyst
- economic interpretation
- accounting / measurement risk
- scope and alternative explanations

### Quant Researcher
- identification and statistics
- data leakage / multiple testing
- replication and regime stability

### Portfolio Manager
- portfolio interaction
- sizing / concentration assumptions
- diversification / correlation risk

### Risk Officer
- failure mechanisms
- tail / regime / liquidity risk
- model / data risk

### Execution Trader
- implementability
- costs / slippage / capacity
- latency / liquidity / market impact

### Red Team
- strongest counterargument
- hidden assumptions / falsifiers
- missing or conflicting evidence

Every required dimension needs explicit review notes.

## Dispositions

- `NO_OBJECTION`
- `CONDITIONAL`
- `BLOCKING_OBJECTION`
- `NOT_APPLICABLE`

A blocking disposition requires explicit objections.
A conditional disposition requires explicit follow-up work.

## Panel state

The panel is fail-visible:

1. any blocking review → `BLOCKING_OBJECTION_PRESENT`
2. otherwise, any missing role → `INCOMPLETE`
3. otherwise, any conditional review → `CONDITIONS_OPEN`
4. otherwise → `REVIEW_SET_COMPLETE`

`REVIEW_SET_COMPLETE` is deliberately **not** approval to trade or allocate
capital. It means only that all six professional review contracts are
represented for that exact evidence-dossier fingerprint.

If the dossier changes because new evidence or replication arrives, it gets a
new fingerprint and the old reviews do not silently transfer.
