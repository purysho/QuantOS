# First Current Quant OS — Handoff

> Comprehensive implementation handoff for the First Current Quant OS prototype.
>
> This document is intended to let a new ChatGPT/Codex/engineer session continue the project without reconstructing the architecture from chat history.
>
> **Repository:** purysho/First-Current-Quant-OS-prototype  
> **Repository visibility:** private  
> **Current package version:** 0.12.3  
> **Implementation baseline reviewed for this handoff:** b262fcf1f18787fd5f4661de2ed3c5c3510ad49b  
> **Baseline commit message:** Restore complete Nautilus differential module after rc5 patch

---

# 1. Project mission

First Current Quant OS is intended to become a professional-grade investment research, valuation, quantitative research, portfolio, risk, and execution operating system.

The target is not “AI picks stocks.”

The target is:

> A professional investment operating system that forces every research idea through evidence, accounting, valuation, statistics, portfolio construction, risk, execution, adversarial review, prospective measurement, and human accountability before any capital could ever be exposed.

The system should be capable of becoming unusually fast and forward-looking, but it must never achieve speed by weakening provenance, chronology, model validation, review, or authority boundaries.

The central doctrine remains:

> **Research may be aggressive; evidence, modeling, valuation, trust and capital authority must remain conservative and explicit.**

The OS should help a professional reason faster and more completely. It must not disguise an opaque score, model output, or LLM answer as professional judgment.

---

# 2. Non-negotiable engineering principles

These rules should be treated as architecture, not style preferences.

## 2.1 Point-in-time truth

The system must distinguish:

- event/economic time;
- provider publication/acceptance time;
- system knowledge time;
- valuation/decision time.

No future-known information may leak into an earlier research state.

For fundamental observations, the conceptual point-in-time schema remains:

~~~
company / security
metric
period_start
period_end
filing_date
accepted_at
available_to_market_at
source
filing/accession_id
revision
currency
units
~~~

Any future provider or model must preserve equivalent semantics.

## 2.2 Content-addressed identity

Important artifacts are immutable and receive deterministic identities derived from their content.

The codebase repeatedly uses this pattern for:

- raw evidence;
- claims;
- evidence dossiers;
- research cases;
- scenarios;
- financial statements;
- forecasts;
- valuation permits and results;
- research manifests;
- portfolio datasets and solutions;
- covariance artifacts;
- risk cubes;
- VaR estimates;
- execution datasets;
- simulation runs;
- simulated orders/fills;
- differential results.

Changing economically meaningful content should change identity.

Nested identity must also be checked. Do not trust only the outer ID if nested artifacts can be mutated.

## 2.3 Fail closed

Invalid or incomplete state should produce an explicit failure or state such as:

- UNKNOWN;
- QUARANTINED;
- INCOMPLETE;
- INSUFFICIENT_EVIDENCE;
- BLOCKED;
- REVIEW_REQUIRED;
- NO_TRADE.

Do not silently:

- impute missing evidence;
- drop failed cases;
- substitute another model;
- fall back to a different optimizer;
- infer a fixing;
- infer a benchmark convention;
- extrapolate a curve without policy permission;
- fill unavailable liquidity;
- promote partial risk results to complete totals.

## 2.4 Separate engines and contracts

Research, valuation, portfolio construction, risk, and execution are separate domains.

External libraries sit behind First Current contracts.

They must not become the operating system’s source of truth.

Current examples:

- skfolio behind First Current portfolio contracts;
- QuantLib behind First Current pricing/risk contracts;
- NautilusTrader behind First Current simulation/execution contracts;
- future OpenSourceRisk/Engine must follow the same rule.

## 2.5 Independent differential validation

Whenever an external engine is introduced, First Current should own a simpler independently specified reference implementation for the overlapping domain.

Existing pattern:

- closed-form Black-Scholes reference vs QuantLib;
- independent bond cash-flow reference vs QuantLib;
- independent swap reference vs QuantLib;
- First Current deterministic top-of-book fill engine vs NautilusTrader.

A differential match proves only the frozen overlap being tested.

It does not imply global engine correctness.

## 2.6 Human disagreement is structural

The reasoning layer intentionally separates professional roles:

1. Fundamental Analyst
2. Quant Researcher
3. Portfolio Manager
4. Risk Officer
5. Execution Trader
6. Red Team / Skeptical Reviewer

One blocking objection cannot be erased by majority vote.

Where method selection occurs, it is explicitly recorded as a human decision rather than hidden inside a composite score.

## 2.7 Authority must be explicit

Artifacts should expose fields such as:

- selection_authority;
- paper_authority;
- trust_authority;
- var_authority;
- approval_authority;
- live_authority;
- network_authority;
- external_order_authority;
- order_authority;
- capital_authority.

The normal value is NONE unless a narrowly defined research/shadow authority is intentionally granted.

Current OS invariants do **not** provide a live-capital path.

---

# 3. Current high-level architecture

~~~
LIVE / EXTERNAL INFORMATION
│
├── SEC / primary filings
├── FRED / ALFRED
├── arXiv Research Radar
├── market / benchmark observations
└── future provider adapters
        │
        ▼
POINT-IN-TIME DATA + PROVENANCE
│
├── immutable raw artifacts
├── event vs knowledge time
├── deterministic IDs
├── lineage fingerprints
└── as-of reconstruction
        │
        ▼
RESEARCH TRUST LAYER
│
├── source verification
├── Claim Workbench
├── exact locators
├── scope / assumptions / limitations
├── counter-evidence review
├── SUPPORTS / LIMITS / CONTRADICTS / EXTENDS
├── replication records
└── Evidence Dossiers
        │
        ▼
PROFESSIONAL REASONING LAYER
│
├── Research Cases
├── alternatives
├── falsifiers
├── monitoring conditions
├── scenario sets
├── six professional role reviews
├── objection resolution
├── readiness gate
└── forecast calibration
        │
        ├─────────────────────────────────────┐
        ▼                                     ▼
FUNDAMENTAL ENGINE                    QUANT RESEARCH LAB
│                                     │
├── reported statements               ├── point-in-time universe
├── accounting invariants             ├── factor contracts
├── operating schedules               ├── walk-forward validation
├── debt / tax / equity               ├── cost-aware backtests
├── linked projections                ├── performance analytics
└── immutable model runs              ├── PBO / DSR
        │                              ├── Research Run Manifest
        ▼                              └── model lifecycle
