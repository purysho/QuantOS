# First Current Quant OS — Handoff

> Comprehensive implementation handoff for the First Current Quant OS prototype.
>
> This document is intended to let a new ChatGPT/Codex/engineer session continue the project without reconstructing the architecture from chat history.
>
> **Repository:** purysho/First-Current-Quant-OS-prototype  
> **Repository visibility:** public (Apache-2.0)  
> **Current package version:** 0.19.0  
> **Implementation baseline for this handoff:** Stages 12.4–20 on branch `claude/stoic-cerf-mts7pn` (execution program and Nautilus divergence resolution, Security Master, exchange calendars and return panels, ORE products and analytics, research sources, observability/security, market-data pipeline, Perspective terminal, keyless data and first-run setup, container and live-USB packaging, Apache-2.0 licensing, locked environment)  
> **Previous green baseline on main:** b262fcf1f18787fd5f4661de2ed3c5c3510ad49b (Stage 12.3)

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
- OpenSourceRisk/Engine 1.8.17.0 (`open-source-risk-engine`) as the optional `ore` extra, x86-64 Linux/Windows only, run in an isolated child process.
- requests/pytz for external intake and time support.
- Parquet export exists in the earlier point-in-time data layer.
- exchange_calendars for exchange session semantics (reference for the frozen First Current calendar).
- uv for locking and syncing exact environments.
- FINOS Perspective 3.8.0 in the browser (jsDelivr, SRI-pinned; not a Python dependency) for the read-only terminal.

Current pyproject dependencies at v0.19.0:

~~~
cvxpy >=1.6,<2
duckdb >=1.4,<2
exchange_calendars >=4.5,<5
numpy >=2,<3
packaging >=24
nautilus_trader ==2.0.0rc5 ; python_version >= 3.12
QuantLib >=1.43,<2
pytz >=2025.2
requests >=2.32,<3
skfolio >=1.0,<2

[optional ore]
open-source-risk-engine ==1.8.17.0 ; x86-64 Linux or AMD64 Windows
~~~

Exact versions and hashes are in `uv.lock` (source of truth) and `requirements.lock` (hashed pip export). See docs/REPRODUCIBLE_ENVIRONMENT.md.

## Important architecture ideas researched but not yet fully implemented

The original target architecture also considered:

- Polars for fast transformation;
- Apache Arrow as interchange;
- PostgreSQL for application metadata/state at larger scale;
- Pandera for dataframe contracts;
- EdgarTools for richer SEC/XBRL ingestion;
- DuckDB + Parquet as the analytical/PIT foundation;

exchange_calendars (Stage 13.3) and a Perspective terminal (Stage 18) are now built. The Security Master (Stage 13), ORE differentials (Stage 14) and a market-data pipeline (Stage 17) exist. There is still no licensed reference-data or corporate-action feed, and no live market-data key has been exercised.

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

### 12.3 NautilusTrader historical differential adapter — built, green

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

### 12.4 Frozen Nautilus equivalence contracts — built

The Stage 12.3 overlap is now the `ZERO_FRICTION` contract. Five more frozen, content-addressed contracts each add exactly one behavior:

1. 12.4.1 `DETERMINISTIC_FEES` — commission_bps → equal maker/taker fee rate; only commissions exactly representable at currency precision (Nautilus rounds half-even);
2. 12.4.2 `ORDER_LATENCY` — StaticLatencyModel; activation must coincide with a quote arrival;
3. 12.4.3 `MARKET_DATA_LATENCY` — knowledge_time + latency → ts_init; post-horizon arrivals dropped;
4. 12.4.4 `IMMEDIATE_TIME_IN_FORCE` — IOC shortfall / FOK kill / non-marketable IOC-FOK; Nautilus venue cancel mapped explicitly to EXPIRED;
5. 12.4.5 `LIMIT_TRANSITION` — resting DAY/GTC limit filled when the touch reaches the limit exactly, or expired at the replay horizon.

Other properties:

