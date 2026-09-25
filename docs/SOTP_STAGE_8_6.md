# Stage 8.6 — Method-Permitted Sum-of-the-Parts Valuation

Stage 8.6 adds SOTP while preserving the methodology gate at both company and segment level.

## Company-level gate

A SOTP can run only with a current company-level SOTP MethodPermit. The company methodology assessment must therefore establish that segment economics are sufficiently divergent and separable for SOTP to be appropriate or conditionally permitted.

## Segment-level methods

Every segment carries its own valuation methodology assessment and its own method permit. The SOTP engine does not assume one method fits every segment.

Examples supported by the architecture include an operating segment valued with FCFF DCF and a regulated bank segment valued with residual income. Nested SOTP is blocked.

## Enterprise-value vs equity-value segments

An enterprise-value segment must include an explicit SegmentBridge: cash plus non-operating investments less debt, preferred equity, and noncontrolling interest.

An equity-value segment must not include an enterprise bridge.

Ownership is applied only after the segment has been converted to equity value.

## Group reconciliation

Segment-level and corporate cash/debt allocations must reconcile to the reported group values before the SOTP can produce an equity value.

Specifically, segment EV-bridge cash plus corporate cash must equal reported group cash, and segment EV-bridge debt plus corporate debt must equal reported group debt, within the explicit engine tolerance.

This prevents hidden double counting or omission of group financing items.

## Corporate items

Corporate adjustments are explicit and separately evidenced: corporate cash, investments, debt, preferred equity, NCI, other assets, other liabilities, capitalized corporate-cost value, intercompany eliminations, and diluted shares.

Intercompany eliminations and corporate-cost values reduce group equity explicitly instead of disappearing inside segment values.

## Point-in-time integrity

Every segment valuation, corporate item set, and group reconciliation reference must use the same as-of timestamp.

## Output

The result preserves every segment method permit, valuation reference ID, value basis, ownership, attributable contribution, corporate adjustment, group equity value, diluted shares, and per-share value in a content-addressed SOTP identity.

SOTP output is valuation evidence only; it is not an investment recommendation and has no capital authority.

## Next slice

Stage 8.7 should add LBO analysis with evidence-backed entry/exit assumptions, sources & uses, explicit debt tranches, minimum-cash sweeps, MOIC/IRR, and downside cases. Valuation triangulation should follow.