VALUATION ENGINE                              │
│                                             ▼
├── methodology gate                  PORTFOLIO ENGINE
├── DCF / reverse DCF                 │
├── comps                             ├── EqualWeight / InverseVol
├── SOTP                              ├── covariance artifacts
├── LBO                               ├── minimum variance
├── sensitivity                       ├── HRP / HERC
└── non-averaging triangulation       ├── common OOS comparison
                                      ├── robustness
                                      ├── human method decision
                                      └── shadow PAPER controls
                                              │
                                              ▼
                                      PRICING & RISK ENGINE
                                              │
                                      ├── point-in-time market snapshots
                                      ├── QuantLib adapter
                                      ├── curves
                                      ├── options / bonds / swaps
                                      ├── deterministic stress
                                      ├── risk cube
                                      ├── historical VaR / ES
                                      └── prospective calibration
                                              │
                                              ▼
                                      SIMULATION & EXECUTION
                                              │
                                      ├── execution contracts
                                      ├── reference fill engine
                                      └── Nautilus differential adapter
                                              │
                                              ▼
                                      FUTURE TERMINAL / OPERATIONS
                                      Perspective + review surfaces
                                              │
                                              ╳
                                    no automatic live-capital path
~~~

---

# 4. Storage and runtime architecture currently in the repo

## Current implemented stack

- Python 3.11+ control and domain plane.
- DuckDB for durable prototype stores.
- NumPy for numerical work.
- CVXPY for constrained portfolio optimization support.
- skfolio for portfolio estimators/optimizers.
- QuantLib for pricing.
- NautilusTrader 2.0.0rc5 on Python 3.12+ for experimental historical differential execution.
- requests/pytz for external intake and time support.
- Parquet export exists in the earlier point-in-time data layer.

Current pyproject dependencies at v0.12.3:

~~~
cvxpy >=1.6,<2
duckdb >=1.4,<2
numpy >=2,<3
nautilus_trader ==2.0.0rc5 ; python_version >= 3.12
QuantLib >=1.43,<2
pytz >=2025.2
requests >=2.32,<3
skfolio >=1.0,<2
~~~

## Important architecture ideas researched but not yet fully implemented

The original target architecture also considered:

- Polars for fast transformation;
- Apache Arrow as interchange;
- PostgreSQL for application metadata/state at larger scale;
- Pandera for dataframe contracts;
- exchange_calendars for exchange/session semantics;
- EdgarTools for richer SEC/XBRL ingestion;
- DuckDB + Parquet as the analytical/PIT foundation;
- a full Security Master;
- OpenSourceRisk/Engine;
- Perspective terminal.

Do not assume those items are implemented just because they were part of the design research.

---

# 5. What has been built

## Foundation before Stage 3

The early prototype established:

- durable timestamped event storage;
- separate knowledge/economic time;
- deterministic IDs;
- DuckDB persistence;
- Parquet export;
- point-in-time reconstruction;
- SEC submissions and filing artifacts;
- FRED/ALFRED vintage-aware ingestion;
- lineage and artifact stores;
- hard capital firewall.

The source tree still contains the relevant foundation modules, including:

- ledger.py
- persistent.py
- lineage.py
- ingestion.py
- artifacts.py
- store.py
- models.py
- adapters/

## Stage 3 — provenance-first edge discovery

Completed.

Key properties:

- revision-safe point-in-time data;
- raw evidence SHA-256 identity;
- bounded graph traversal;
- sourced graph edges;
- benchmark-adjusted reaction windows;
- shadow edge measurement;
- stale/incomplete windows fail closed;
- no causal inference from graph path alone;
- no live-capital authority.

Reference: docs/STAGE_3_COMPLETE.md

## Stage 4 — Research Radar

Completed.

Implemented:

- live arXiv metadata intake;
- immutable raw feed artifact;
- deterministic attention triage;
- review queue;
- quarantine;
- exact-source artifact verification;
- reviewer workflow;
- no promotion from discovery directly into trusted claims.

Important semantic rule:

> A new paper is a discovery item, not knowledge.

Reference docs include:

- RESEARCH_RADAR_V0_4_1.md
- RESEARCH_RADAR_TRIAGE_V0_4_2.md
- RESEARCH_REVIEW_QUARANTINE_V0_4_5.md
- RESEARCH_VERIFICATION_WORKFLOW_V0_4_6.md
- STAGE_4_COMPLETE.md

## Stage 5 — claim trust and professional reasoning controls

Completed.

Built:

- Claim Workbench;
- exact verified artifact pinning;
- exact locators;
- scope/assumptions/limitations;
- independent drafter/reviewer;
- counter-evidence notes;
- separate approval vs promotion;
- typed evidence links:
  - SUPPORTS
  - LIMITS
  - CONTRADICTS
  - EXTENDS
- replication records:
  - DIRECT
  - CONCEPTUAL
  - REANALYSIS
  - REPLICATES
  - PARTIAL
  - FAILS_TO_REPLICATE
  - INCONCLUSIVE
- Evidence Dossier;
- six professional roles;
- immutable blocking objections;
- objection resolution without deleting original findings.

No opaque truth/confidence score is used.

Reference: docs/STAGE_5_COMPLETE.md

## Stage 6 — whole-case reasoning

Completed.

Built:

- immutable Research Case;
- thesis and mechanism;
- explicit alternative explanations;
- falsifiers;
- monitoring conditions;
- as-of time;
- scenario sets with probability bands;
- Case Dossier fingerprinting;
- six case-level professional reviews;
- Research Readiness Gate;
- shadow-only permit;
- forecast freeze;
- outcome adjudication after horizon;
- Brier score and log loss;
- calibration remains insufficient until minimum evidence exists.

Reference: docs/STAGE_6_COMPLETE.md

## Stage 7 — Fundamental Engine

Completed.

Built:

### Reported statements

- balance sheet;
- income statement;
- cash-flow statement;
- filing/accession provenance;
- point-in-time acceptance/knowledge time;
- line-level locators;
- deterministic statement identity.

### Accounting gates

- Assets = Liabilities + Equity;
- cash-flow reconciliation;
- cash roll-forward;
- cross-statement tie-outs;
- entity/currency/period consistency.

### Linked forecasts

- revenue;
- gross margin;
- operating expenses;
- working capital;
- PP&E/depreciation;
- debt/interest;
- tax;
- retained earnings;
- cash flow.

Projected lines are explicitly ESTIMATED.

### Advanced schedules

- debt tranches;
- fixed/floating rates;
- maturity and repayment;
- NOLs;
- cash sweep;
- minimum cash;
- share issuance/repurchase;
- options;
- treasury stock method;
- restricted units;
- diluted shares.

State continuity is enforced across forecast periods.

Reference: docs/STAGE_7_COMPLETE.md

## Stage 8 — Valuation Engine

Completed.

Built:

### DCF / reverse DCF

- FCFF;
- WACC;
- terminal growth;
- enterprise-to-equity bridge;
- diluted per-share value;
- terminal-value contribution visibility;
- reverse DCF.

### Methodology gate

Valuation methods are assessed as:

- APPROPRIATE;
- CONDITIONAL;
- INAPPROPRIATE.

A MethodPermit is required downstream.

Business-type logic already covers examples such as:

