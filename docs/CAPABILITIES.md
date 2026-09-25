# Capabilities

The full, engine-by-engine inventory of what First Current Quant OS implements. Each item is backed by tests, and each stage has its own document in this folder. The build history is in `HANDOFF.md`.


### Point-in-time foundation
- separate event time and knowledge time
- durable DuckDB event ledger and Parquet export
- as-of reconstruction without revision leakage
- deterministic identities, idempotence and conflict detection
- SEC submissions and primary-document capture
- FRED/ALFRED vintage ingestion

### Research intake and claim trust
- live arXiv metadata radar
- Crossref DOI journal radar (finance journals, mandatory contact etiquette, precision-preserving dates)
- SSRN working papers through Crossref (DOI prefix 10.2139, posted-date filter, mandatory query)
- frozen feed registry for NBER, Federal Reserve (FEDS, IFDP, press releases, speeches), SEC press releases, BIS (working papers, central-bank speeches) and ECB (working papers, press releases); undated items are stamped first-seen and never back-dated; DOCTYPE/entity XML refused
- immutable raw-feed provenance
- deterministic attention triage and review quarantine
- exact-source SHA-256 verification
- verified source required before claim drafting
- exact locator, scope, assumptions and limitations
- drafter/reviewer separation
- mandatory counter-evidence notes
- approval separate from promotion

### Evidence and professional reasoning
- SUPPORTS / LIMITS / CONTRADICTS / EXTENDS links
- replication records and fail-visible evidence dossiers
- no opaque truth score
- six durable professional roles:
  1. Fundamental Analyst
  2. Quant Researcher
  3. Portfolio Manager
  4. Risk Officer
  5. Execution Trader
  6. Red Team
- blocking objections cannot be outvoted
- immutable Research Cases, alternatives, falsifiers and scenario sets
- case reviews bound to exact dossier fingerprints
- prospective readiness permits remain `PROSPECTIVE_SHADOW_ONLY`
- forecast calibration with Brier score and log loss

### Fundamental Engine — v0.8.0 baseline
Reported financials and modeled projections are deliberately separate.

**Reported statements**
- balance sheet / income statement / cash-flow domain model
- filing accession, acceptance time and system knowledge time
- line-level artifact provenance and source locators
- point-in-time storage
- accounting identities and cross-statement tie-outs

**Linked projections**
- evidence-backed operating assumption sets
- revenue / margin / working-capital / PP&E schedules
- deterministic one-period and multi-period roll-forwards
- parent-projection lineage
- immutable model-run manifests
- projected lines explicitly marked `ESTIMATED`

**Advanced financing / tax / equity**
- debt by tranche
- fixed and floating rates
- maturities and mandatory repayment
- NOL generation/utilization
- minimum-cash debt sweeps
- share issuance/repurchase and dilution
- strict debt/NOL/share state continuity
- unfunded maturities that breach minimum cash fail closed

### Valuation Engine — v0.9.0 baseline

#### Methodology gate
A mathematically correct model cannot run merely because it exists.

Business profiles assess:
- FCFF DCF
- FCFE DCF
- dividend discount
- residual income
- trading comps
- transaction comps
- SOTP
- probability-weighted DCF
- NAV
- liquidation value
- replacement value
- LBO

Each method is `APPROPRIATE`, `CONDITIONAL`, or `INAPPROPRIATE`. Conditional methods require evidence-backed conditions before a content-addressed MethodPermit is issued.

Examples:
- banks/insurers can block enterprise FCFF when funding/regulatory capital are operating constraints
- pre-revenue biotech requires explicit binary-outcome treatment
- conglomerate SOTP requires economically divergent disclosed segments
- distress makes liquidation analysis explicit

#### FCFF DCF + reverse DCF
- exact validated model-run binding
- explicit per-period unlevered cash tax
- explicit discount timing
- evidence-backed WACC and terminal growth
- hard `terminal growth < WACC` gate
- explicit enterprise-to-equity bridge
- diluted per-share value
- terminal-value contribution visibility
- market-implied terminal-growth reverse DCF

#### DCF sensitivity
- explicit WACC × terminal-growth grids
- invalid perpetuity cells remain visible
- policy-bound terminal-value and dispersion diagnostics
- no composite confidence score

#### Comparable-company valuation
- deterministic point-in-time candidate universe
- explicit inclusion/exclusion reasons
- one entity cannot be counted twice through multiple snapshots
- future-known peer data excluded
- minimum-peer gating
- EV/Revenue, EV/EBITDA, EV/EBIT, P/E, P/Book and FCF-yield distributions
- peer-level values + min + configured percentiles + max
- percentile valuation ranges rather than one cherry-picked multiple