- contracts are registered in `NAUTILUS_EQUIVALENCE_CONTRACTS`; tampered contracts, contracts that do not exercise their behavior, and fixtures combining behaviors fail closed;
- the differential now requires the reference `SimulatedFill` records and compares total fees and the exact per-fill time/quantity/price/fee sequence, in addition to state/count/quantity/VWAP;
- Nautilus results record `contract_id`, behavior, raw Nautilus terminal state, fill times, fees and liquidity sides;
- every observed reference/Nautilus divergence is recorded on its contract as `known_divergences` and refused;
- differential-gate logic has synthetic tests that run on Python 3.11 without Nautilus;
- CI sets `QUANTOS_REQUIRE_NAUTILUS=1` on 3.12/3.13 so runtime tests cannot silently skip.

Observed divergences that define Stage 12.5 work:

- Nautilus matches an in-flight (latency-delayed) order only on the next data arrival, against the post-activation book;
- Nautilus L1 MARKET DAY/GTC orders larger than displayed size fill the remainder one tick worse immediately;
- Nautilus fills a resting limit at its own limit price (MAKER) even when the book crosses through it;
- Nautilus liquidity consumption does not refresh on an unchanged repeated quote;
- Nautilus IOC takes displayed quantity even when the reference policy disables partial fills;
- Nautilus rounds commissions half-even to currency minor units.

Reference: docs/NAUTILUS_EQUIVALENCE_CONTRACTS_STAGE_12_4.md

### 12.5 Multi-order execution schedules — built

- explicit position targets bound to child order intents;
- exact allocation, direction and currency (no implicit FX) checks;
- orders run through the unchanged reference engine;
- fills reconciled into positions, cash, fees and minimum cash;
- concurrent working orders on one instrument fail closed, because the reference has no shared-liquidity semantics;
- cash and short breaches kept as `CONSTRAINT_BREACH`.

Reference: docs/EXECUTION_SCHEDULES_STAGE_12_5.md

### 12.6 Multi-instrument Nautilus schedule differential — built

- the seventh frozen contract, `MULTI_INSTRUMENT_SCHEDULE`, allows one zero-friction order per instrument, with several instruments in one BacktestEngine;
- fills are attributed by client_order_id;
- per-order differentials are aggregated, and one mismatch keeps the whole schedule MISMATCH.

Reference: docs/NAUTILUS_SCHEDULE_DIFFERENTIAL_STAGE_12_6.md

### 12.7 Replay data-quality gate — built

Reports CLEAN, DEGRADED or UNUSABLE. It never repairs data. It checks:

- insufficient quotes;
- sequence regressions;
- no book at submission;
- ambiguous arrival order;
- gaps, wide spreads, mid jumps, thin books and knowledge lag;
- stale book at submission;
- trades outside the quote.

Reference: docs/REPLAY_DATA_QUALITY_STAGE_12_7.md

### 12.8 Deterministic TCA — built

- implementation shortfall against the arrival mid;
- decomposed exactly into timing, half-spread, slippage/impact, fees and opportunity cost;
- the reconciliation is asserted in Decimal;
- fill ratio, participation and fill timing;
- a market-VWAP benchmark only when trade prints exist;
- aggregation across a schedule.

Reference: docs/EXECUTION_TCA_STAGE_12_8.md

### 12.9 Two-person execution review — built

- binds the schedule and its result, the replay quality report, TCA for exactly the schedule's orders, and an optional Nautilus schedule differential;
- reviewer and Red Team challenger must differ;
- unresolved objections cannot be outvoted;
- WITHIN_POLICY recommends only `ELIGIBLE_FOR_SHADOW_EXECUTION_DESIGN_REVIEW`;
- every authority stays NONE.

Reference: docs/EXECUTION_REVIEW_STAGE_12_9.md

### 12.10 Nautilus divergence resolution — built

- every observed reference/Nautilus divergence becomes an explicit, non-default reference mode on `ExecutionSimulationPolicy`; defaults, and therefore every existing policy ID, are unchanged;
- modes, each established by experiment against NautilusTrader 2.0.0rc5:
  - `NEXT_QUOTE_ARRIVAL` activation;
  - `ONE_TICK_THROUGH` market residual for DAY/GTC orders at L1 (IOC/FOK do not sweep);
  - resting-limit fills at `LIMIT_PRICE` as MAKER;
  - per-price-level liquidity memory refreshed `ON_LEVEL_SIZE_CHANGE`;
  - `HALF_EVEN_MINOR_UNIT` fee rounding (exact ties refused);
  - IOC `ALWAYS_ALLOW` partial fills;