- bank/insurer;
- pre-revenue biotech;
- conglomerate;
- distress.

### Sensitivity

- WACC × terminal growth;
- invalid cells remain visible;
- thresholds are policy-bound.

### Comparable companies

- point-in-time candidate universe;
- explicit inclusion/exclusion;
- no duplicate entities;
- minimum-peer rules;
- multiple distributions;
- reviewed normalization adjustments;
- sector metric contracts.

### SOTP

- separate segment permits;
- EV and equity basis support;
- ownership;
- corporate costs;
- intercompany eliminations;
- reconciliation to group cash/debt.

### LBO

- sources & uses;
- sponsor acquisition financing;
- tranches;
- interest;
- amortization;
- NOL;
- minimum cash;
- debt sweep;
- exit equity;
- MOIC;
- IRR;
- downside via separate model run.

### Triangulation

No weighted synthetic “fair value.”

DCF, comps, and SOTP agreement/disagreement remains visible.

LBO remains a sponsor-return cross-check.

Reference: docs/STAGE_8_COMPLETE.md

## Stage 9 — Quant Research Lab

Completed.

Built:

- point-in-time investable universe;
- stable security identity behavior for ticker changes;
- factor contracts;
- immutable feature observations;
- purged/embargoed walk-forward validation;
- cost-aware economic backtesting;
- benchmark baselines;
- performance analysis;
- multiple-testing audit;
- PBO / Deflated Sharpe controls;
- immutable Research Run Manifest;
- Model Registry lifecycle;
- prospective expectation freeze;
- PAPER observations;
- postmortem;
- ResearchLabService facade;
- quantos-lab CLI;
- deterministic golden E2E workflow.

Research model lifecycle concept:

~~~
IDEA
  ↓
RESEARCH
  ↓
VALIDATED
  ↓
BACKTESTED
  ↓
PAPER
  ↓
future gates only
~~~

Stage 9 does not create live authority.

Reference: docs/RESEARCH_LAB_STAGE_9_10.md and related Stage 9 docs.

## Stage 10 — Portfolio Engine

Completed and documented as v0.10.10.

### 10.1 Baselines

- point-in-time return datasets;
- EqualWeight;
- InverseVolatility;
- explicit constraints;
- independent constraint validation;
- immutable portfolio solutions.

### 10.2 Covariance

- Empirical;
- Ledoit-Wolf;
- exact dataset binding;
- symmetry;
- PSD;
- condition number;
- optional repair policy;
- immutable covariance artifact.

### 10.3 Minimum variance

- skfolio MeanRisk;
- fully invested / long only initial scope;
- exact frozen covariance consumed;
- solver status;
- no silent fallback;
- mandatory baseline lineage;
- explicit First Current portfolio-level one-way turnover.

### 10.4 Common OOS comparison

- identical OOS folds for every candidate;
- common implementation-cost policy;
- realized return/risk/concentration metrics;
- no automatic winner.

### 10.5 HRP / HERC

- Pearson distance;
- Ward linkage;
- frozen covariance;
- cluster fingerprints;
- same OOS comparison path.

### 10.6 Robustness

- adjacent-fold weight instability;
- cluster instability;
- covariance-estimator sensitivity;
- constraint fragility;
- solver fragility.

### 10.7 Human method decision

- reviewer + independent challenger;
- every method explicitly assessed;
- trade-offs and objections;
- human selection only;
- recommendation only for later PAPER review.

### 10.8–10.10 Shadow PAPER portfolio controls

- finite authorization;
- exact selected solution;
- cost assumptions;
- mandatory kill set;
- ACTIVE/SUSPENDED/TERMINATED/EXPIRED/CLOSED enforcement;
- no resume of the same authorization after terminal/suspended state;
- shadow vs OOS review;
- cost-model error;
- drift;
- benchmark-relative evidence;
- mandatory two-person postmortem;
- can recommend another RESEARCH iteration only.

Reference: docs/STAGE_10_COMPLETE.md

## Stage 11 — Pricing & Risk Engine

Completed and documented as v0.11.10.

### 11.1 Contracts

- point-in-time MarketQuote;
- MarketDataSnapshot;
- typed equity, bond, European option, and fixed/float swap contracts;
- PricingModelSpecification;
- PricingRequest;
- RiskScenario;
- content-addressed identities.

### 11.2 QuantLib adapter

- equity spot mark-to-market;
- analytic European Black-Scholes-Merton;
- explicit quote mapping;
- no implicit FX;
- unsupported measures fail closed;
- version provenance;
- global evaluation date lock and restoration;
- independent closed-form differential tests.

### 11.3 Curves

- point-in-time zero-rate inputs;
- ACT/365F baseline;
- continuous compounding;
- log-linear discount interpolation;
- negative-rate support;
- forward sanity checks;
- QuantLib pillar cross-check;
- explicit extrapolation only.

### 11.4 Bonds

- regular no-stub fixed-rate bonds;
- reference cash flows;
- QuantLib differential;
- NPV;
- clean/dirty price;
- accrued;
- DV01.

### 11.5 Swaps

- separate discount/forward curves;
- explicit schedules/day counts;
- future-starting USD vanilla swaps;
- no inferred historical fixings;
- independent reference;
- QuantLib differential;
- fixed-leg DV01;
- schedule fingerprints.

### 11.6 Deterministic scenario revaluation

- exact shock matching;
- absolute/relative shock semantics;
- shocked point-in-time snapshots;
- curve rebuild;
- equity/options/bonds/swaps;
- P&L from shocked minus base NPV.

### 11.7 Portfolio risk cube

- signed positions × scenarios;
- incomplete coverage remains INCOMPLETE;
- no partial total;
- net/gross NPV;
- concentration;
- scenario P&L;
- largest contributors.

### 11.8 Historical simulation VaR / ES

- COMPLETE cube required;
- equal-weight empirical historical simulation;
- structural minimum sample;
- nearest-rank VaR;
- explicit ES tail count;
- no hidden scaling/parametric extrapolation;
- RESEARCH_ONLY authority.

### 11.9 Prospective VaR calibration

- forecast-before-outcome;
- realized P&L bound to frozen estimate;
- exceptions;
- Kupiec unconditional coverage;
- insufficient histories remain insufficient;
- no regulatory traffic-light inference.

### 11.10 Exception independence + risk review

- Christoffersen independence;
- conditional coverage;
- gaps do not create fake transitions;
- two-person risk review;
- deterministic stress + concentration + VaR/ES + prospective calibration;
- no approval/live/order/capital authority.

Reference: docs/STAGE_11_COMPLETE.md

## Stage 12 — Simulation & Execution Engine

### 12.1 Execution contracts — built