#### Reviewed peer normalization
- raw peer facts never mutate
- signed normalization adjustments are separate artifacts
- adjustment rationale, evidence, preparer and knowledge time are explicit
- preparer/reviewer separation
- future-known adjustments fail closed
- industry-specific metric contracts restrict economically admissible multiples

#### Sum-of-the-parts
- company-level SOTP permit
- independent method permit for every segment
- EV- and equity-basis segments
- ownership applied after EV-to-equity conversion
- corporate costs and intercompany eliminations explicit
- segment + corporate cash/debt must reconcile to reported group totals
- common as-of timestamp required

#### LBO
- exact operating-model-run binding
- separate sponsor acquisition financing
- sources & uses must balance
- evidence-backed debt tranches, rates, amortization and maturities
- sponsor-interest tax shield and NOL handling
- minimum-cash liquidity gate
- debt sweep by explicit priority
- exit multiple, MOIC and IRR
- non-positive exit equity preserved without fake IRR
- downside cases use separate model runs rather than hidden haircuts

#### Non-averaging triangulation
- DCF, trading comps and SOTP compared on a common per-share basis
- multiple comps metrics count as **one method family**, not independent votes
- common overlap / partial overlap / disjoint states
- cross-method dispersion remains visible
- no weighted synthetic fair value
- LBO remains a sponsor-return cross-check and is never averaged into per-share value

### Quant Research Lab — v0.9.x baseline

- point-in-time investable-universe construction;
- immutable factor specifications and feature observations;
- walk-forward validation;
- implementation-aware backtesting;
- benchmark baselines;
- performance analytics;
- multiple-testing / backtest-overfitting diagnostics;
- immutable Research Run Manifests;
- explicit model lifecycle registry;
- prospective PAPER monitoring and review controls.

### Portfolio Engine — v0.10.10 baseline

**Construction**
- EqualWeight and InverseVolatility mandatory baselines;
- frozen empirical / Ledoit-Wolf covariance artifacts;
- constrained minimum-variance MeanRisk;
- HRP and HERC hierarchical candidates;
- exact portfolio-level one-way turnover semantics;
- content-addressed solution lineage.

**Common OOS comparison**
- all candidate methods use identical frozen OOS periods;
- common implementation-cost accounting;
- realized volatility, drawdown, expected shortfall, turnover, concentration and market-relative wealth;
- no automatic winner.

**Robustness and human selection**
- adjacent-fold weight stability;
- clustering stability;
- covariance-estimator sensitivity;
- constraint and solver fragility;
- two-person human research decision with explicit trade-offs and objections.

**Shadow PAPER controls**
- finite shadow-only portfolio authorization;
- exact selected-solution binding;
- mandatory runtime kill conditions;
- irreversible suspension/termination/expiry for a given authorization;
- implementation-cost assumption variance;
- OOS-vs-shadow calibration;
- benchmark-relative prospective review;
- mandatory two-person postmortem;
- no promotion, order or capital authority.

### Pricing & Risk Engine — v0.11.10 baseline

**Pricing contracts and QuantLib boundary**
- point-in-time market snapshots with event/knowledge-time lineage;
- typed equity, bond, European option and fixed/float swap contracts;
- explicit model specifications and pricing requests;
- QuantLib isolated behind First Current contracts and a shared global-state lock;
- independent differential validation for Black-Scholes options, fixed-rate bonds and vanilla swaps;
- frozen engine/version and exact market-input lineage.

**Rates and derivatives**
- content-addressed zero-rate / discount curves;
- explicit interpolation, compounding, day-count and extrapolation policy;
- fixed-rate bond NPV / clean / dirty / accrued / DV01;
- separate discount and forwarding curves for future-starting fixed/float swaps;
- no inferred historical fixings or hidden benchmark conventions.

**Deterministic portfolio risk**
- content-addressed market shocks and derived shocked snapshots;
- scenario revaluation for equity, European options, bonds and swaps;
- deterministic curve rebuilding under frozen policies;
- explicit position × scenario coverage cube;
- missing coverage remains `INCOMPLETE` and suppresses partial portfolio totals;
- net/gross base NPV, NPV concentration and scenario P&L.