- five new frozen contracts (12.10.1–12.10.5), twelve in total. Each contract requires its exact modes, and a 120-trial seeded randomized resting-limit differential matches.

Reference: docs/NAUTILUS_DIVERGENCE_RESOLUTION_STAGE_12_10.md

## Stage 13 — Security Master & corporate actions

### 13.1 Bitemporal Security Master — built

- Company → Security → Listing hierarchy, plus ISIN/FIGI/CUSIP/SEDOL/CIK/vendor identifiers;
- each record has world validity and knowledge time;
- supersession and retraction;
- ticker and identifier resolution through symbol changes and reuse; ambiguity raises;
- integrity report: collisions, overlapping primaries, dangling references, lifetime violations, invalid check digits.

Reference: docs/SECURITY_MASTER_STAGE_13_1.md

### 13.2 Corporate-action economics — built

- bitemporal events: cash and stock dividends, splits, spin-offs, cash and stock mergers, delistings;
- split-only or total-return backward adjustment factors;
- total-return series with terminal proceeds;
- position transformations;
- missing prior closes, counterparty prices, delisting proceeds and spin-off basis are reported or refused, never inferred.

Reference: docs/CORPORATE_ACTIONS_STAGE_13_2.md

### 13.3 Exchange calendars — built

- a frozen First Current XNYS rule calendar for 2010–2030: 09:30–16:00 New York, 13:00 early closes, Juneteenth from 2022, and unscheduled closures such as Sandy, the Bush and Carter funerals;
- an `exchange_calendars` adapter and a differential: an exact match over 5,279 sessions;
- navigation outside the frozen range fails closed.

Reference: docs/EXCHANGE_CALENDARS_STAGE_13_3.md

### 13.4 Research return panels — built

- raw session closes, the latest revision known at decision time, with closes known before the session closed rejected;
- split-only or total-return adjustment from the reviewed ledger, with terminal proceeds for securities delisted without a final print;
- close-to-close UTC return observations feed `PortfolioDatasetBuilder`;
- missing closes make a security incomplete, and nothing is filled.

Reference: docs/RESEARCH_RETURN_PANEL_STAGE_13_4.md

## Stage 14 — OpenSourceRisk/Engine differential — built (narrow)

- pinned ORE 1.8.17.0 as an optional extra, run in an isolated child process (`quantos.ore_worker`); its SWIG bindings crash when sharing a process with the QuantLib package;
- a content-addressed ORE input bundle is generated from frozen swap and curve artifacts;
- 14.1: swap NPV compared with the Stage 11.5 reference and QuantLib;
- 14.2: Stage 11.6 scenario P&L compared per cell and across a cube, after proving the rebuilt curves are the exact curves the revaluation used.

Reference: docs/ORE_DIFFERENTIAL_STAGE_14.md

### 14.3–14.6 ORE products and analytics — built

- 14.3 fixed-rate bonds (`DiscountingRiskyBondEngine`) versus the Stage 11.4 reference: about 5e-13. Refused: issue day > 28, a cash flow inside the settlement window, and extrapolating curves;
- 14.4 European equity options (`AnalyticEuropeanEngine`) versus a closed-form Black–Scholes–Merton reference: about 2e-14;
- 14.5 ORE sensitivity analytics on a pillar-aligned grid versus single-pillar +1bp Stage 11.6 revaluations: about 1e-11;
- 14.6 ORE stress tests (absolute zero-rate shocks) versus First Current revaluation: about 2e-10.

Reference: docs/ORE_PRODUCTS_AND_ANALYTICS_STAGE_14_3.md

## Stage 15 — research library expansion

### 15.1 Crossref DOI radar — built

- metadata-only journal discovery into the existing triage/review workflow;
- mandatory contact etiquette and throttling;
- archived response artifacts;
- DOI validation and case normalization;
- partial publication dates keep their precision tag.

Reference: docs/CROSSREF_RADAR_STAGE_15_1.md