- canonical execution instrument;
- venue/symbol separate from canonical identity;
- tick/lot constraints;
- top-of-book quote and trade-print events;
- event/knowledge time;
- deterministic historical replay dataset;
- source-lineage fingerprint;
- execution simulation policy;
- historical-only run manifest;
- MARKET/LIMIT intents;
- DAY/GTC/IOC/FOK;
- immutable simulated fill;
- strict order state machine;
- no network/external order/capital authority.

Reference: docs/EXECUTION_CONTRACTS_STAGE_12_1.md

### 12.2 Deterministic First Current reference fill engine — built

- top-of-book only;
- explicit order activation time;
- explicit market-data availability time;
- no future data;
- marketable-limit semantics;
- deterministic participation;
- deterministic tick/lot rounding;
- deterministic slippage + impact;
- commission;
- partial-fill policy;
- IOC/FOK behavior;
- dataset-boundary expiry;
- immutable fill/state persistence.

This is the execution differential oracle.

Reference: docs/REFERENCE_EXECUTION_STAGE_12_2.md

### 12.3 NautilusTrader historical differential adapter — implemented but NOT currently green

The intended Stage 12.3 scope is deliberately narrow:

- NautilusTrader 2.0.0rc5;
- Python 3.12+ only;
- BacktestEngine only;
- no LiveNode;
- simulated in-memory venue;
- L1_MBP QuoteTick data;
- no trade-based hidden liquidity;
- zero market/order latency;
- zero fees/slippage/impact;
- participation = 1;
- no partial fills;
- one exact top-of-book quote at submission knowledge time;
- enough displayed contra liquidity for full order;
- already-marketable limits;
- exact comparison of final state, fill count, filled quantity, and VWAP;
- trust authority only REFERENCE_MATCH_ONLY on an exact match.

Reference: docs/NAUTILUS_DIFFERENTIAL_STAGE_12_3.md

---

# 6. Current repository state — IMPORTANT

## Current implementation head before this HANDOFF document

**Commit:** b262fcf1f18787fd5f4661de2ed3c5c3510ad49b  
**Message:** Restore complete Nautilus differential module after rc5 patch

Current pyproject version:

**0.12.3**

## Current CI status

Stage 12.3 is now **green and complete at its deliberately narrow differential scope**.

The integration had two intermediate failures worth remembering:

1. the initial NautilusTrader 2.0.0rc5 runtime rejected an obsolete MakerTakerFeeModel constructor mapping;
2. the first compatibility edit then introduced a syntax error while reconstructing execution_nautilus.py.

The repair commit:

**b262fcf1f18787fd5f4661de2ed3c5c3510ad49b — Restore complete Nautilus differential module after rc5 patch**

completed successfully across the full CI matrix:

- Python 3.11 — green;
- Python 3.12 — green;
- Python 3.13 — green;
- unit tests — green;
- fail-closed demo — green;
- edge-discovery demo — green.

This means the Stage 12.3 zero-friction differential adapter is again validated under the current pinned dependency set.

The later HANDOFF.md commit is documentation-only; if its own workflow is still queued/running, it should not be confused with the implementation gate above.

---

# 7. Immediate restart checklist

Do these in order.

## 1. Confirm current main and CI

Start by reading:

- HANDOFF.md;
- docs/NAUTILUS_DIFFERENTIAL_STAGE_12_3.md;
- src/quantos/execution_nautilus.py;
- tests/test_execution_nautilus.py.

Confirm that the latest implementation ancestor at or after b262fcf remains green before adding scope.

## 2. Begin Stage 12.4 only through frozen equivalence contracts

The documented next slice is controlled differential expansion.

Add one behavior at a time:

1. explicit order latency;
2. explicit market-data latency;
3. deterministic fees;
4. controlled partial-liquidity / partial-fill cases.

Do not broaden the Stage 12.3 claim implicitly.

## 3. Preserve the Python compatibility boundary

- Python 3.11 remains a supported First Current runtime without NautilusTrader v2;
- NautilusTrader 2.0.0rc5 is installed only on Python 3.12+;
- generic imports and tests must remain safe on 3.11;
- 3.12/3.13 must execute the actual Nautilus differential runtime tests.

## 4. Preserve the exact differential oracle

For every new overlap fixture compare the same economic order through:

- FIRST_CURRENT_REFERENCE;
- NAUTILUS_TRADER.

At minimum preserve comparison of:

- final state;
- fill count;
- filled quantity;
- VWAP;
- later, any newly frozen fee/latency/partial-fill semantics.

## 5. Do not proceed to paper/live execution

Stage 12 remains historical simulation/differential validation.

No network authority, external-order authority, or capital authority should be introduced by Stage 12.4.

---

# 8. Research / professional reasoning library

The user specifically wants the reasoning layer grounded in research so it can cite, challenge, and reduce hallucination.

The repository currently has the ingestion/trust machinery, but it does **not** yet contain a complete verified canonical investment-research library.

The next research-library effort should use the existing Stage 4–6 workflow rather than placing unreviewed summaries directly into prompts.

## Required source treatment

For every source:

~~~
exact source bytes / canonical record
        ↓
SHA-256 artifact
        ↓
VERIFIED source identity
        ↓
claim draft
        ↓
exact locator
        ↓
scope / assumptions / limitations
        ↓
counter-evidence search
        ↓
independent reviewer
        ↓
APPROVED claim
        ↓
evidence / replication graph
        ↓
reasoning corpus
~~~

Do not create one “book summary” blob and treat it as truth.

Break sources into scoped claims.

## Core knowledge domains

The reasoning layer should eventually contain reviewed material across:

### Accounting / corporate finance

- revenue recognition;
- working capital;
- deferred revenue;
- stock-based compensation;
- goodwill and impairment;
- capitalized vs expensed investment;
- leases;
- deferred taxes;
- minority interests;
- pensions;
- acquisitions;
- dilution;
- buybacks;
- debt covenants;
- capital structure;
- cost of capital;
- ROIC;
- incremental ROIC;
- reinvestment.

### Valuation

- FCFF;
- FCFE;
- dividend discount;
- residual income;
- economic profit;
- APV;
- reverse DCF;
- comps;
- transactions;
- SOTP;
- replacement value;
- liquidation value;
- NAV;
- real options;
- probability-weighted valuation;
- LBO.

### Business strategy

- industry structure;
- barriers to entry;
- switching costs;
- network effects;
- economies of scale;
- brand;
- distribution advantage;
- cost advantage;
- customer concentration;
- supplier power;
- pricing power;
- capital intensity;
- unit economics;
- operating leverage;
- reinvestment runway.

### Quantitative finance / econometrics

- portfolio theory;
- covariance estimation;
- shrinkage;
- factor models;
- momentum;
- quality;
- value;
- low volatility;
- risk parity;
- hierarchical methods;
- Bayesian estimation;
- robust optimization;
- CVaR;
- time-series and cross-sectional methods;
- cointegration;
- regime change;
- multiple testing;
- selection bias;
- survivorship bias;
- look-ahead bias;
- non-stationarity;
- factor decay;
- crowding.

