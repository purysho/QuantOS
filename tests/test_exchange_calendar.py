import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from quantos.exchange_calendar import (
    CalendarDifferentialState,
    ExchangeCalendarsAdapter,
    XNYSRuleCalendarBuilder,
    compare_calendars,
    session_calendar_identity,
)

UTC = timezone.utc


def xnys(start=date(2020, 1, 2), end=date(2026, 12, 31)):
    return XNYSRuleCalendarBuilder().build(start=start, end=end)


class XNYSRuleTests(unittest.TestCase):
    def test_holiday_rules(self):
        cal = xnys()
        for holiday in (
            date(2024, 3, 29),   # Good Friday
            date(2022, 6, 20),   # Juneteenth observed Monday
            date(2021, 7, 5),    # Independence Day observed Monday
            date(2022, 12, 26),  # Christmas observed Monday
            date(2024, 11, 28),  # Thanksgiving
            date(2025, 1, 9),    # unscheduled closure
        ):
            self.assertFalse(cal.is_session(holiday), holiday)
        self.assertTrue(cal.is_session(date(2021, 6, 18)))  # before Juneteenth existed
        self.assertTrue(cal.is_session(date(2021, 12, 31)))  # Saturday New Year not observed

    def test_early_closes_and_dst_aware_utc_times(self):
        cal = xnys()
        for day in (date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24)):
            session = cal.session(day)
            self.assertTrue(session.early_close)
        self.assertEqual(cal.session(date(2024, 7, 3)).close_utc, datetime(2024, 7, 3, 17, tzinfo=UTC))
        self.assertEqual(cal.session(date(2024, 7, 8)).close_utc, datetime(2024, 7, 8, 20, tzinfo=UTC))
        self.assertEqual(cal.session(date(2024, 1, 8)).close_utc, datetime(2024, 1, 8, 21, tzinfo=UTC))
        self.assertEqual(cal.session(date(2024, 1, 8)).open_utc, datetime(2024, 1, 8, 14, 30, tzinfo=UTC))

    def test_navigation_and_range_fail_closed(self):
        cal = xnys()
        self.assertEqual(cal.previous_session(date(2024, 7, 5)).session_date, date(2024, 7, 3))
        self.assertEqual(cal.next_session(date(2024, 7, 3)).session_date, date(2024, 7, 5))
        self.assertEqual(len(cal.sessions_between(date(2024, 7, 1), date(2024, 7, 5))), 4)
        with self.assertRaises(ValueError):
            cal.is_session(date(2019, 12, 31))
        with self.assertRaises(ValueError):
            XNYSRuleCalendarBuilder().build(start=date(2009, 1, 1), end=date(2010, 1, 1))
        with self.assertRaises(ValueError):
            cal.session(date(2024, 7, 4))

    def test_identity(self):
        cal = xnys()
        self.assertEqual(cal.calendar_id, session_calendar_identity(cal))
        self.assertEqual(cal, xnys())


class ExchangeCalendarsDifferentialTests(unittest.TestCase):
    def test_rules_match_exchange_calendars_over_full_frozen_range(self):
        start, end = date(2010, 1, 4), date(2030, 12, 31)
        reference = XNYSRuleCalendarBuilder().build(start=start, end=end)
        external = ExchangeCalendarsAdapter().build(mic="XNYS", start=start, end=end)
        differential = compare_calendars(reference, external)
        self.assertEqual(differential.state, CalendarDifferentialState.MATCH, differential)
        self.assertEqual(differential.trust_authority, "REFERENCE_MATCH_ONLY")

    def test_missing_unscheduled_closure_is_a_visible_mismatch(self):
        start, end = date(2025, 1, 2), date(2025, 1, 31)
        external = ExchangeCalendarsAdapter().build(mic="XNYS", start=start, end=end)
        reference = xnys(start, end)
        from quantos.exchange_calendar import _calendar, XNYS_UNSCHEDULED_CLOSURES  # noqa: F401
        extra = replace(external.sessions[0], session_date=date(2025, 1, 9))
        tampered = _calendar(
            mic="XNYS",
            timezone_name=external.timezone_name,
            source=external.source,
            source_version=external.source_version,
            start=start,
            end=end,
            sessions=tuple(sorted(external.sessions + (extra,), key=lambda s: s.session_date)),
        )
        differential = compare_calendars(reference, tampered)
        self.assertEqual(differential.state, CalendarDifferentialState.MISMATCH)
        self.assertEqual(differential.missing_in_reference, (date(2025, 1, 9),))


if __name__ == "__main__":
    unittest.main()