### 15.2 NBER, SSRN and regulator / central-bank sources — built

- a frozen feed registry of ten sources, covering RSS 2.0, RSS 1.0/RDF and Atom:
  - NBER working papers;
  - Fed FEDS, IFDP, press releases and speeches;
  - SEC press releases;
  - BIS working papers and central-bank speeches;
  - ECB working papers and press releases;
- undated items (NBER) are stamped first-seen, re-scans reuse the stamp, and nothing is back-dated;
- DOCTYPE/entity XML is refused, bodies are capped at 5 MB, redirects are not followed, a contact User-Agent is required and requests are throttled;
- SSRN runs through Crossref (`prefix:10.2139`, `posted-content`, `from-posted-date`) with a mandatory query;
- a live smoke check of all ten feeds runs in the Radar Live Smoke workflow.

Reference: docs/RESEARCH_SOURCES_STAGE_15_2.md

## Stage 16 — observability and security — built

- run and span IDs through contextvars; redacted JSON-line events; an error taxonomy; metrics; a DuckDB span store and `quantos ops-report`;
- `SecretProvider` reads `QUANTOS_SECRET_<NAME>` or a private `QUANTOS_SECRETS_DIR` and refuses group- or world-readable files; secrets redact themselves;
- `EgressGuard` enforces a host allowlist on every adapter;
- an operator kill switch independent of strategy code (`quantos kill-switch`);
- a hash-chained audit log (`quantos audit-verify`).

Reference: docs/OBSERVABILITY_AND_SECURITY_STAGE_16.md

## Stage 17 — market-data provider pipeline — built

- Tiingo and Polygon EOD adapters. They take raw prices only; Polygon must return `adjusted=false`. Keys are sent in headers, and raw responses are archived;
- a revision-preserving bitemporal `BarStore`; quality reports cover missing sessions, stale data, OHLC violations, intraday captures and revisions;
- cross-provider close reconciliation in bps and provider corporate-action cross-checks against the reviewed ledger; closes feed the Stage 13.4 panel;
- `quantos market-data`. It is verified on recorded fixtures only: no live key was available.

Reference: docs/MARKET_DATA_PIPELINE_STAGE_17.md

## Stage 18 — Perspective terminal — built

- `quantos terminal export`: every DuckDB store opened read-only. Every declared table is mapped to a workspace, and a test enforces the mapping. The output is typed JSON plus a manifest carrying the export ID, per-file SHA-256 and `authority: NONE`;
- `quantos terminal serve`: loopback-only, GET-only, strict CSP, no directory listings;
- the browser pins Perspective 3.8.0 with SRI, verifies every table's SHA-256 before display, and uses a read-only datagrid;
- a headless Chromium smoke check (`QUANTOS_TERMINAL_SMOKE=1`).

Reference: docs/PERSPECTIVE_TERMINAL_STAGE_18.md

## Stage 19 — keyless public data, setup, doctor and daily runbook — built

- keyless official sources:
  - SEC ticker directory and XBRL company facts (point-in-time fundamentals; a restatement is a new version);
  - the US Treasury par curve;
  - ECB FX;
  - FRED CSV;
- explicit knowledge-time policies (`CAPTURE_TIME` or `PUBLICATION_SCHEDULE`), stored on every row;
- Security Master seeding from SEC's directory: idempotent, with assumptions flagged;
- `QUANTOS_HOME` holds `quantos.toml`, a private `secrets/` directory and `data/`;
- `quantos setup`, `quantos doctor [--online]` and `quantos daily` (failures isolated per step);
- friendly CLI errors;
- offline terminal assets through integrity-verified vendoring.

Reference: docs/KEYLESS_DATA_AND_SETUP_STAGE_19.md

## Stage 20 — container image and live USB — built

- **Container:** non-root, read-only root filesystem, `cap_drop ALL`, terminal on the host's loopback only, offline assets included. It builds, and `setup`, `doctor`, `daily` and the terminal were verified in the container.
- **Live USB:** a Debian 13 live-build recipe with encrypted LUKS2 persistence, a first-login setup wizard, a daily timer and desktop launchers. It is free software only by default; `FC_FIRMWARE=1` adds non-free firmware for more hardware.

