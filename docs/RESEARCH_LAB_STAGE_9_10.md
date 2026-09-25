# Stage 9.10 — Research Lab Service & Golden Workflow

Stage 9.10 turns the Stage 9 research modules into one application-facing Research Lab surface.

## ResearchLabService

The service facade exposes the audited domain engines without duplicating their logic:

- point-in-time universe reconstruction;
- factor scoring;
- immutable walk-forward validation;
- cost-aware economic backtesting;
- conventional performance diagnostics;
- DSR/PBO multiple-testing audit;
- Research Run Manifest construction;
- prospective expectation freeze;
- prospective comparison;
- Research Revision Seed creation.

The underlying immutable stores and lifecycle ledgers remain separate so persistence boundaries stay explicit.

## Dedicated CLI

The package now exposes `quantos-lab` in addition to the existing `quantos` and `quantos-radar` entrypoints.

`quantos-lab demo` runs a deterministic synthetic golden workflow. `--root` can be supplied to keep the generated DuckDB ledgers; otherwise a temporary workspace is used.

## Golden workflow

The end-to-end test exercises:

point-in-time universe → factor → validation → economic backtest → performance → DSR/PBO → PAPER-eligible manifest → RESEARCH/VALIDATED/BACKTESTED/PAPER registry gates → prospective PAPER observations → policy health → postmortem closure → scenario calibration/portfolio comparison → immutable revision seed.

The golden result must end with:

- registry stage: PAPER;
- pre-close PAPER health: MEASURED;
- post-close PAPER health: CLOSED;
- capital authority: NONE.

## Why this matters

Stage 9 is no longer a collection of standalone primitives. There is now one executable workflow proving that lineage survives across the entire quantitative research lifecycle and that the system still fails closed before live capital.

## Next phase

The next build phase can begin from this stable Research Lab foundation: portfolio optimization integration (skfolio), richer factor families, real point-in-time provider pipelines, and eventually NautilusTrader paper execution. Those integrations should consume the Research Run Manifest rather than bypass it.
