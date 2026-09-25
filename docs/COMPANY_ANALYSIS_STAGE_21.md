# Stage 21 — Company Analysis from SEC XBRL

Module: `quantos.company_analysis`.
CLI: `quantos analyze TICKER [--as-of DATE] [--years N]`.
Daily step: `analysis`. App: **Analyze a company**.

## What it produces

For any SEC filer, from keyless XBRL company facts (Stage 19):

- **Standardized annual statements** in the Stage 7 model: income statement, balance sheet and cash flow.
  - Each carries its filing context: accession, form, filing date, knowledge time and source artifact.
  - Every line has a locator such as `us-gaap:NetIncomeLoss 0000320193-24-000123`. A computed line says `derived: …`.
- **Validation** with the Stage 7 accounting-identity validator.
  - The tolerance is 1 in 100,000 of total assets (at least $1,000), because filers round.
  - Every issue is stored; none is forced to tie.
- **Metrics**, for the terminal and the app: revenue and growth, gross, operating and net margins, diluted EPS, free cash flow and FCF margin, return on equity and assets, debt/equity, the current ratio and a validation-error count.

## Rules

- **As originally filed.** A fiscal year is built from the first 10-K whose primary period, its latest `Assets` instant, is that year-end. Comparatives restated in later 10-Ks remain visible in `xbrl_facts` and never overwrite the original.
- **Point in time.** Only facts with `knowledge_time ≤ known_at` are used, so `--as-of 2024-06-30` shows what was knowable then.
- **Explicit mapping.** Each standardized key has an ordered list of US GAAP concepts; the first one reported wins. Examples:
  - revenue: `Revenues`, then `RevenueFromContractWithCustomerExcludingAssessedTax`, and so on;
  - temporary equity: `RedeemableNoncontrollingInterestEquity…`, `TemporaryEquityCarryingAmount…`.
- **No automatic valuation.** A DCF or other valuation needs reviewed, evidence-backed assumptions (Stage 8).

## Validator extensions (Stage 7)

Standard reconciling items, used only when a filer reports them:

| Identity | Extension |
|---|---|
| net change in cash = operating + investing + financing | `+ effect_of_exchange_rate_on_cash` (ASC 230) |
| net income = pretax income − tax | checked on `consolidated_net_income` (includes noncontrolling interests) when reported |
| assets = liabilities + equity | `+ temporary_equity` (redeemable/mezzanine equity, ASC 480-10-S99) |

## Verification on real filers (2026-09-25)

| Company | Years | Outcome |
|---|---|---|
| JPM | 10 | all identities tie |
| MSFT | 10 | all identities tie, once the FX effect on cash is included |
| BRK.B | 10 | all identities tie, once noncontrolling interests and $3.26bn of redeemable noncontrolling interest (2023) are included |
| AAPL | 10 | ties except 2020–2023, where Apple's cash-flow statement includes restricted cash that its balance-sheet cash line excludes; this is correctly flagged |

The reported figures match the companies' published numbers (for example, AAPL FY2024 revenue of $391.0bn and diluted EPS of $6.08).
