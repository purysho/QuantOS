# Stage 13.3 — Exchange Session Calendars

QuantOS now owns a frozen, rule-based session calendar. It treats the `exchange_calendars` package (Apache-2.0, now a core dependency) as an external engine to compare against, never as the source of truth.

## Artifact

A `SessionCalendar` is content-addressed. It holds:

- the exchange MIC and IANA time zone;
- its source (`QUANTOS_RULES` or `EXCHANGE_CALENDARS`) and source version;
- a frozen date range;
- the exact sessions, each with a local date, UTC open, UTC close and early-close flag.

Queries outside the range fail closed. Asking for a non-session date raises. Helpers are provided for navigation: `is_session`, `session`, `sessions_between`, `previous_session` and `next_session`.

## XNYS rules (frozen 2010–2030)

**Hours:** 09:30–16:00 America/New_York. UTC times are computed DST-aware through `zoneinfo`.

**Holidays:**

- New Year's Day (a Saturday New Year is *not* observed on the Friday);
- Martin Luther King Jr. Day;
- Washington's Birthday;
- Good Friday (from the Gregorian Easter computation);
- Memorial Day;
- Juneteenth (from 2022);
- Independence Day;
- Labor Day;
- Thanksgiving;
- Christmas.

Other Saturday holidays move to the Friday before, and Sunday holidays move to the Monday after.

**Early closes (13:00):**

- July 3;
- the day after Thanksgiving;
- Christmas Eve.

**Unscheduled closures** can't be derived from rules. They sit in an explicit table with evidence references: Hurricane Sandy (2012-10-29 and 30), and the national days of mourning on 2018-12-05 and 2025-01-09.

## Differential

`compare_calendars` reports:

- sessions missing on either side;
- sessions whose open, close or early-close flag differ.

Over 2010-01-04 to 2030-12-31, the QuantOS rules match `exchange_calendars` exactly on all 5,279 sessions. A closure missing from the unscheduled table would show up as a MISMATCH, not be silently absorbed.

## Not yet in scope

- Other exchanges. Add a rule builder per MIC and compare it the same way.
- Half-day auction details.
- Intraday halts.
