import unittest

from helpers import entry

from yahoo_rt.collector import Collector
from yahoo_rt.fetcher import FetchError, Page
from yahoo_rt.models import parse_jst
from yahoo_rt.parser import ParseResult, oldest_id, to_post
from yahoo_rt.store import Store


def posts(ids, when="2026-10-24 18:00"):
    return [to_post(entry(n, when)) for n in ids]


class FakeClient:
    """新しい順に並んだ投稿の列を持ち、Yahoo のように 1 ページずつ返す。"""

    def __init__(self, timeline, page_size=3, fail_older=False):
        self.timeline = timeline
        self.page_size = page_size
        self.fail_older = fail_older
        self.calls = []

    def _page(self, items):
        return Page("fake", "", ParseResult(items, oldest_id(items), True))

    def fetch_latest(self, query):
        self.calls.append(("latest", query))
        return self._page(self.timeline[: self.page_size])

    def fetch_older(self, query, oldest, start):
        self.calls.append(("older", oldest))
        if self.fail_older:
            raise FetchError("HTTP 404")
        older = [p for p in self.timeline if int(p.id) < int(oldest)]
        return self._page(older[: self.page_size])


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.logs = []

    def tearDown(self):
        self.store.close()

    def make(self, client, **kw):
        return Collector(client, self.store, ["q"], log=self.logs.append, **kw)

    def test_first_run_backfills_until_exhausted(self):
        client = FakeClient(posts(range(10, 0, -1)))
        st = self.make(client).collect_query("q")
        self.assertEqual((st.added, st.pages, st.error), (10, 4, ""))
        self.assertEqual(self.store.count(), 10)

    def test_backfill_respects_page_limit(self):
        client = FakeClient(posts(range(10, 0, -1)))
        st = self.make(client, backfill_max_pages=1).collect_query("q")
        self.assertEqual((st.added, st.pages), (6, 2))

    def test_no_backfill_when_latest_page_overlaps(self):
        client = FakeClient(posts(range(10, 0, -1)))
        c = self.make(client)
        c.collect_query("q")
        client.timeline = posts([12, 11]) + client.timeline
        client.calls.clear()
        st = c.collect_query("q")
        self.assertEqual(st.added, 2)
        self.assertEqual(client.calls, [("latest", "q")])

    def test_gap_is_filled_until_known_posts(self):
        client = FakeClient(posts(range(10, 0, -1)))
        c = self.make(client)
        c.collect_query("q")
        client.timeline = posts(range(18, 10, -1)) + client.timeline  # 8 件増えた
        st = c.collect_query("q")
        self.assertEqual(st.added, 8)
        self.assertEqual(self.store.count(), 18)
        self.assertEqual(sum(1 for k, _ in client.calls if k == "older"), 6)  # 初回4 + 今回2

    def test_since_stops_backfill(self):
        timeline = posts([6, 5, 4], "2026-10-24 18:00") + posts([3, 2, 1], "2026-10-24 08:00")
        client = FakeClient(timeline)
        st = self.make(client, since=parse_jst("2026-10-24 12:00")).collect_query("q")
        self.assertEqual(st.pages, 2)
        self.assertEqual(sum(1 for k, _ in client.calls if k == "older"), 1)

    def test_pagination_failures_disable_backfill_with_one_notice(self):
        client = FakeClient(posts(range(30, 0, -1)), fail_older=True)
        c = self.make(client)
        for i in range(4):
            client.timeline = posts(range(100 + i * 10, 90 + i * 10, -1)) + client.timeline
            st = c.collect_query("q")
            self.assertEqual(st.error, "")
        self.assertFalse(c.pagination_enabled)
        self.assertEqual(sum(1 for k, _ in client.calls if k == "older"), 3)
        self.assertEqual(len(self.logs), 1)

    def test_refresh_updates_counts_beyond_known_posts(self):
        client = FakeClient(posts(range(10, 0, -1)))
        c = self.make(client)
        c.collect_query("q")
        client.timeline = [to_post(entry(n, likesCount=100 + n)) for n in range(10, 0, -1)]
        st = c.collect_query("q", refresh=True)
        self.assertEqual((st.added, st.pages, st.error), (0, 4, ""))
        likes = {int(p.id) % 1000: p.likes for p in self.store.load_posts()}
        self.assertEqual(likes[1], 101)  # 最後のページの投稿まで更新された

    def test_refresh_stops_at_since_and_reports_progress(self):
        timeline = posts(range(60, 30, -1), "2026-10-24 18:00") + posts(range(30, 0, -1), "2026-10-24 08:00")
        client = FakeClient(timeline)
        c = self.make(client, since=parse_jst("2026-10-24 12:00"))
        st = c.collect_query("q", refresh=True)
        self.assertEqual(st.pages, 11)  # 18:00 の 30 件 + 08:00 の最初の 3 件で止まる
        self.assertEqual(len(self.logs), 1)  # 10 ページごとの途中経過

    def test_refresh_reports_pagination_failure(self):
        client = FakeClient(posts(range(10, 0, -1)), fail_older=True)
        st = self.make(client).collect_query("q", refresh=True)
        self.assertIn("さかのぼり取得が途中で止まりました", st.error)
        self.assertEqual(st.added, 3)

    def test_latest_failure_is_reported_not_raised(self):
        class Broken(FakeClient):
            def fetch_latest(self, query):
                raise FetchError("接続できませんでした")

        st = self.make(Broken([])).collect_query("q")
        self.assertIn("接続できません", st.error)

    def test_unrecognized_page(self):
        class Blank(FakeClient):
            def fetch_latest(self, query):
                return Page("fake", "<html></html>", ParseResult())

        st = self.make(Blank([])).collect_query("q")
        self.assertIn("doctor", st.error)


if __name__ == "__main__":
    unittest.main()
