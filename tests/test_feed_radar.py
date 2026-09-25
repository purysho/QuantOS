import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import duckdb

from quantos.adapters.crossref_radar import CrossrefRadarAdapter, CrossrefRadarError
from quantos.adapters.feed_radar import (
    FEED_REGISTRY,
    FEEDS_BY_ID,
    FeedRadarAdapter,
    FeedRadarError,
    parse_feed,
)
from quantos.radar_cli import scan_feeds, scan_ssrn
from quantos.security import DEFAULT_EGRESS_ALLOWLIST

UTC = timezone.utc
FETCHED = datetime(2026, 9, 25, 12, tzinfo=UTC)

NBER = b"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"><channel><title>NBER</title>
<item><title>Factor Crowding and Returns -- by Ada Quant, Bo Risk</title>
<description>We study crowding.</description>
<link>https://www.nber.org/papers/w35758#fromrss</link>
<guid>https://www.nber.org/papers/w35758#fromrss</guid></item>
<item><title>Second Paper -- by C. Author</title><description>x</description>
<link>https://www.nber.org/papers/w35759#fromrss</link></item>
</channel></rss>"""

FED = b"""<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel><title>FEDS</title>
<item><title>FEDS Paper: Liquidity Buffers</title>
<link><![CDATA[https://www.federalreserve.gov/econres/feds/liquidity-buffers.htm]]></link>
<description><![CDATA[<a href="https://x">Ann Econ</a>, Ben Model and Cy Stat<br><br>We measure &amp; model buffers.]]></description>
<category>FEDS Paper</category>
<pubDate><![CDATA[Mon, 21 Sep 2026 15:15:00 -0400]]></pubDate></item>
</channel></rss>"""

BIS = b"""<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel rdf:about="https://www.bis.org"><title>BIS</title></channel>
<item rdf:about="https://www.bis.org/publications/work1379.htm">
<title>Global imbalances</title><link>https://www.bis.org/publications/work1379.htm</link>
<description>This paper analyses imbalances.</description>
<dc:creator>Stefan Avdjiev</dc:creator><dc:creator>Kristin J Forbes</dc:creator>
<dc:date>2026-09-23T00:00:00Z</dc:date></item>
</rdf:RDF>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>ECB</title>
<entry><title>Monetary policy statement</title><link href="https://www.ecb.europa.eu//press/pr/2026/html/x.en.html"/>
<id>tag:ecb,2026:x</id><updated>2026-09-24T14:00:00+02:00</updated>
<author><name>ECB Press</name></author><summary>Statement.</summary></entry>
</feed>"""


class ParsingTests(unittest.TestCase):
    def test_nber_items_get_paper_number_identity_authors_and_first_seen_time(self):
        items = parse_feed(NBER, source=FEEDS_BY_ID["nber-wp"], fetched_at=FETCHED, feed_artifact_id="a1")
        self.assertEqual([i.canonical_id for i in items], ["nber:w35758", "nber:w35759"])
        first = items[0]
        self.assertEqual(first.title, "Factor Crowding and Returns")
        self.assertEqual(first.authors, ("Ada Quant", "Bo Risk"))
        self.assertEqual(first.published_at, FETCHED)
        self.assertIn("published-precision:first-seen", first.categories)
        self.assertIn("kind:WORKING_PAPER", first.categories)
        self.assertEqual(first.provider, "nber-wp")

    def test_first_seen_time_is_reused_so_rescans_are_idempotent(self):
        earlier = FETCHED - timedelta(days=3)
        items = parse_feed(NBER, source=FEEDS_BY_ID["nber-wp"], fetched_at=FETCHED, feed_artifact_id=None,
                           first_seen={"nber:w35758": earlier})
        self.assertEqual(items[0].published_at, earlier)
        again = parse_feed(NBER, source=FEEDS_BY_ID["nber-wp"], fetched_at=FETCHED + timedelta(hours=1),
                           feed_artifact_id=None, first_seen={"nber:w35758": earlier})
        self.assertEqual(items[0].discovery_id, again[0].discovery_id)

    def test_fed_paper_authors_and_timezone_normalization(self):
        (item,) = parse_feed(FED, source=FEEDS_BY_ID["fed-feds"], fetched_at=FETCHED, feed_artifact_id=None)
        self.assertEqual(item.authors, ("Ann Econ", "Ben Model", "Cy Stat"))
        self.assertEqual(item.summary, "We measure & model buffers.")
        self.assertEqual(item.published_at, datetime(2026, 9, 21, 19, 15, tzinfo=UTC))
        self.assertIn("subject:FEDS Paper", item.categories)

    def test_rss1_rdf_and_atom(self):
        (bis,) = parse_feed(BIS, source=FEEDS_BY_ID["bis-wp"], fetched_at=FETCHED, feed_artifact_id=None)
        self.assertEqual(bis.authors, ("Stefan Avdjiev", "Kristin J Forbes"))
        self.assertEqual(bis.published_at, datetime(2026, 9, 23, tzinfo=UTC))
        (ecb,) = parse_feed(ATOM, source=FEEDS_BY_ID["ecb-press"], fetched_at=FETCHED, feed_artifact_id=None)
        self.assertEqual(ecb.canonical_id, "ecb-press:https://www.ecb.europa.eu/press/pr/2026/html/x.en.html")
        self.assertEqual(ecb.published_at, datetime(2026, 9, 24, 12, tzinfo=UTC))
        self.assertEqual(ecb.authors, ("ECB Press",))

    def test_unsafe_or_malformed_feeds_fail_closed(self):
        source = FEEDS_BY_ID["sec-press"]
        bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
        cases = {
            "doctype": bomb,
            "empty": b"",
            "not xml": b"<rss><channel>",
            "html page": b"<html><body>moved</body></html>",
            "undated tz": b"<rss><channel><item><title>t</title><link>https://www.sec.gov/x</link><pubDate>2026-09-23T10:00:00</pubDate></item></channel></rss>",
            "no link": b"<rss><channel><item><title>t</title></item></channel></rss>",
            "bad scheme": b"<rss><channel><item><title>t</title><link>javascript:alert(1)</link></item></channel></rss>",
        }
        for name, body in cases.items():
            with self.subTest(name), self.assertRaises(FeedRadarError):
                parse_feed(body, source=source, fetched_at=FETCHED, feed_artifact_id=None)

    def test_registry_hosts_are_on_the_egress_allowlist(self):
        for source in FEED_REGISTRY:
            self.assertIn(urlparse(source.url).hostname, DEFAULT_EGRESS_ALLOWLIST, source.source_id)
        self.assertEqual(len({s.source_id for s in FEED_REGISTRY}), len(FEED_REGISTRY))


class AdapterTests(unittest.TestCase):
    def test_only_registered_feeds_with_contact_user_agent_and_throttle(self):
        calls, sleeps = [], []
        clock = iter([0.0, 0.2, 0.4])

        def transport(url, headers, timeout):
            calls.append((url, headers["user-agent"]))
            return NBER, "application/rss+xml"

        with self.assertRaises(FeedRadarError):
            FeedRadarAdapter(user_agent="anonymous")
        adapter = FeedRadarAdapter(user_agent="QuantOS research@example.com", transport=transport,
                                   clock=lambda: next(clock), sleeper=sleeps.append)
        with self.assertRaises(FeedRadarError):
            adapter.fetch(source_id="https://evil.example/feed")
        adapter.fetch(source_id="nber-wp", max_items=1)
        fetched = adapter.fetch(source_id="nber-wp", max_items=1)
        self.assertEqual(len(fetched.items), 1)
        self.assertEqual(calls[0], ("https://back.nber.org/rss/new.xml", "QuantOS research@example.com"))
        self.assertEqual(len(sleeps), 1)

    def test_scan_feeds_is_idempotent_for_undated_items(self):
        class Fake:
            def fetch(self, *, source_id, max_items, first_seen, artifact_store):
                from quantos.adapters.feed_radar import FeedRadarFetch

                now = datetime.now(UTC)
                items = parse_feed(NBER, source=FEEDS_BY_ID[source_id], fetched_at=now,
                                   feed_artifact_id=None, first_seen=first_seen)
                return FeedRadarFetch(FEEDS_BY_ID[source_id], FEEDS_BY_ID[source_id].url, now, items, None)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kwargs = dict(
                source_ids=("nber-wp",), max_items=10, radar_db=str(root / "radar.duckdb"),
                artifact_root=str(root / "a"), artifact_db=str(root / "a.duckdb"),
                triage_db=str(root / "t.duckdb"), review_db=str(root / "r.duckdb"),
                queue_threshold=1.0, adapter=Fake(),
            )
            scan_feeds(**kwargs)
            scan_feeds(**kwargs)
            con = duckdb.connect(str(root / "radar.duckdb"))
            rows = con.execute("SELECT provider, canonical_id FROM radar_items ORDER BY 2").fetchall()
            con.close()
        self.assertEqual(rows, [("nber-wp", "nber:w35758"), ("nber-wp", "nber:w35759")])


    def test_rescan_of_a_republished_feed_keeps_the_first_sighting(self):
        # BIS re-publishes its feed with new bytes while the item is unchanged.
        bodies = [BIS, BIS.replace(b"<title>BIS</title>", b"<title>BIS working papers</title>")]

        class Fake:
            def fetch(self, *, source_id, max_items, first_seen, artifact_store):
                from quantos.adapters.feed_radar import FeedRadarFetch

                now = datetime.now(UTC)
                body = bodies.pop(0)
                artifact = artifact_store.put(source_uri=FEEDS_BY_ID[source_id].url, content=body,
                                              fetched_at=now, media_type="application/xml")
                items = parse_feed(body, source=FEEDS_BY_ID[source_id], fetched_at=now,
                                   feed_artifact_id=artifact.artifact_id, first_seen=first_seen)
                return FeedRadarFetch(FEEDS_BY_ID[source_id], FEEDS_BY_ID[source_id].url, now, items, artifact)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kwargs = dict(
                source_ids=("bis-wp",), max_items=10, radar_db=str(root / "radar.duckdb"),
                artifact_root=str(root / "a"), artifact_db=str(root / "a.duckdb"),
                triage_db=str(root / "t.duckdb"), review_db=str(root / "r.duckdb"),
                queue_threshold=0.0, adapter=Fake(),
            )
            scan_feeds(**kwargs)
            scan_feeds(**kwargs)
            radar = duckdb.connect(str(root / "radar.duckdb"))
            items = radar.execute("SELECT feed_artifact_id FROM radar_items").fetchall()
            radar.close()
            queue = duckdb.connect(str(root / "r.duckdb"))
            queued = queue.execute("SELECT feed_artifact_id FROM research_review_queue").fetchall()
            queue.close()
            archive = duckdb.connect(str(root / "a.duckdb"))
            archived = archive.execute("SELECT count(*) FROM source_artifacts").fetchone()[0]
            archive.close()
        self.assertEqual(len(items), 1)
        self.assertEqual(queued, items, "the review queue keeps the first sighting too")
        self.assertEqual(archived, 2, "every fetched feed version stays archived")


class SSRNTests(unittest.TestCase):
    def test_ssrn_url_filters_prefix_type_and_posted_date_and_requires_query(self):
        url = CrossrefRadarAdapter.build_ssrn_url(
            from_posted_date=date(2026, 9, 1), query="factor momentum", max_results=20, mailto="ops@example.com"
        )
        params = parse_qs(urlparse(url).query)
        self.assertEqual(
            params["filter"], ["prefix:10.2139,type:posted-content,from-posted-date:2026-09-01"]
        )
        self.assertEqual(params["query.bibliographic"], ["factor momentum"])
        with self.assertRaises(CrossrefRadarError):
            CrossrefRadarAdapter.build_ssrn_url(from_posted_date=date(2026, 9, 1), query=" ",
                                                max_results=20, mailto="ops@example.com")

    def test_posted_content_uses_posted_date_and_repository_tag(self):
        body = json.dumps({"status": "ok", "message-type": "work-list", "message": {"items": [{
            "DOI": "10.2139/ssrn.5555555", "type": "posted-content", "title": ["Crowded Factors"],
            "author": [{"given": "Ada", "family": "Quant"}], "posted": {"date-parts": [[2026, 9, 10]]},
            "indexed": {"date-time": "2026-09-24T10:11:12Z"}, "group-title": "SSRN",
        }]}}).encode()
        (item,) = CrossrefRadarAdapter.parse_response(body, fetched_at=FETCHED, feed_artifact_id=None)
        self.assertEqual(item.published_at, datetime(2026, 9, 10, tzinfo=UTC))
        self.assertIn("repository:SSRN", item.categories)
        self.assertIn("published-precision:day", item.categories)

    def test_scan_ssrn_enters_radar(self):
        body = json.dumps({"status": "ok", "message-type": "work-list", "message": {"items": [{
            "DOI": "10.2139/ssrn.1", "title": ["T"], "posted": {"date-parts": [[2026, 9]]},
            "indexed": {"date-time": "2026-09-24T10:11:12Z"},
        }]}}).encode()
        seen = []

        def transport(url, headers, timeout):
            seen.append(url)
            return body, "application/json"

        adapter = CrossrefRadarAdapter(mailto="ops@example.com", user_agent="QuantOS", transport=transport)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ranked = scan_ssrn(
                from_posted_date=date(2026, 9, 1), query="momentum", max_results=5,
                radar_db=str(root / "radar.duckdb"), artifact_root=str(root / "a"),
                artifact_db=str(root / "a.duckdb"), triage_db=str(root / "t.duckdb"),
                review_db=str(root / "r.duckdb"), adapter=adapter,
            )
        self.assertEqual(len(ranked), 1)
        self.assertIn("prefix%3A10.2139", seen[0])


@unittest.skipUnless(os.environ.get("QUANTOS_LIVE_FEEDS") == "1", "set QUANTOS_LIVE_FEEDS=1 for the live smoke check")
class LiveFeedSmokeTests(unittest.TestCase):
    def test_every_registered_feed_parses(self):
        adapter = FeedRadarAdapter(user_agent="First Current Quant OS smoke (+https://github.com/purysho/First-Current-Quant-OS-prototype)")
        for source in FEED_REGISTRY:
            with self.subTest(source.source_id):
                fetched = adapter.fetch(source_id=source.source_id, max_items=5)
                self.assertTrue(fetched.items)


if __name__ == "__main__":
    unittest.main()