Reference: docs/PACKAGING_STAGE_20.md, packaging/live-usb/README.md

## Cross-cutting — licensing and reproducibility

- **License:** the project is open source under Apache-2.0 (relicensed at v0.19.0). `quantos.license_policy` gates every locked dependency's license, and `THIRD_PARTY_NOTICES.md` is generated from it. See docs/LICENSING.md.
- **Environment:** `uv.lock` and the hashed `requirements.lock` are enforced in CI, with a weekly dependency-drift workflow. `quantos env-manifest` gives a content-addressed environment identity. See docs/REPRODUCIBLE_ENVIRONMENT.md.

---

# 6. Current repository state — IMPORTANT

## Current implementation head

**Stages 12.4 through 18** are on branch `claude/stoic-cerf-mts7pn`, pending review and merge to main.

Current pyproject version:

**0.18.0**

## Current CI status

The branch was validated locally with `uv sync --locked --extra ore` on Python 3.11, 3.12 and 3.13:

- 718 tests pass on each version;
- the fail-closed demo and the edge-discovery demo pass;
- on 3.11, the NautilusTrader runtime tests are skipped by design (58 skips in total there); the live-feed and browser smoke checks are opt-in everywhere (2 skips on 3.12/3.13);
- the ORE runtime tests executed on every version.

The CI workflow was rewritten to use `uv sync --locked --extra ore`, a lock-freshness job, `QUANTOS_REQUIRE_NAUTILUS` and `QUANTOS_REQUIRE_ORE`. It runs only on pull requests and on pushes to main, so **it has not yet run on GitHub for this branch**. Open a PR and confirm the full matrix is green before building further.

Stage 12.3 history: it was **green and complete at its deliberately narrow differential scope**.

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

## 1. Confirm CI on the branch or merged head

Open or inspect the PR for `claude/stoic-cerf-mts7pn`. Confirm these jobs are green on the current head:

- the `lock` job;
- the 3.11/3.12/3.13 `test` jobs, with ORE and NautilusTrader required where declared.

The first CI run also exercises the new `astral-sh/setup-uv` step and the 77 MB ORE wheel download.

## 2. Pick the next slice from section 14

Keep the pattern that every new stage used:

- a frozen, content-addressed contract or policy;
- an independent First Current reference;
- a fail-closed scope;
- mismatches preserved;
- adversarial tests;
- a stage document;
- no new authority.

For NautilusTrader, the recorded Stage 12.4 divergences are still open. Resolve each one by extending the reference semantics explicitly, or keep it refused. Never add a tolerance to make a divergence pass. Never widen an existing contract; add a new one.

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

No network authority, external-order authority, or capital authority has been introduced by any stage.

## 6. Keep ORE out of the QuantLib process

ORE must only run through `OREEngineRunner`, which isolates it in a child process. Importing `ORE` in a process that has used the QuantLib Python package can crash the interpreter.

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

The Stage 13 core (bitemporal identity, listings, identifiers, resolution, integrity, corporate-action economics) is built. Still missing: provider feeds, calendars, cross-vendor reconciliation and wiring into research datasets. The requirements below remain the target.

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

## Completed since the Stage 12.3 baseline

- **Stage 12.4:** single-behavior Nautilus contracts (fees, latencies, IOC/FOK, limit transitions).
- **Stage 12.5:** multi-order schedules with inventory and cash.
- **Stage 12.6:** multi-instrument Nautilus schedule differential.
- **Stage 12.7:** replay data-quality gate.
- **Stage 12.8:** TCA.
- **Stage 12.9:** two-person execution review.
- **Stage 13.1/13.2:** Security Master core and corporate-action economics.
- **Stage 14.1/14.2:** ORE swap NPV and scenario-cube differential.
- **Stage 15.1:** Crossref DOI radar.
- **Cross-cutting:** license decision and license gate, uv lockfiles, environment manifest.

## Still open in execution (Stage 12.x)

- Resolve or keep refusing each recorded Stage 12.4 Nautilus divergence:
  - next-arrival latency matching;
  - L1 market-order sweep;
  - maker fill at the limit price;
  - consumption refresh;
  - IOC with partial fills disabled;
  - fee rounding.