**Distributional risk and prospective calibration**
- equal-weight historical-simulation VaR / expected shortfall with explicit observation chronology;
- nearest-rank empirical quantile and explicit tail-count convention;
- prospective forecast-before-outcome backtesting;
- Kupiec unconditional-coverage diagnostics;
- Christoffersen exception-independence and combined conditional-coverage diagnostics;
- two-person risk review with explicit limitations and challenger objections;
- statistical non-rejection never becomes model approval;
- no regulatory classification is inferred.

### Simulation & Execution Engine — v0.12.10 baseline

**Execution contracts and reference oracle**
- canonical execution instruments separate from venue symbols;
- point-in-time top-of-book quotes and trade prints with event/knowledge time;
- content-addressed historical replay datasets, simulation policies and run manifests;
- strict simulated order state machine with immutable fills;
- deterministic First Current top-of-book reference fill engine with explicit order/market-data latency, participation, tick/lot rounding, slippage, impact, commission, partial-fill, IOC/FOK and horizon-expiry semantics.

**NautilusTrader historical differential**
- NautilusTrader 2.0.0rc5 `BacktestEngine` only, on Python 3.12+; no `LiveNode` or venue adapters;
- six frozen, content-addressed equivalence contracts, each adding exactly one behavior: zero friction, deterministic fees, order latency, market-data latency, IOC/FOK remainders, and non-marketable-to-marketable limit transitions;
- exact comparison of final state, fill count, quantity, VWAP, total fees and the per-fill time/quantity/price/fee sequence;
- observed reference/Nautilus divergences are recorded on each contract and refused rather than tolerated;
- a match grants only `REFERENCE_MATCH_ONLY`; no network, external-order or capital authority;
- a seventh contract runs one order per instrument across several instruments in one backtest and aggregates per-order differentials;
- Stage 12.10 resolves the recorded divergences by making each Nautilus behavior an explicit, non-default reference mode (next-quote activation, one-tick L1 market sweep, resting-limit fills at the limit price with per-level liquidity memory, half-even minor-unit fee rounding, IOC always-partial), each proven by its own frozen contract; twelve contracts in total, including a 120-trial randomized resting-limit differential.

**Execution research and review**
- multi-order execution schedules reconciled into inventory and cash; concurrent orders on one book fail closed; cash/short breaches preserved;
- replay data-quality gate (gaps, spreads, jumps, thin/stale books, sequence regressions, trades outside the quote) that reports and never repairs;
- exact implementation-shortfall TCA (timing, half-spread, slippage/impact, fees, opportunity) proven to reconcile in Decimal;
- two-person execution review whose best outcome only permits designing a separate shadow-execution authority plane.

### Security Master & corporate actions — v0.13.2 baseline
- bitemporal Company → Security → Listing hierarchy with ISIN/FIGI/CUSIP/SEDOL/CIK identifiers and check-digit validation;
- ticker resolution through symbol changes and symbol reuse; ambiguity raises, unknown returns nothing;
- corrections supersede by knowledge time and never leak backward;
- corporate-action economics (dividends, splits, spin-offs, mergers, delistings) with split-only or total-return adjustment factors, total-return series and position transformations; missing prior closes, counterparty prices, delisting proceeds and spin-off basis allocations are reported, never inferred.

### Exchange calendars and research return panels — v0.13.4 baseline
- frozen XNYS session calendar (2010–2030, early closes, unscheduled closures) matching `exchange_calendars` exactly over 5,279 sessions;
- point-in-time research return panels from raw session closes and the reviewed corporate-action ledger: split-only or total-return, close-to-close in UTC, incomplete histories excluded rather than filled.

### OpenSourceRisk/Engine differential — v0.14.6 baseline
- pinned `open-source-risk-engine==1.8.17.0` as an optional `ore` extra behind First Current contracts;
- ORE isolated in a child process (its SWIG bindings crash when sharing a process with QuantLib);
- content-addressed ORE input bundles generated from frozen swap and curve artifacts;
- swap NPV compared with the Stage 11.5 reference and QuantLib; Stage 11.6 scenario P&L compared cell by cell and across a cube;
- fixed-rate bonds against the Stage 11.4 reference, European equity options against a closed-form Black–Scholes–Merton reference, and ORE's own sensitivity and stress analytics against single-pillar First Current revaluations.

### Observability and security — v0.16 baseline
- run and span tracing, redacted JSON-line events, error taxonomy, metrics and `quantos ops-report`;
- secret provider (environment or private secrets directory), outbound egress allowlist enforced for every adapter, operator kill switch (`quantos kill-switch`), and a hash-chained audit log (`quantos audit-verify`).

