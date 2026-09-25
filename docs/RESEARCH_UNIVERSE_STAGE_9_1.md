# Stage 9.1 — Point-in-Time Investable Universe

Stage 9 begins the Quant Research Lab with the universe, not the signal.

A factor or strategy tested on a biased candidate set can look excellent while being economically false. The universe layer therefore makes identity, survivorship, point-in-time eligibility, and exclusion reasons explicit before any factor is calculated.

## Stable identity

Security identity is separate from ticker. Listing observations record stable security, issuer, and listing IDs plus the ticker effective during a defined interval.

A symbol change therefore changes the selected listing fact but not the stable security ID.

## Knowledge-time reconstruction

Listing revisions, market eligibility observations, and corporate actions all carry knowledge time.

Universe construction only uses facts whose knowledge time is less than or equal to the requested as-of time. Later-discovered delistings or revisions cannot leak backward into an earlier universe.

## Eligibility policy

The policy explicitly controls allowed asset classes, security types, exchanges, countries, currencies, primary-listing requirement, minimum price, minimum average daily dollar volume, minimum market capitalization, minimum trading history, market-data freshness, suspension handling, and excluded pending corporate actions.

The policy has rationale and evidence references and is content-addressed.

## Corporate actions

Known pending mergers, acquisitions, delistings, or bankruptcies can be excluded by policy. Effective terminating actions are always fail-visible.

Splits, symbol changes, and spin-offs remain recorded but are not automatically treated as terminal events.

## Decisions

Every security receives an immutable decision containing inclusion state, exact exclusion reasons, selected listing/ticker, and the exact listing, market, and corporate-action fact IDs used.

Missing market data is an exclusion, not an invitation to use a later observation.

## Persistence

InvestableUniverse manifests are content-addressed and stored idempotently in DuckDB. Research experiments can therefore pin an exact universe ID rather than a prose description such as large-cap US stocks.

## Next slice

Stage 9.2 should add factor/signal specifications that are versioned against exact universe IDs, data fields, lookback windows, lag rules, normalization transforms, and source lineage. No factor should be allowed to read information after its decision timestamp.