- Multi-quote partial-fill accumulation through a new contract.
- Cancels/replaces and conflicting orders on one instrument. The reference needs explicit shared-liquidity and queue semantics first.
- Session boundaries and exchange calendars.
- Sizing share targets from portfolio weights, which needs a frozen capital, price and lot-rounding policy.
- A shadow/paper execution adapter. It must come only with a separate authority plane:
  - finite authorization;
  - kill switch independent of strategy code;
  - broker/sandbox reconciliation;
  - credential isolation;
  - no reuse of historical permits.

## Still open in ORE (Stage 14.x)

The version is frozen, the process boundary exists, swap mapping and input lineage are done, and the scenario-cube differential works for swaps. Remaining:

1. bonds, options and further products, each with its own frozen overlap;
2. ORE's own sensitivity and stress analytics, compared with the Stage 11.7 cube;
3. VaR/ES comparison only where the semantics truly match;
4. XVA only after broader mapping is proven.

ORE must not replace First Current's risk contracts.

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

Built (Stage 18) as a read-only view over exports. Still to add: a stored backtest artifact store so the Backtests workspace fills, curated default layouts per workspace (pivots and charts saved as Perspective configs), and optional offline vendoring of the Perspective assets (a license NOTICE review is needed first).

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

Crossref (15.1) and NBER, SSRN, Fed, SEC, BIS and ECB (15.2) now sit beside arXiv. Still to add, all through the verified Claim Workbench gate:

- further regulators (FCA, ESMA, CFTC, FINRA) and central banks (BoE, BoJ);
- accounting standards;
- institutional research;
- code linked to papers;
- lawful canonical books and manuals.

## Security Master / corporate actions

The core exists (Stage 13). Still to add:

- a licensed reference-data and corporate-action provider feed;
- cross-vendor identifier reconciliation;
- exchange calendars beyond XNYS;
- rights issues and tender offers;
- tax treatment;

## Data validation layer

Consider explicit:

- Pandera schemas;
- provider-level data-quality reports;
- cross-provider reconciliation;
- PIT validation suites;
- corporate-action reconstruction tests.

## Operational observability

Stage 16 built structured logging, run and span IDs, metrics, an error taxonomy and the environment manifest. Still to do:

- structured logging;
- run IDs;
- metrics;
- error taxonomy;
- traceability across services;
- deterministic environment capture;
- dependency/version SBOM;
- data/provider health dashboards.

## Reproducible environment

Done:

- `uv.lock` and the hashed `requirements.lock`, enforced in CI;
- a weekly drift workflow;
- `quantos env-manifest`, a content-addressed record of OS/arch/libc, exact packages, BLAS, QuantLib/Nautilus/ORE builds, CVXPY solvers, lock fingerprints and consistency, and git revision state.

Still to do: cite `manifest_id` automatically from Research Run Manifests and simulation runs.

## Security

Stage 16 built secret storage, audit logs, an egress policy and an independent kill switch. Still required before any network execution:

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

Stages 12.3–18 are built (see section 5). Recommended next:

1. Confirm the full CI matrix on the branch PR, then merge.
2. Owner decision on a licensed market-data provider (Tiingo, Polygon or another), followed by one live Stage 17 capture and reconciliation.
3. Stage 12.x: multi-quote partial fills and cancel/replace with explicit shared-liquidity semantics.
4. Real-universe research: Stage 17 closes → Stage 13.4 panels → Stage 9 walk-forward, with a stored backtest artifact for the terminal.
5. ORE beyond the current overlaps: CDS, swaptions, XVA. Each needs a First Current reference first.
6. Cross-engine orchestration / application services.
7. Performance and scale hardening.
8. Remaining operational readiness: disaster recovery, SBOM, provider health dashboards, broker sandbox separation.
9. A long prospective PAPER program.
10. Only then discuss a separate live-capital architecture.

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

**Decided:**