### Market-data provider pipeline — v0.17 baseline
- Tiingo and Polygon end-of-day adapters (raw, unadjusted prices only), with keys from the secret provider sent in headers and exact responses archived;
- revision-preserving bitemporal bar store, calendar completeness, staleness, OHLC and intraday-capture checks;
- cross-provider close reconciliation and provider corporate-action cross-checks against the reviewed ledger; closes feed the research return panel.

### Perspective terminal — v0.18 baseline
- `quantos terminal export` writes an immutable, content-addressed, read-only export of every DuckDB store, mapped to the HANDOFF workspaces (Markets, Research Radar, Evidence, Financials, Valuation, Portfolio, Risk, Strategy Lab, PAPER, Execution, P&L/TCA, Audit);
- `quantos terminal serve` serves it on loopback only, GET only, with a strict CSP; Perspective 3.8.0 is pinned with SRI, and the browser verifies every table's SHA-256 before display.

### Reproducibility and licensing
- `uv.lock` universal lockfile plus hashed `requirements.lock`, enforced in CI;
- content-addressed environment manifest (`quantos env-manifest`) with lock-consistency and code-revision state;
- proprietary license with a dependency license gate over the whole lockfile.

## Intelligence, modeling and valuation chain

```text
external source
    ↓
immutable artifact
    ↓
verified source identity
    ↓
independently reviewed Claim Card
    ↓
typed evidence / replication graph
    ↓
Evidence Dossier
    ↓
Research Case + Scenario Set
    ↓
six professional reviews
    ↓
PROSPECTIVE_SHADOW_ONLY controls

primary financial sources
    ↓
point-in-time reported statements
    ↓
accounting validation
    ↓
explicit model assumptions
    ↓
linked operating / debt / tax / equity schedules
    ↓
balanced multi-period projected statements
    ↓
immutable model-run lineage
    ↓
valuation methodology assessment
    ↓
method-specific permit
    ↓
DCF / comps / SOTP / LBO
    ↓
non-averaging triangulation

portfolio research
    ↓
frozen construction candidates
    ↓
common OOS comparison
    ↓
robustness + human method review
    ↓
shadow-only PAPER controls
    ↓
point-in-time pricing / curves
    ↓
deterministic scenario revaluation
    ↓
portfolio risk cube
    ↓
historical VaR / ES
    ↓
prospective calibration + risk review
    ↓
historical replay + reference fill oracle
    ↓
frozen-contract Nautilus differential
    ↓
schedules + replay quality + TCA + two-person execution review

    ╳
no automatic live-capital path
    ╳
```

## Safety semantics (what labels do and do not mean)

- `VERIFIED` source = source identity checked, not conclusions proven.
- `APPROVED` claim = scoped claim passed review, not universal truth.
- `REPLICATION_SUPPORTED` = evidence structure, not a probability.
- `READY_FOR_PROSPECTIVE_SHADOW` = permission to measure prospectively, not permission to trade.
- `ESTIMATED` financial line = model output, not a filed fact.
- balanced statements = accounting consistency, not forecast accuracy.
- MethodPermit = permission to use a valuation framework for the stated profile, not proof the output is correct.
- DCF/comps/SOTP/LBO output = valuation evidence, not an investment recommendation.
- triangulation = preserved agreement/disagreement, not a synthetic fair value.
- `MEASURED` = sample exists, not profitable.
- historical-simulation VaR / ES = backward-looking empirical loss summaries, not guarantees or capital requirements.
- `WITHIN_TEST_TOLERANCE` = a statistical null was not rejected under the frozen test, not model approval.
- `WITHIN_POLICY` risk review = supplied evidence did not breach frozen research rules, not LIVE authority.
- `REFERENCE_MATCH_ONLY` = Nautilus reproduced one frozen historical fixture under one named contract, not live execution validation.
- `NO_TRADE`, `UNKNOWN`, `QUARANTINED`, `INCOMPLETE`, and `INSUFFICIENT_EVIDENCE` are valid outcomes.

See `docs/STAGE_7_COMPLETE.md` for the Fundamental Engine baseline, `docs/STAGE_8_COMPLETE.md` for the Valuation Engine baseline, `docs/STAGE_10_COMPLETE.md` for the Portfolio Engine and shadow-PAPER control chain, `docs/STAGE_11_COMPLETE.md` for Pricing & Risk, and `docs/NAUTILUS_EQUIVALENCE_CONTRACTS_STAGE_12_4.md` for the current execution differential baseline.
