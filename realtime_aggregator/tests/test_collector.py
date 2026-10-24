import unittest

from helpers import entry

from yahoo_rt.collector import Collector
from yahoo_rt.fetcher import FetchError, Page
from yahoo_rt.models import parse_jst
from yahoo_rt.parser import ParseResult, oldest_id, to_post
from yahoo_rt.store import Store


def posts(ids, when="2026-10-24 18:00", **kw):
    return [to_post(entry(n, when, **kw)) for n in ids]


class FakeClient:
    """新しい順の投稿の列を持ち、Yahoo のように 1 ページずつ返す。

    model="cursor": 起点 ID より古い投稿を、start 件目（省略時は 1 件目）から返す。
    model="offset": 起点 ID を無視し、全体の start 件目から返す。
    model="skip":   起点 ID より古い投稿を、先頭の 1 件を飛ばして返す（重なりが確認できない）。
    """

    def __init__(self, timeline, page_size=3, model="cursor", fail_older=False, extra=()):
        self.timeline = timeline
        self.page_size = page_size
        self.model = model
        self.fail_older = fail_older
        self.extra = list(extra)
        self.calls = []

    def _page(self, items, extra=()):
        return Page("fake", "", ParseResult([*items, *extra], oldest_id(items), True, list(items)))

    def fetch_latest(self, query):
        self.calls.append(("latest", query))
        return self._page(self.timeline[: self.page_size], self.extra)

    def fetch_older(self, query, oldest, start=None):
        self.calls.append(("older", oldest, start))
        if self.fail_older:
            raise FetchError("HTTP 404")
        if self.model == "offset":
            pool = self.timeline
        else:
            pool = [p for p in self.timeline if int(p.id) < int(oldest)]
            if self.model == "skip":
                pool = pool[1:]
        s = (start or 1) - 1 if self.model != "skip" else 0
        return self._page(pool[s: s + self.page_size])


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.logs = []

    def tearDown(self):
        self.store.close()

    def make(self, client, **kw):
        return Collector(client, self.store, ["q"], log=self.logs.append, **kw)

    def older_calls(self, client):
        return sum(1 for c in client.calls if c[0] == "older")

    def test_first_run_collects_every_post_when_server_uses_cursor(self):
        client = FakeClient(posts(range(10, 0, -1)), model="cursor")
        c = self.make(client)
        st = c.collect_query("q")
        self.assertEqual((st.added, st.error, st.gap_suspected), (10, "", False))
        self.assertEqual(c.page_mode, "cursor")
        self.assertTrue(all(call[2] is None for call in client.calls if call[0] == "older"))

    def test_first_run_collects_every_post_when_server_uses_offset(self):
        client = FakeClient(posts(range(10, 0, -1)), model="offset")
        c = self.make(client)
        st = c.collect_query("q")
        self.assertEqual((st.added, st.gap_suspected), (10, False))
        self.assertEqual(c.page_mode, "offset")

    def test_skipped_posts_are_reported(self):
        client = FakeClient(posts(range(10, 0, -1)), model="skip")
        st = self.make(client).collect_query("q")
        self.assertTrue(st.gap_suspected)
        self.assertEqual(len(self.logs), 1)
        self.assertIn("飛ばした可能性", self.logs[0])

    def test_backfill_respects_page_limit(self):
        client = FakeClient(posts(range(10, 0, -1)))
        st = self.make(client, backfill_max_pages=1).collect_query("q")
        self.assertEqual((st.added, st.pages), (5, 2))  # 最新 3 件 + 重なり 1 件を除く 2 件

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
        client.calls.clear()
        st = c.collect_query("q")
        self.assertEqual(st.added, 8)
        self.assertEqual(self.store.count(), 18)
        self.assertEqual(self.older_calls(client), 3)

    def test_since_stops_backfill_and_best_post_does_not(self):
        timeline = posts([6, 5, 4], "2026-10-24 18:00") + posts([3, 2, 1], "2026-10-24 08:00")
        best = posts([99], "2026-10-23 10:00")  # 一覧の外にある前日のベストポスト
        client = FakeClient(timeline, extra=best)
        st = self.make(client, since=parse_jst("2026-10-24 12:00")).collect_query("q")
        self.assertEqual(st.pages, 2)
        self.assertEqual(self.older_calls(client), 1)

    def test_pagination_failures_disable_backfill_with_one_notice(self):
        client = FakeClient(posts(range(30, 0, -1)), fail_older=True)
        c = self.make(client)
        for i in range(4):
            client.timeline = posts(range(100 + i * 10, 90 + i * 10, -1)) + client.timeline
            st = c.collect_query("q")
            self.assertEqual(st.error, "")
        self.assertFalse(c.pagination_enabled)
        self.assertEqual(self.older_calls(client), 3)
        self.assertEqual(len(self.logs), 1)

    def test_stop_request_ends_walk(self):
        client = FakeClient(posts(range(30, 0, -1)))
        st = self.make(client, should_stop=lambda: self.older_calls(client) >= 2).collect_query("q")
        self.assertEqual(self.older_calls(client), 2)
        self.assertEqual(st.added, 7)

    def test_refresh_updates_counts_beyond_known_posts(self):
        client = FakeClient(posts(range(10, 0, -1)))
        c = self.make(client)
        c.collect_query("q")
        client.timeline = [to_post(entry(n, likesCount=100 + n)) for n in range(10, 0, -1)]
        st = c.collect_query("q", refresh=True)
        self.assertEqual((st.added, st.pages, st.error), (0, 5, ""))
        likes = {int(p.id) % 1000: p.likes for p in self.store.load_posts()}
        self.assertEqual(likes[1], 101)  # 最後のページの投稿まで更新された

    def test_refresh_stops_at_since_and_reports_progress(self):
        timeline = posts(range(60, 30, -1), "2026-10-24 18:00") + posts(range(30, 0, -1), "2026-10-24 08:00")
        client = FakeClient(timeline)
        st = self.make(client, since=parse_jst("2026-10-24 12:00")).collect_query("q", refresh=True)
        self.assertEqual(st.pages, 15)  # 1 ページ 2 件ずつ進み、08:00 の投稿に届いたところで止まる
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