- The project is **open source under Apache-2.0** (`LICENSE`, `NOTICE`, docs/LICENSING.md). The owner relicensed it at v0.19.0 so that anyone can use it for free.
- Redistributed images (container, live USB) carry license obligations beyond the source tree. For example, a published ISO must be accompanied by the matching Debian sources; see docs/LICENSING.md.
- `quantos.license_policy` enforces the dependency rules in tests:
  - permissive licenses are free to use;
  - LGPL/MPL only while unmodified and separately installed;
  - AGPL/GPL/PolyForm-NC/BUSL/SSPL are prohibited.
- ORE-SWIG (modified BSD) was reviewed and admitted.
- Have counsel review before any commercial distribution.

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
- execution_schedule.py
- execution_data_quality.py
- execution_analytics.py
- execution_review.py

## Security Master / corporate actions

- security_master.py
- corporate_actions.py

## ORE

- ore_risk.py
- ore_worker.py (child-process boundary)

## Research intake adapters

- adapters/arxiv_radar.py
- adapters/crossref_radar.py

## Environment / governance

- environment_manifest.py
- license_policy.py

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

At v0.18.0 the suite has 718 tests. On Python 3.11 the NautilusTrader runtime tests are intentionally skipped. The live-feed (`QUANTOS_LIVE_FEEDS=1`) and browser (`QUANTOS_TERMINAL_SMOKE=1`) smoke checks are opt-in. With the `ore` extra installed, the ORE runtime tests run on every Python version.

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

As of v0.18.0:

- the `lock` job verifies that `uv.lock` matches `pyproject.toml` and that `requirements.lock` is its exact export;
- the test matrix installs with `uv sync --locked --extra ore` and records the environment manifest;
- `QUANTOS_REQUIRE_NAUTILUS=1` (3.12+) and `QUANTOS_REQUIRE_ORE=1` (all versions) turn a missing engine into a failure instead of a silent skip;
- the license gate test fails on any unreviewed or prohibited dependency license;
- a weekly **Dependency Drift** workflow tests the newest versions the ranges allow, without gating merges.

There is also a separate live arXiv and institutional-feed smoke workflow that must remain DISCOVERY_ONLY. It installs through the hashed `requirements.lock`.

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

Install (exact, locked):

~~~bash
uv sync --locked --extra ore      # omit --extra ore off x86-64 Linux/Windows
# or: python -m pip install --require-hashes -r requirements.lock && python -m pip install --no-deps -e .
~~~

Run tests:

~~~bash
uv run python -m unittest discover -s tests -v
~~~

Core demos and environment identity:

~~~bash
quantos demo
quantos edge-demo
quantos env-manifest [--db data/environment.duckdb]
~~~

Research Lab:

~~~bash
quantos-lab demo
~~~

Research Radar:

~~~bash
quantos-radar scan-arxiv --max-results 20
CROSSREF_MAILTO=you@example.com quantos-radar scan-crossref --from-index-date 2026-09-01
quantos-radar review-list --status QUEUED
~~~

Third-party notices (regenerate after dependency changes):

~~~bash
python -m quantos.license_policy > THIRD_PARTY_NOTICES.md
~~~

Earlier sessions ran through GitHub tooling without a local repo. The v0.15.1–v0.18.0 work was done in a cloud container with local Python 3.11/3.12/3.13 environments.

---

# 22. Documentation index for restart

Read these first:

