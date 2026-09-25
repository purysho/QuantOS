# Stage 9.7 — Research Run Manifest & Model Registry

Stage 9.7 binds the Quant Research Lab into one immutable lifecycle chain.

## Research Run Manifest

A ResearchRunManifest links the exact Research Case, experiment, factor specification, universe policy, dataset fingerprints, code revision, validation plan, economic backtest, backtest policy, performance analysis, performance policy, multiple-testing audit, overfitting policy, selected variant series, mandatory benchmark families, and prospective-shadow permit when present.

The builder validates every upstream relationship rather than accepting IDs independently.

Examples:
- experiment factor ID must equal the FactorSpecification ID;
- validation plan must belong to the exact experiment and factor;
- economic backtest must cite the exact validation plan;
- performance analysis must cite the exact backtest;
- multiple-testing audit must belong to the exact experiment;
- selected variant must be the audit-selected variant, must be present in the audit's complete registry, and must cite the exact selected backtest;
- a prospective-shadow permit must belong to the experiment's explicit Research Case.

Partial BACKTESTED chains fail closed.

## Experiment-to-case identity

ResearchExperimentSpecification now has an optional research_case_id. The model registry requires it. This closes the former gap between systematic experiment lineage and the professional Research Case / six-role review / prospective-shadow gate.

## Eligible stages

A manifest can be structurally eligible for:

- RESEARCH: Research Case + experiment + factor + datasets + code revision;
- VALIDATED: adds exact immutable walk-forward plan;
- BACKTESTED: adds economic backtest, performance analysis, complete DSR/PBO audit, and selected variant series;
- PAPER: adds exact PROSPECTIVE_SHADOW_ONLY permit for the same Research Case/dossier.

Eligibility does not automatically advance registry state.

## Lifecycle ledger

The prototype registry allows adjacent promotion:

RESEARCH → VALIDATED → BACKTESTED → PAPER

Transitions cannot skip stages and each carries actor, timestamp, reason, evidence references, exact manifest ID, and an immutable transition ID.

APPROVED and LIVE states exist in the vocabulary for the long-term architecture but are blocked by construction in this prototype.

## Stale-artifact reset

The stable model ID is derived from the Research Case plus experiment identity. If datasets, code, validation, backtest, selected variant, analysis, audit, or shadow permit change, the Research Run Manifest fingerprint changes.

Registering a new manifest for the same model resets the active registry state to RESEARCH and records MANIFEST_CHANGED_RESET. Prior PAPER state remains in history but has no authority over the changed manifest.

## Boundary

PAPER means the exact research configuration may be observed prospectively under the existing shadow-only firewall. It does not mean approved for capital, suitable for deployment, or expected to remain profitable.

## Next slice

Stage 9.8 should connect a PAPER registry state to prospective portfolio shadow observations and monitoring/postmortem controls, while keeping all live-capital states unavailable.
