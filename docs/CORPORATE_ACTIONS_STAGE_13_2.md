# Stage 13.2 — Corporate-Action Economics, Adjustment and Total Return

Stage 13.1 fixed identity. Stage 13.2 adds what corporate actions do to prices, returns and positions.

## Events

`CorporateActionEvent` is bitemporal. It has a `record_key`, an announcement time, a knowledge time and an ex date, and later versions supersede earlier ones. Retraction is supported.

Validation depends on the kind:

| Kind | Economics |
| --- | --- |
| `CASH_DIVIDEND` | positive cash per share + currency |
| `STOCK_SPLIT` | ratio_new : ratio_old (forward or reverse; 1:1 rejected) |
| `STOCK_DIVIDEND` | ratio_new extra shares per ratio_old held |
| `SPINOFF` | child security + child shares per parent share |
| `CASH_MERGER` | cash consideration; terminates the security |
| `STOCK_MERGER` | acquirer + exchange ratio (+ optional cash); terminates |
| `DELISTING` | optional proceeds per share; terminates |

`CorporateActionLedger` is append-only DuckDB. It rejects two versions of one key at the same knowledge time and a key that moves between securities.

Every computation checks that each supplied action:

- was known at the cut-off;
- belongs to the security;
- is not a duplicate version;
- is not dated after the security terminated.

## Backward price adjustment

`adjustment_factors` has two modes:

- `SPLIT_ONLY`: factor 1/multiplier for splits and stock dividends.
- `TOTAL_RETURN`: splits and stock dividends as above, plus:
  - cash dividends, factor 1 − D/prior close;
  - spin-offs, factor 1 − ratio·child price/prior close.

A missing prior close or child price **raises**. Factors on one ex date multiply. The series is content-addressed, and `adjust()` applies the cumulative factor to prices before each ex date.

## Total return

`total_returns` computes close-to-close returns. Each action falls in the window (previous close, close]:

    r = (P_t × share multiplier + cash dividends + spin-off child value) / P_{t−1} − 1

Terminating actions end the series with a final return from proceeds:

- cash consideration;
- ratio × acquirer price + cash;
- delisting proceeds.

These are reported rather than filled, so the series is visibly incomplete:

- a missing child or acquirer price;
- missing delisting proceeds (the survivorship trap);
- prices after termination;
- a split and a distribution in the same window, which needs an explicit per-share basis.

## Positions

`apply_to_position` carries a lot through actions up to a date:

- **Splits and stock dividends** change the share count. Fractional shares are floored and the remainder reported, unless fractional shares are allowed.
- **Cash dividends** add cash.
- **Spin-offs** create a child lot. Cost-basis allocation must be supplied from evidence, keyed by event ID; it is never assumed.
- **Mergers** convert the lot to cash and/or acquirer shares.
- **A delisting without proceeds** leaves the position `unresolved`.

## Still open

- Provider feeds for actions.
- Tax treatment.
- Rights issues and tender offers.
- Wiring adjusted and total-return series into the Stage 9 research datasets.