1. README.md
2. HANDOFF.md
3. docs/STAGE_11_COMPLETE.md
4. docs/EXECUTION_CONTRACTS_STAGE_12_1.md
5. docs/REFERENCE_EXECUTION_STAGE_12_2.md
6. docs/NAUTILUS_DIFFERENTIAL_STAGE_12_3.md
7. docs/NAUTILUS_EQUIVALENCE_CONTRACTS_STAGE_12_4.md
8. docs/EXECUTION_SCHEDULES_STAGE_12_5.md … docs/EXECUTION_REVIEW_STAGE_12_9.md
9. docs/SECURITY_MASTER_STAGE_13_1.md and docs/CORPORATE_ACTIONS_STAGE_13_2.md
10. docs/ORE_DIFFERENTIAL_STAGE_14.md
11. docs/CROSSREF_RADAR_STAGE_15_1.md
12. docs/LICENSING.md and docs/REPRODUCIBLE_ENVIRONMENT.md

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
- docs/NAUTILUS_DIVERGENCE_RESOLUTION_STAGE_12_10.md
- docs/EXCHANGE_CALENDARS_STAGE_13_3.md
- docs/RESEARCH_RETURN_PANEL_STAGE_13_4.md
- docs/ORE_PRODUCTS_AND_ANALYTICS_STAGE_14_3.md
- docs/RESEARCH_SOURCES_STAGE_15_2.md
- docs/OBSERVABILITY_AND_SECURITY_STAGE_16.md
- docs/MARKET_DATA_PIPELINE_STAGE_17.md
- docs/PERSPECTIVE_TERMINAL_STAGE_18.md
- docs/KEYLESS_DATA_AND_SETUP_STAGE_19.md
- docs/PACKAGING_STAGE_20.md
- docs/USER_GUIDE.md
- docs/CAPABILITIES.md

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
- provider-adjusted price ≠ First Current fact (only raw closes plus the reviewed ledger).
- regulator press release in the radar ≠ verified fact.
- terminal display ≠ authority (exports declare `authority: NONE`).
- kill switch released ≠ permission to trade.
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

Progress against these criteria at v0.19.0:

- **Met at prototype depth:** fundamentals, valuation, research lab, portfolio, pricing/risk, ORE differential baseline (swaps, bonds, options, sensitivities, stress), historical execution simulation, reproducible environment, Perspective terminal (read-only), operational observability.
- **Partial:**
  - research intake covers arXiv, Crossref, SSRN, NBER and regulator/central-bank feeds, but the reviewed claim library is not populated;
  - keyless point-in-time data (SEC fundamentals, Treasury, ECB, FRED) runs live; the stock-price path is built and fixture-tested but has not been exercised with a live key;
  - the Security Master exists without a reference-data feed;
  - security controls exist (secrets, egress, kill switch, audit), but no independent security review has been done;
  - lineage runs from raw source to terminal export; a per-figure drill-down in the UI is still to do.
- **Not started:**
  - shadow execution/reconciliation.

A separate production/live program would require materially more.

---

# 26. Recommended next action

1. Open a PR for `claude/stoic-cerf-mts7pn` and get the full CI matrix green. This is the first GitHub run of the uv/ORE workflow.
2. Then pick **one** slice from section 14. The most valuable next slices are:
   - publish a release: tag v0.19.0, attach a built ISO with its sha256 file and source ISO, and push the container image;
   - one live Stage 17 price capture with a free Tiingo key, reconciled across two providers;
   - a real-universe walk-forward, from Stage 17 closes through Stage 13.4 panels, with a stored backtest artifact the terminal can display.

Required pattern, unchanged:

> extend the First Current reference semantics first → freeze the contract or policy → map the external engine or provider explicitly → run differential or adversarial fixtures → preserve mismatches explicitly → require the full Python 3.11/3.12/3.13 CI matrix to remain green.

Do not combine several new behaviors into one change.

---

# 27. One-sentence handoff

First Current Quant OS has already built a strict point-in-time evidence → reasoning → fundamentals → valuation → quant research → portfolio → pricing/risk pipeline and now adds:

- a complete historical execution program: twelve frozen Nautilus contracts with the divergences resolved as explicit reference modes, schedules, a replay-quality gate, TCA and a two-person execution review;
- a bitemporal Security Master with corporate-action economics;
- exchange calendars and point-in-time total-return research panels;
- process-isolated ORE differentials for swaps, bonds, options, sensitivities and stress;
- Crossref, SSRN, NBER and regulator/central-bank research discovery;
- observability, secrets, egress control, a kill switch and a hash-chained audit log;
- a revision-preserving market-data provider pipeline;
- a read-only, integrity-verified Perspective terminal;
- an Apache-2.0 license with a dependency gate;
- keyless public data, first-run setup, a doctor and a daily runbook;
- a container image and a live-USB recipe with encrypted persistence;
- a locked, manifest-captured environment.

The codebase is at v0.19.0. The next controlled steps are a licensed data-provider decision, real-universe research on live data, and deeper execution semantics, all without weakening the no-live-capital boundary.