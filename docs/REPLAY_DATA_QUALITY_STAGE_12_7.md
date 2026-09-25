# Stage 12.7 — Replay Data-Quality and Execution-Robustness Gate

An execution simulation that looks clean but runs on a broken tape is the most dangerous kind of execution result. Stage 12.7 adds `ReplayQualityEngine`, which inspects a historical replay dataset under a frozen `ReplayQualityPolicy`. It can optionally also check the orders that will run against that dataset.

The gate **reports**. It never repairs, interpolates, forward-fills or drops data.

Crossed or locked books, and events known before they happened, are already rejected by the Stage 12.1 contracts, so they cannot reach this gate.

## Policy

`ReplayQualityPolicy` freezes:

- minimum quotes per instrument;
- maximum quote gap;
- maximum spread (bps of mid);
- maximum consecutive mid jump (bps);
- minimum displayed size;
- maximum knowledge lag (knowledge_time − event_time);
- maximum book age at order submission;
- rationale and evidence.

It is content-addressed.

## Issues and states

| Issue | State impact |
| --- | --- |
| `INSUFFICIENT_QUOTES` | UNUSABLE |
| `SEQUENCE_REGRESSION`: provider sequence goes backwards against knowledge order | UNUSABLE |
| `NO_BOOK_AT_SUBMISSION`: no quote available when an order is submitted, after market-data latency | UNUSABLE |
| `AMBIGUOUS_ARRIVAL_ORDER`: two quotes share a knowledge time | DEGRADED |
| `QUOTE_GAP` | DEGRADED |
| `WIDE_SPREAD` | DEGRADED |
| `MID_JUMP` (extreme volatility / liquidity collapse) | DEGRADED |
| `THIN_BOOK` | DEGRADED |
| `KNOWLEDGE_LAG` | DEGRADED |
| `STALE_BOOK_AT_SUBMISSION` | DEGRADED |
| `TRADE_OUTSIDE_QUOTE`: a trade print outside the latest known bid/ask, suggesting a stale or missing quote | DEGRADED |

- **CLEAN** means no issue was found.
- **DEGRADED** means the dataset may be used only with explicit human acknowledgement. The Stage 12.9 execution review turns this into `REVIEW_REQUIRED`.
- **UNUSABLE** means the replay order, or the existence of a book, cannot be trusted.

Each `ReplayQualityReport` binds the dataset, the policy, the market-data latency and the order intents it checked. It also carries a per-instrument profile (counts, worst gap, spread, jump, thinnest size, largest lag) and the full sorted list of issues. The report is content-addressed and persisted idempotently. It carries no simulation or capital authority.