### Derivatives and risk

- Black-Scholes-Merton;
- term structure;
- fixed income;
- swaps;
- Greeks;
- scenario analysis;
- VaR / ES;
- historical simulation;
- exception testing;
- model risk;
- liquidity and tail risk.

### Market microstructure / execution

- order types;
- spread;
- depth;
- queue;
- impact;
- latency;
- adverse selection;
- transaction cost analysis;
- implementation shortfall;
- venue behavior;
- broker reconciliation.

### Behavioral finance

- overconfidence;
- base-rate neglect;
- anchoring;
- representativeness;
- disposition effect;
- narrative fallacy;
- confirmation bias;
- incentive effects.

## Canonical research corpus to ingest and verify

This list is a target research library, not a statement that every item has already been ingested.

### Valuation / corporate finance

- Aswath Damodaran — Investment Valuation and NYU Stern valuation materials.
- Koller, Goedhart, Wessels — Valuation: Measuring and Managing the Value of Companies.
- Rosenbaum & Pearl — Investment Banking.
- Graham & Dodd — Security Analysis.
- Penman — Financial Statement Analysis and Security Valuation.
- Mauboussin / Rappaport expectations-based valuation work.

### Strategy / business quality

- Michael Porter — competitive strategy / industry structure.
- Bruce Greenwald — Competition Demystified.
- capital-allocation / ROIC / reinvestment literature.
- historical business-quality and moat research, treated as competing frameworks rather than doctrine.

### Portfolio theory / asset pricing

- Markowitz — Portfolio Selection.
- Black-Litterman.
- Fama-French factor literature.
- Jegadeesh & Titman — momentum.
- Novy-Marx — gross profitability.
- Asness, Moskowitz & Pedersen — value and momentum.
- Asness, Frazzini & Pedersen — Quality Minus Junk.
- DeMiguel, Garlappi & Uppal — 1/N vs optimized portfolios.
- Ledoit-Wolf covariance/shrinkage work.
- Grinold & Kahn — Active Portfolio Management.
- Ilmanen — Expected Returns.
- Palomar — Portfolio Optimization: Theory and Application.

### Research methodology / overfitting

- Bailey et al. — Probability of Backtest Overfitting.
- Bailey & López de Prado — Deflated Sharpe Ratio.
- López de Prado — relevant purged-CV / financial ML methodology.
- replication and multiple-testing literature.
- model-selection and forecast-calibration literature.

### Microstructure / execution

- Larry Harris — Trading and Exchanges.
- Cartea, Jaimungal & Penalva — Algorithmic and High-Frequency Trading.
- implementation shortfall / market-impact literature.
- venue and queue-model literature.

### Risk

- Kupiec — unconditional coverage.
- Christoffersen — interval / independence / conditional coverage testing.
- coherent risk-measure / expected-shortfall literature.
- historical-simulation methodology and known weaknesses.
- stress-testing/model-risk literature.

## Historical case library to build

Cases should not be used as simplistic analogies.

For each case preserve:

- initial information available at the time;
- competing narratives;
- financing/liquidity mechanics;
- valuation regime;
- market structure;
- causal uncertainty;
- outcome;
- similarities and differences to any current case.

Initial case set:

- dot-com bubble;
- GFC;
- LTCM;
- Lehman;
- Enron;
- Wirecard;
- SVB;
- COVID crash;
- 2022 rate shock;
- Japanese asset bubble;
- commodity supercycles;
- banking crises;
- major LBO failures;
- spin-offs;
- failed acquisitions;
- Apple turnaround;
- Amazon reinvestment period;
- Nvidia business transformation;
- GE conglomerate decline.

---

# 9. Third-party technology research and intended role

Re-verify every license/version when integration actually occurs.

## Current core dependencies

### skfolio

Role:

- baseline allocation;
- covariance estimators;
- minimum-risk optimization;
- HRP/HERC.

Architecture rule:

First Current owns constraints, identities, OOS comparison, robustness and authority.

### QuantLib

Role:

- pricing;
- curves;
- bonds;
- options;
- swaps;
- Greeks / sensitivities.

Architecture rule:

First Current owns contracts and independent references.

QuantLib mutable global evaluation date must remain contained by the shared lock/runtime boundary.

### NautilusTrader

Role:

- historical event-driven execution differential now;
- possible paper execution much later.

Current pin:

- 2.0.0rc5;
- Python 3.12+ only.

Important:

It is LGPL-licensed and currently a release candidate.

Keep dependency/license boundaries explicit.

No LiveNode or live adapter should enter Stage 12 without a separately designed authority plane.

## Planned / researched dependencies

### OpenSourceRisk/Engine

Correct ORE project for risk is OpenSourceRisk/Engine.

Do **not** confuse it with regolith-labs/ore, which is unrelated crypto/mining software.

Target role:

- independent portfolio risk comparison;
- richer scenario/risk analytics;
- later XVA / derivatives risk where justified.

Integration rule:

ORE should be an adapter behind First Current contracts and validated against the independent Stage 11 baseline.

### Perspective

Target role:

- high-performance analytical terminal;
- markets;
- company/valuation;
- portfolio;
- risk;
- strategy lab;
- backtest;
- execution/P&L views.

It should be UI/analytics, not the owner of business logic.

### DuckDB

Already used.

Target remains:

- prototype analytical store;
- point-in-time queries;
- local research datasets.

### Polars / Arrow

Still useful future candidates for:

- fast transforms;
- streaming/larger data;
- common memory interchange.

Not currently core dependencies.

### Microsoft Qlib

Reference/study project only at present.

Useful concepts:

- dataset/feature organization;
- rolling research;
- concept drift;
- ML experiment organization;
- automated factor research.

Avoid making the whole platform a runtime core unless a clear gap emerges.

### OpenBB

Reference/provider abstraction only unless licensing architecture is deliberately accepted.

Earlier research found the current project license restrictive for casual embedding.

### SilvioBaratto/optimizer

Study architecture only unless separately licensed.

Earlier research identified a noncommercial license incompatible with an unrestricted commercial-capable core.

### ArcticDB

Technically useful, but earlier licensing research made it unattractive for unrestricted commercial production.

---

# 10. Professional reasoning behavior the OS should enforce

The system should not merely know formulas.

It should decide which framework is economically appropriate.

Examples:

## Software company

Do not blindly extrapolate EBIT.

Inspect:

- retention;
- CAC economics;
- incremental margins;
- stock compensation;
- dilution;
- R&D and sales investment;
- reinvestment runway;
- implied expectations.

## Bank

Do not default to enterprise-value FCFF.

Consider:

- P/B;
- P/E;
- ROE;
- residual/excess-return frameworks;
- regulatory capital;
- funding as operating input.

## Early-stage biotech

Plain DCF may be misleading.

Consider:

