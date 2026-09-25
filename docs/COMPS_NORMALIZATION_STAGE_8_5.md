# Stage 8.5 — Reviewed Peer Normalization & Sector Metric Contracts

Stage 8.5 makes two judgment-heavy parts of comparable-company analysis explicit: normalization and metric choice.

## Raw facts remain immutable

PeerSnapshot objects are never edited. Normalization produces a separate reviewed dataset whose identity is derived from the exact raw snapshot IDs and exact adjustment IDs.

Each adjustment records the raw peer snapshot, metric, signed amount, adjustment class, knowledge time, rationale, evidence references, and preparer.

Supported adjustment classes are non-recurring, accounting-policy, pro-forma, and other explicitly documented adjustments.

## Independent normalization review

A normalization dataset requires a reviewer, review time, and review notes. The reviewer cannot be the preparer of any included adjustment.

The review and every adjustment must be point-in-time valid for the valuation as-of date. Future-known adjustments fail closed.

An adjustment may reference only an included peer from the exact peer-selection result.

## Sector metric contracts

A SectorMetricContract declares the target industry and the exact multiple families that are economically admissible for that industry.

This prevents a generic comps engine from automatically applying every available multiple to every company type. For example, a reviewed banking contract can restrict analysis to equity-oriented metrics such as P/E and P/Book, while an operating software contract may permit EV/Revenue and EV/EBITDA.

The contract carries rationale and evidence references and is content-addressed.

## Strict normalized comps path

The normalized valuation path requires the exact peer selection, exact normalization dataset, exact target profile, exact sector metric contract, exact comps distribution policy, and a current TRADING_COMPS method permit.

The normalization dataset must cover exactly the included peer snapshots. A dataset from another peer selection cannot be reused.

## No hidden fair-value point

Normalization does not change the Stage 8.4 distribution rule. The system still preserves peer-level metrics and returns all configured percentile-based implied values rather than selecting a single fair multiple.

## Next slice

Stage 8.6 should add SOTP with segment-level method permits, intercompany/corporate-cost reconciliation, and explicit segment-to-equity bridges. LBO and valuation triangulation follow after SOTP.

Normalized comps remain valuation evidence only. They have no live-capital authority.
