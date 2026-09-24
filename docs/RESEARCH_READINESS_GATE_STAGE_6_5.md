# Research Readiness Gate — Stage 6.5

Stage 6.5 creates the first whole-case readiness gate.

It can authorize **prospective shadow evaluation only**.

It cannot authorize real capital.

## Input

The gate consumes:
- one exact Case Dossier;
- the current Case Review Panel for that same fingerprint.

## Deterministic mapping

```text
case panel BLOCKING_OBJECTION_PRESENT
    → BLOCKED

case panel INCOMPLETE
    → INCOMPLETE

case panel CONDITIONS_OPEN
    → CONDITIONS_OPEN

case panel REVIEW_SET_COMPLETE
    → READY_FOR_PROSPECTIVE_SHADOW
```

No expected-return threshold, truth score, majority vote, or subjective
"conviction" number is used by this gate.

## Prospective Shadow Permit

A permit may be issued only from
`READY_FOR_PROSPECTIVE_SHADOW`.

The permit binds:
- Research Case ID;
- exact Case Dossier fingerprint;
- exact Scenario Set ID;
- issuer;
- issue timestamp;
- purpose = `PROSPECTIVE_SHADOW_ONLY`.

If evidence or scenarios change, the Case Dossier fingerprint changes and the
old permit no longer applies.

## Capital firewall

The live-capital firewall is unchanged and still blocks every live order,
including when a valid shadow permit exists.

A shadow permit is research-governance metadata, not brokerage authority.