- probability-adjusted pipeline values;
- milestone structure;
- trial state;
- cash runway;
- dilution;
- binary outcomes.

## Conglomerate

SOTP should become a natural candidate where disclosed segments have distinct economics.

## Red Team questions

Every material conclusion should be attacked.

Examples:

- Which assumptions drive most of the value?
- How much comes from terminal value?
- Is WACC internally consistent?
- Are margins above defensible industry economics?
- Does reinvestment support assumed growth?
- Is terminal ROIC plausible?
- What does current price imply?
- What do other methods imply?
- Could accounting choices distort FCF?
- What evidence would invalidate the thesis?
- What would make the strategy stop working?
- Could results be caused by selection bias or leakage?
- Can transaction costs erase the edge?
- Is liquidity sufficient at intended scale?

## Triangulation rule

Never worship one model.

Preserve:

- DCF;
- comps;
- reverse DCF;
- SOTP;
- historical multiples;
- scenario distributions;
- market-implied assumptions.

Do not hide disagreement by averaging unlike methods.

---

# 11. Research/backtest doctrine

Every serious research experiment should preserve:

- experiment ID;
- strategy/model ID;
- git revision;
- dataset fingerprint;
- universe fingerprint;
- feature fingerprint;
- parameter fingerprint;
- number of variants tested;
- train/validation/test windows;
- purge/embargo settings;
- cost model;
- benchmark;
- turnover;
- exposure;
- PBO;
- Sharpe / DSR;
- max drawdown;
- CVaR/ES;
- artifacts and evidence references.

Mandatory conceptual baselines:

- cash;
- broad market benchmark;
- equal weight;
- inverse volatility;
- candidate.

Do not call a strategy “profitable” because one backtest is positive.

Preferred language:

- historically documented;
- evidence-backed candidate;
- in-sample;
- out-of-sample;
- prospective shadow;
- insufficient evidence.

Initial systematic family discussed earlier:

- Quality + Value + Momentum.

Future candidates may include:

- trend following;
- low-vol/defensive;
- earnings/revisions;
- carry;
- statistical arbitrage;
- event-driven;
- volatility/derivatives.

Every family should enter the same research lifecycle.

---

# 12. Data architecture gaps still to close

These are major outstanding engineering areas even though the prototype already has strong point-in-time semantics.

## Full Security Master

A complete durable Security Master is still a major requirement.

Conceptual hierarchy:

~~~
Company
 └── Security
      ├── Listing
      │    ├── ticker
      │    ├── exchange
      │    └── currency
      └── identifiers
           ├── ISIN
           ├── FIGI
           ├── CUSIP
           ├── CIK
           └── vendor IDs
~~~

It must handle:

- ticker changes;
- delistings;
- mergers;
- dual listings;
- ADRs;
- share classes;
- symbol reuse;
- exchange changes;
- splits;
- spin-offs;
- dividends;
- corporate actions;
- survivorship.

Never make ticker the permanent identity.

## Production market data

Current tests and fixtures prove semantics but do not constitute a production real-time/point-in-time market-data stack.

Need:

- licensed provider selection;
- raw capture;
- corrections/revisions;
- exchange calendars;
- corporate actions;
- stale-data policy;
- outage/gap policy;
- vendor reconciliation;
- clock/timezone normalization.

## Broader fundamentals

Need eventually:

- richer XBRL normalization;
- non-US fundamentals;
- analyst estimates if legally/licensably available;
- segment history;
- industry-specific accounting packs;
- restatement handling.

---

# 13. What is left to build

The exact numbering after Stage 12 is not frozen beyond the existing Stage 12 docs. The recommended order below preserves the current architecture.

## Immediate — Stage 12.3 is complete

The zero-friction Nautilus differential baseline is green across Python 3.11–3.13 after the rc5 compatibility repair.

The next implementation work begins at Stage 12.4.

## Stage 12.4 — controlled differential expansion

The existing Stage 12.3 document already specifies this next slice.

Expand one behavior at a time:

1. explicit order latency;
2. explicit market-data latency;
3. deterministic fees;
4. controlled partial liquidity / partial fills;
5. IOC;
6. FOK;
7. limit orders with controlled non-marketable/marketable transitions.

For every new behavior:

- write a separate frozen equivalence contract;
- prove First Current reference behavior first;
- map Nautilus explicitly;
- compare outcomes;
- preserve mismatches rather than tolerating them silently.

Do not jump directly to “Nautilus matches our simulator.”

## Later Stage 12 — execution research

Recommended later slices:

### Multi-event / multi-order replay

- multiple orders;
- portfolio schedule;
- cash/inventory state;
- conflicting orders;
- cancels/replaces;
- session boundaries.

### Execution analytics

- arrival price;
- VWAP;
- implementation shortfall;
- spread cost;
- impact estimate;
- participation;
- fill ratio;
- latency cost;
- opportunity cost.

### Execution robustness

- market-data gaps;
- stale book;
- crossed/locked states;
- partial sessions;
- extreme volatility;
- liquidity collapse;
- oversized orders;
- replay determinism.

### Shadow execution only

Only after historical differentials are stable should a shadow/paper adapter be considered.

It must still have:

- no live capital;
- explicit finite authorization;
- kill conditions;
- broker/account reconciliation if a sandbox broker is later used;
- no reuse of a historical-only permit for network activity.

## ORE integration

Stage 11 intentionally stopped before OpenSourceRisk/Engine.

Remaining work:

1. freeze the exact ORE version/build;
2. define service/FFI boundary;
3. map First Current instruments and market data;
4. preserve input lineage;
5. run deterministic scenario fixtures that overlap Stage 11;
6. compare ORE vs First Current risk cube;
7. compare supported VaR/ES fixtures where semantics truly match;
8. record mismatches explicitly;
9. only then expose ORE-specific capabilities;
10. XVA only after core mapping is proven.

ORE must not replace First Current’s risk contracts.

## Broader pricing/risk

Potential later work:

- more curve types;
- inflation;
- credit curves;
- CDS;
- FX forwards/options;
- American/Bermudan options;
- futures;
- commodities;
- structured products;
- stochastic scenario models;
- Monte Carlo;
- counterparty risk;
- XVA.

Each requires explicit product semantics and independent validation.

## Perspective terminal

Still not built.

Target workspaces:

- Markets;
- Research Radar;
- Evidence / Claim Workbench;
- Company;
- Financials;
- Valuation;
- Portfolio;
- Risk;
- Strategy Lab;
- Backtests;
- PAPER monitoring;
- Execution;
- P&L / TCA;
- Audit / lineage.

Design rule:

The terminal consumes immutable domain artifacts.

It must not implement independent business logic in the UI.

## Real research library

The Research Radar currently proves live arXiv metadata discovery, but the broad professional library is not yet built.

Add adapters/ingestion for:

