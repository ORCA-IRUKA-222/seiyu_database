import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import entry
from test_collector import FakeClient

from yahoo_rt.models import parse_jst
from yahoo_rt.parser import to_post
from yahoo_rt.report import write_hits_csv
from yahoo_rt.search import Form, build_config, run_search, safe_name


def form(**kw):
    base = dict(name="声グラハロウィーン", keywords="声グラハロウィーン", since="2026-10-24 00:00")
    base.update(kw)
    return Form(**base)


class BuildConfigTest(unittest.TestCase):
    def test_expands_queries_and_lists(self):
        cfg, notes = build_config(form(exclude_users="@seigura、@other", exclude_words="ネタバレ 告知",
                                       focus="薬師寺李有 = 李有\n青木陽菜"), Path("/tmp/app"))
        self.assertEqual(len(cfg.queries), 4)
        self.assertIn("声優グランプリ ハロウィン", cfg.queries)
        self.assertEqual(cfg.exclude_users, ["@seigura", "@other"])
        self.assertEqual(cfg.exclude_words, ["ネタバレ", "告知"])
        self.assertEqual(cfg.keywords, {"薬師寺李有": ["薬師寺李有", "李有"], "青木陽菜": ["青木陽菜"]})
        self.assertEqual(cfg.resolve(cfg.db), Path("/tmp/app/data/声グラハロウィーン.sqlite3"))
        self.assertEqual(notes, [])

    def test_input_errors_are_explained(self):
        for kw, message in [
            (dict(keywords=" \n "), "キーワード"),
            (dict(since=""), "始まり"),
            (dict(since="10/24"), "日時の形式"),
            (dict(until="2026-10-23 23:00"), "後の日時"),
        ]:
            with self.assertRaises(ValueError) as cm:
                build_config(form(**kw))
            self.assertIn(message, str(cm.exception))

    def test_safe_name(self):
        self.assertEqual(safe_name(' 声グラ/ハロウィーン:2026? '), "声グラ_ハロウィーン_2026")
        self.assertEqual(safe_name("..."), "search")


class RunSearchTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.logs, self.results = [], []
        timeline = [to_post(entry(n, "2026-10-24 18:00", likesCount=n)) for n in range(12, 2, -1)]
        timeline[0] = to_post(entry(12, "2026-10-24 18:30", text="【交換】缶バッジ 譲：A 求：B"))
        timeline[1] = to_post(entry(11, "2026-10-24 18:20", text="物販のお知らせ", screenName="seigura"))
        timeline += [to_post(entry(n, "2026-10-23 20:00")) for n in (2, 1)]  # 期間より前
        self.client = FakeClient(timeline, page_size=4)

    def run_search(self, cfg, **kw):
        kw.setdefault("follow", False)
        kw.setdefault("should_stop", lambda: False)
        run_search(cfg, log=self.logs.append, on_results=self.results.append, client=self.client,
                   sleep=lambda s: None, **kw)

    def test_collects_period_and_writes_outputs(self):
        cfg, _ = build_config(form(keywords="声グラ", synonyms="", exclude_users="@seigura"), self.dir)
        self.run_search(cfg)
        self.assertEqual(len(self.results), 1)
        r = self.results[0]
        self.assertEqual((r.total, r.kept), (10, 8))
        reasons = {p.id[-2:]: reason for p, reason in r.classified if reason}
        self.assertEqual(reasons, {"12": "取引の投稿", "11": "除外アカウント"})
        self.assertTrue(r.report.exists())
        hits = list(csv.DictReader((r.report.parent / "all_hits.csv").read_text(encoding="utf-8-sig").splitlines()))
        self.assertEqual(len(hits), 10)
        self.assertEqual({h["除外した理由"] for h in hits}, {"", "取引の投稿", "除外アカウント"})
        self.assertEqual(hits[0]["一致した検索語"], "声グラ")

    def test_follow_stops_at_end_of_period(self):
        cfg, _ = build_config(form(keywords="声グラ", synonyms="", until="2026-10-24 23:00"), self.dir)
        self.run_search(cfg, follow=True, now=lambda: parse_jst("2026-10-25 00:00"))
        self.assertEqual(len(self.results), 1)
        self.assertIn("期間の終わり", self.logs[-1])

    def test_follow_polls_until_stopped(self):
        cfg, _ = build_config(form(keywords="声グラ", synonyms="", interval=20), self.dir)
        latest_calls = lambda: sum(1 for c in self.client.calls if c[0] == "latest")  # noqa: E731
        self.run_search(cfg, follow=True, should_stop=lambda: latest_calls() >= 3)
        self.assertEqual(latest_calls(), 3)
        self.assertEqual(len(self.results), 3)  # 最初の検索 + 定期取得 2 回

    def test_write_hits_csv_anonymized(self):
        cfg, _ = build_config(form(keywords="声グラ", synonyms=""), self.dir)
        self.run_search(cfg)
        path = write_hits_csv(self.dir / "out" / "hits.csv", self.results[0].classified, anonymize=True)
        text = path.read_text(encoding="utf-8-sig")
        self.assertNotIn("twitter.com", text)
        self.assertIn("user0001", text)


if __name__ == "__main__":
    unittest.main()
