# Stage 9.8 — Prospective PAPER Monitoring & Postmortem

Stage 9.8 turns PAPER into a real prospective observation state without creating any route to live capital.

## PAPER authority boundary

A PaperPortfolioObservation is accepted only when all of the following refer to the exact same model configuration:

- Research Run Manifest is PAPER-eligible;
- current Model Registry state is PAPER;
- registry manifest ID equals the supplied manifest ID;
- manifest is bound to the supplied PROSPECTIVE_SHADOW_ONLY permit;
- permit, manifest, and Research Case IDs agree;
- permit and manifest case-dossier fingerprints agree.

## Prospective observation

Every observation is recorded only after its holding period has ended and cannot begin before the shadow permit was issued.

It records gross return, reconciled net return, transaction cost rate, borrow cost rate, one-way turnover, all four mandatory benchmark returns, exact benchmark fact IDs, Research Case monitoring-condition checks, and evidence references.

Net return must equal gross return less the explicit transaction and borrow costs. Missing or inconsistent economics fail closed.

## Monitoring conditions

Every frozen Research Case monitoring condition must be checked exactly once for every prospective period. Conditions cannot disappear from the PAPER process merely because they are inconvenient.

Any breached Research Case condition makes the health state REVIEW_REQUIRED.

## Policy-bound health

PaperMonitoringPolicy explicitly records:

- minimum observation count;
- maximum allowed drawdown magnitude;
- maximum average one-way turnover;
- maximum average implementation-cost rate;
- minimum acceptable market-cap-relative wealth return;
- rationale and evidence references.

Health is INSUFFICIENT_EVIDENCE until the sample threshold is reached, MEASURED when sufficient observations remain inside all bounds, or REVIEW_REQUIRED when any quantitative threshold or frozen monitoring condition is breached.

These states are monitoring outputs, not capital decisions.

## Postmortem closure

A PAPER manifest can be closed with an evidence-backed postmortem reason such as completed evaluation, thesis invalidation, underperformance, risk breach, implementation cost, data quality, or model change required.

Once a postmortem exists, the exact manifest cannot accept further prospective observations. Its health becomes CLOSED. The Model Registry remains PAPER; closure does not silently promote or authorize anything.

## Next slice

Stage 9.9 should add prospective forecast/portfolio calibration linkage and research postmortem comparison: expected scenario probabilities, expected factor/portfolio behavior, realized outcomes, calibration error, and explicit lessons that feed a new Research Case revision rather than mutate the old manifest.