- NBER;
- SSRN where feasible;
- DOI/Crossref metadata;
- academic journals;
- regulator publications;
- BIS;
- central banks;
- SEC;
- accounting standards;
- institutional research;
- GitHub code associated with papers;
- canonical books/manuals where lawful source access exists.

The verified Claim Workbench remains the ingestion gate.

## Security Master / corporate actions

Build before serious real-universe backtests.

## Data validation layer

Consider explicit:

- Pandera schemas;
- provider-level data-quality reports;
- cross-provider reconciliation;
- PIT validation suites;
- corporate-action reconstruction tests.

## Operational observability

Need:

- structured logging;
- run IDs;
- metrics;
- error taxonomy;
- traceability across services;
- deterministic environment capture;
- dependency/version SBOM;
- data/provider health dashboards.

## Reproducible environment

Current pyproject ranges are useful for development but final reproducible research should add:

- lockfile strategy;
- exact environment manifests;
- OS/architecture metadata;
- solver versions;
- native-library versions;
- QuantLib/Nautilus build details.

## Security

Before any network execution:

- secret storage;
- credential isolation;
- least privilege;
- audit logs;
- network egress policy;
- broker sandbox separation;
- kill switch independent of strategy code;
- disaster recovery;
- compromised-data handling.

## Compliance/governance

If this ever becomes a product used for investment decisions, add a separate governance/compliance workstream.

Do not infer legal/regulatory status automatically from risk-model outputs.

## Live trading

Live capital is **not** merely “the next implementation step.”

If it is ever considered, it requires a new authority plane after:

- long prospective PAPER evidence;
- execution reconciliation;
- independent risk review;
- security review;
- operations readiness;
- compliance review;
- explicit capital limits;
- hard external kill switch;
- full audit trail.

No current artifact should be repurposed as live authorization.

---

# 14. Recommended future build order

After Stage 12.3 is repaired:

1. Stage 12.4 controlled execution differential expansion.
2. Stage 12.5 multi-event / partial-fill / latency execution validation.
3. Stage 12 execution robustness + TCA + shadow-only review.
4. OpenSourceRisk/Engine differential adapter.
5. Full Security Master + corporate actions.
6. Production point-in-time market-data provider pipeline.
7. Broader verified research-library ingestion.
8. Perspective terminal.
9. Cross-engine orchestration / application services.
10. Performance and scale hardening.
11. Security / operational readiness.
12. Long prospective PAPER program.
13. Only then discuss a separate live-capital architecture.

---

# 15. Definition of “ahead of the curve”

The user wants the OS to be live and ahead of the curve.

That should mean:

- research arrives quickly;
- information is timestamped immediately;
- claims are reviewed quickly;
- models update from newly available evidence;
- market/research changes can trigger analysis;
- prospective forecasts are frozen and measured;
- research degradation is detected;
- portfolio/risk/execution states update quickly;
- the terminal surfaces what changed and why.

It should **not** mean:

- use data before it was knowable;
- let an LLM improvise evidence;
- skip review;
- accept a backtest because it looks good;
- silently choose an optimizer;
- let one engine become unquestioned truth;
- trade on stale/incomplete lineage.

Speed comes from automation of disciplined process, not removal of discipline.

---

# 16. Important library / licensing notes

These are architectural notes from prior research and should be reverified before release/integration.

- Perspective — Apache-2.0.
- QuantLib — permissive BSD-style.
- OpenSourceRisk/Engine — modified BSD-style.
- skfolio — BSD-3-Clause.
- DuckDB — MIT.
- Polars — MIT.
- Pandera — MIT.
- exchange_calendars — Apache-2.0.
- Microsoft Qlib — MIT.
- NautilusTrader — LGPL-3.0; keep boundaries/obligations explicit.
- SilvioBaratto/optimizer — previously identified as PolyForm Noncommercial; do not embed as commercial-capable core without licensing.
- OpenBB — previously identified as AGPL-3.0; do not casually embed without accepting its obligations.
- ArcticDB — previously identified with BSL/commercial-production restrictions; not recommended as default core storage.

The repository itself should receive an explicit top-level licensing decision before public/commercial release if one is still absent.

---

# 17. Current module map

The source package is now broad. Important groups:

## Data / provenance

- artifacts.py
- ledger.py
- lineage.py
- persistent.py
- ingestion.py
- models.py
- store.py
- adapters/

## Research discovery / trust

- research_radar.py
- radar_triage.py
- research_review.py
- research_catalog.py
- review_queue.py
- claim_workbench.py
- claims.py
- claim_evidence_graph.py
- evidence.py
- reasoning_dossier.py

## Whole-case reasoning

- research_case.py
- scenarios.py
- case_dossier.py
- professional_reviews.py
- case_reviews.py
- readiness.py
- calibration.py

## Fundamentals

- financial_statements.py
- fundamental_schedules.py
- fundamental_model.py
- advanced_schedules.py
- advanced_fundamental_model.py

## Valuation

- valuation.py
- valuation_methodology.py
- valuation_sensitivity.py
- comparables.py
- comps_normalization.py
- sotp.py
- lbo.py
- triangulation.py

## Quant research

- research_universe.py
- factor_contracts.py
- validation.py
- backtest_economics.py
- performance_analytics.py
- overfitting_diagnostics.py
- model_registry.py
- paper_monitoring.py
- prospective_review.py
- research_lab.py
- research_lab_cli.py
- research_lab_demo.py

## Portfolio

- portfolio_construction.py
- covariance.py
- portfolio_optimization.py
- portfolio_hierarchical.py
- portfolio_comparison.py
- portfolio_robustness.py
- portfolio_decision.py
- portfolio_paper_authorization.py
- portfolio_paper_monitoring.py
- portfolio_paper_review.py

## Pricing / risk

- pricing_risk_contracts.py
- quantlib_runtime.py
- pricing_quantlib.py
- interest_rate_curves.py
- bond_pricing.py
- swap_pricing.py
- scenario_revaluation.py
- portfolio_risk_cube.py
- historical_simulation_risk.py
- var_backtesting.py
- risk_review.py

## Simulation / execution

- execution_contracts.py
- execution_reference.py
- execution_nautilus.py

---

# 18. Testing philosophy

Tests are part of the product’s trust model.

Prefer tests that attack failure boundaries, not only happy paths.

Existing test families cover:

- future-known data leakage;
- stale information;
- content-identity conflicts;
- tampering;
- missing provenance;
- duplicate semantic keys;
- accounting imbalance;
- stale permits;
- inappropriate valuation method;
- invalid terminal assumptions;
- duplicate peers;
- incomplete scenario coverage;
- optimizer constraint violations;
- covariance mismatch;
- OOS fold mismatch;
- robustness fragility;
- unresolved human objections;
- shadow authorization expiry;
- kill conditions;
- QuantLib differential mismatch;
- VaR chronology;
- exception clustering;
- execution chronology;
- fill state transitions;
- replay horizon violations;
- Nautilus differential behavior.

