# Case-Level Professional Reviews — Stage 6.4

Stage 6.4 moves the six professional review contracts from single claims to
whole Research Cases.

Every review binds to one exact `case-dossier:` fingerprint:

```text
Research Case
    +
Scenario Set
    +
current Evidence Dossiers for every cited claim
    ↓
Case Dossier fingerprint
    ↓
six independent professional case reviews
```

If the scenario set changes, or new contradiction/replication evidence changes
a cited Claim Dossier, the Case Dossier fingerprint changes. Prior reviews stay
auditable but do not transfer to the new evidence state.

## Mandatory case checks

### Fundamental Analyst
- mechanism and economics
- accounting / measurement quality
- valuation or business-model fit

### Quant Researcher
- identification and statistics
- data leakage / multiple testing
- replication and regime stability

### Portfolio Manager
- scenario asymmetry
- portfolio interaction / concentration
- sizing / diversification assumptions

### Risk Officer
- failure mechanisms
- tail / regime / liquidity risk
- model / data / scenario coverage

### Execution Trader
- implementability
- costs / slippage / capacity
- latency / liquidity / market impact

### Red Team
- strongest alternative explanation
- falsifiers / disconfirming evidence
- missing or conflicting evidence

## No voting away objections

Panel state is fail-visible:

1. any unresolved blocking objection → `BLOCKING_OBJECTION_PRESENT`
2. otherwise any missing role → `INCOMPLETE`
3. otherwise any unresolved condition → `CONDITIONS_OPEN`
4. otherwise → `REVIEW_SET_COMPLETE`

Blocking and conditional reviews are immutable. Only the reviewer who raised
the issue can resolve it, and resolution requires evidence references.

`REVIEW_SET_COMPLETE` is still not a recommendation, position-size decision,
trade authorization, or permission to allocate capital.