At the current Stage 12.3 branch, the suite is in the high-400-test range.

Do not weaken tests just to make CI green.

If an external library API changes, adapt the boundary or pin intentionally.

---

# 19. CI requirements

Main CI runs on:

- Python 3.11;
- Python 3.12;
- Python 3.13.

Normal workflow also runs:

- unit tests;
- fail-closed demo;
- edge-discovery demo.

NautilusTrader v2 is conditionally installed only on Python 3.12+.

Therefore:

- generic imports must remain safe on 3.11;
- Nautilus runtime tests must be intentionally gated;
- a green 3.11 job alone does not validate Stage 12.3.

There is also a separate live arXiv smoke workflow that must remain DISCOVERY_ONLY.

---

# 20. Development process rules for the next assistant

1. Inspect current main before changing anything.
2. Read the relevant stage document.
3. Read the exact affected implementation and tests.
4. Work in small commits/slices.
5. Do not stack a new stage on a red CI head.
6. Use immutable/content-addressed artifacts for economically meaningful state.
7. Preserve point-in-time chronology.
8. Make unsupported behavior fail closed.
9. Add adversarial tests with every new boundary.
10. Keep third-party engines behind adapters.
11. Add an independent reference before trusting a complex external engine.
12. Preserve version/build provenance.
13. Never add implicit live authority.
14. Do not broaden one stage’s equivalence claim because one fixture passed.
15. Document every completed stage in docs/.
16. Update README when a stage baseline materially changes.
17. Prefer human review gates to opaque weighted scores.
18. Preserve negative evidence, disagreement, and failed replications.
19. Never “fix” a failure by silently relaxing policy.
20. If a dependency behaves differently across Python versions, make the compatibility boundary explicit.

---

# 21. Commands / entry points

Install:

~~~bash
python -m pip install -e .
~~~

Run tests:

~~~bash
python -m unittest discover -s tests -v
~~~

Core demos:

~~~bash
quantos demo
quantos edge-demo
~~~

Research Lab:

~~~bash
quantos-lab demo
~~~

Research Radar:

~~~bash
quantos-radar scan-arxiv --max-results 20
quantos-radar review-list --status QUEUED
~~~

The user does not rely on a local repo for this project; ChatGPT has been operating directly through GitHub tooling and pushing to the repository.

---

# 22. Documentation index for restart

Read these first:

1. README.md
2. HANDOFF.md
3. docs/STAGE_11_COMPLETE.md
4. docs/EXECUTION_CONTRACTS_STAGE_12_1.md
5. docs/REFERENCE_EXECUTION_STAGE_12_2.md
6. docs/NAUTILUS_DIFFERENTIAL_STAGE_12_3.md
7. src/quantos/execution_nautilus.py
8. tests/test_execution_nautilus.py

For earlier architecture:

- docs/STAGE_3_COMPLETE.md
- docs/STAGE_4_COMPLETE.md
- docs/STAGE_5_COMPLETE.md
- docs/STAGE_6_COMPLETE.md
- docs/STAGE_7_COMPLETE.md
- docs/STAGE_8_COMPLETE.md
- docs/RESEARCH_LAB_STAGE_9_10.md
- docs/STAGE_10_COMPLETE.md
- docs/STAGE_11_COMPLETE.md

---

# 23. Product boundaries that must not regress

Do not turn any of the following into stronger claims than they are:

- VERIFIED source ≠ proven conclusion.
- APPROVED claim ≠ universal truth.
- REPLICATION_SUPPORTED ≠ probability.
- balanced statements ≠ accurate forecast.
- ESTIMATED line ≠ reported fact.
- MethodPermit ≠ correct valuation.
- valuation ≠ investment recommendation.
- OOS result ≠ future edge.
- robustness WITHIN_POLICY ≠ deploy.
- PAPER ≠ live.
- historical VaR ≠ capital requirement.
- statistical non-rejection ≠ model approval.
- historical Nautilus differential match ≠ live execution validation.
- research recommendation ≠ external order authority.
- no artifact currently authorizes real capital.

---

# 24. Major risks to continue watching

## Data risk

The most dangerous failure is clean-looking analysis built on:

- revised data;
- survivorship;
- wrong timestamps;
- missing corporate actions;
- wrong security identity;
- stale prices;
- incomplete filings.

## Model risk

- wrong model for business type;
- false precision;
- terminal-value dominance;
- unstable covariance;
- non-stationarity;
- overfitting;
- hidden multiple testing;
- model/solver fallback;
- miscalibrated forecasts.

## Portfolio risk

- concentration;
- turnover;
- estimator sensitivity;
- cluster instability;
- liquidity;
- crowding;
- covariance regime shifts.

## Execution risk

- latency semantics;
- queue assumptions;
- hidden liquidity;
- partial-fill behavior;
- tick/lot rounding;
- stale book;
- fees/impact;
- broker/venue mismatch.

## AI/reasoning risk

- hallucinated evidence;
- summary treated as source;
- unsupported causal claim;
- hidden assumption;
- omitted counter-evidence;
- false analogy to historical case;
- “professional sounding” output without provenance.

The existing reasoning/evidence architecture exists specifically to prevent these failures.

---

# 25. Completion criteria for the broader prototype

A serious “Quant OS prototype complete” claim should require at least:

- verified research intake beyond one source family;
- full Security Master and corporate actions;
- real point-in-time market/fundamental provider path;
- professional reasoning library populated with reviewed claims;
- fundamentals;
- valuation;
- research lab;
- portfolio;
- pricing/risk;
- ORE differential baseline;
- historical execution simulation;
- shadow execution/reconciliation;
- Perspective terminal;
- complete lineage from raw source to displayed figure;
- reproducible environment;
- CI and golden workflows;
- operational observability;
- security review;
- no silent capital path.

A separate production/live program would require materially more.

---

# 26. Recommended next action

Begin **Stage 12.4 — controlled Nautilus differential expansion**.

The first slice should add exactly one new behavior behind a frozen equivalence contract, preferably explicit deterministic latency or deterministic fees.

Required pattern:

> extend the First Current reference semantics first → freeze the equivalence contract → map the same behavior into NautilusTrader → run differential fixtures → preserve mismatches explicitly → require the full Python 3.11/3.12/3.13 CI matrix to remain green.

Do not combine latency, fees, partial fills, queue behavior, and multiple orders into one change.

---

# 27. One-sentence handoff

First Current Quant OS has already built a strict point-in-time evidence → reasoning → fundamentals → valuation → quant research → portfolio → pricing/risk pipeline and now has a green Stage 12.3 historical NautilusTrader differential baseline; the current codebase is at v0.12.3 and the next controlled build is Stage 12.4 execution-equivalence expansion without weakening the no-live-capital boundary.