import unittest

from helpers import entry

from yahoo_rt import textutil
from yahoo_rt.analyze import analyze, build_timeline, choose_bucket_minutes, filter_posts, match_groups
from yahoo_rt.config import Config
from yahoo_rt.models import parse_jst
from yahoo_rt.parser import to_post


def post(n, text, when="2026-10-24 18:00", **kw):
    return to_post(entry(n, when, text=text, **kw))


class UseFallbackTokenizer(unittest.TestCase):
    """janome の有無で結果が変わらないよう、簡易抽出に固定する。"""

    def setUp(self):
        self._saved = textutil._tokenizer, textutil._tokenizer_checked
        textutil._tokenizer, textutil._tokenizer_checked = None, True

    def tearDown(self):
        textutil._tokenizer, textutil._tokenizer_checked = self._saved


class FilterTest(unittest.TestCase):
    def test_exclusions(self):
        cfg = Config(exclude_users=["@Official"], exclude_words=["譲渡"])
        posts = [
            post(1, "楽しかった！ https://t.co/aaa"),
            post(2, "楽しかった！  https://t.co/bbb"),  # URL と空白だけ違う重複
            post(3, "チケット譲渡します"),
            post(4, "RT @someone: 楽しかった"),
            post(5, "告知です", screenName="official"),
            post(6, "ｶﾜｲｲ"),
        ]
        kept, excluded = filter_posts(posts, cfg)
        self.assertEqual([p.text for p in kept], ["楽しかった！ https://t.co/aaa", "ｶﾜｲｲ"])
        self.assertEqual(
            dict(excluded), {"同じ本文の重複": 1, "除外ワード": 1, "リポスト": 1, "除外アカウント": 1}
        )


class TimelineTest(unittest.TestCase):
    def test_buckets_align_to_jst_and_fill_gaps(self):
        posts = [post(1, "a", "2026-10-24 18:03"), post(2, "b", "2026-10-24 18:09"), post(3, "c", "2026-10-24 18:31")]
        tl = build_timeline(posts, 10)
        self.assertEqual(tl[0][0], parse_jst("2026-10-24 18:00"))
        self.assertEqual([n for _, n in tl], [2, 0, 0, 1])

    def test_bucket_widens_for_long_periods(self):
        self.assertEqual(choose_bucket_minutes(3 * 3600, 10), 10)
        self.assertEqual(choose_bucket_minutes(7 * 24 * 3600, 10), 60)
        self.assertEqual(choose_bucket_minutes(0, 7), 7)


class GroupsTest(unittest.TestCase):
    def test_matching_ignores_width_and_case(self):
        posts = [post(1, "ｶﾜｲｲ!!", likesCount=5), post(2, "かっこいい"), post(3, "SHIMONO最高")]
        groups = match_groups(posts, {"かわいい": ["カワイイ"], "出演者": ["shimono"], "空": []})
        by = {g.label: g for g in groups}
        self.assertEqual(by["かわいい"].count, 1)
        self.assertEqual(by["出演者"].count, 1)
        self.assertEqual(by["空"].terms, ["空"])  # 語が空ならラベル自体で照合
        self.assertAlmostEqual(by["かわいい"].share, 1 / 3)
        self.assertEqual(groups[0].count, 1)


class AnalyzeTest(UseFallbackTokenizer):
    def test_full_analysis(self):
        cfg = Config(
            event_name="声グラハロウィーン",
            queries=["声グラハロウィーン"],
            keywords={"仮装": ["仮装", "コスプレ"]},
            reactions={"最高": ["最高"], "感謝": ["ありがとう"]},
        )
        posts = [
            post(1, "声グラハロウィーン最高！仮装のクオリティ🎃🎃 #声グラハロウィーン", "2026-10-24 18:05", likesCount=30),
            post(2, "コスプレ衣装が最高でした🎃 #声グラハロウィーン #ハロウィン", "2026-10-24 18:12", likesCount=5),
            post(3, "ありがとうございました 衣装 #声グラハロウィーン", "2026-10-24 19:40", likesCount=0),
        ]
        a = analyze(posts, cfg)
        self.assertEqual((a.total, a.unique_users, a.bucket_minutes), (3, 3, 10))
        self.assertEqual(a.peak, (parse_jst("2026-10-24 18:00"), 1))
        self.assertEqual(a.hashtags[0], ("#声グラハロウィーン", 3))
        self.assertEqual(dict(a.emoji)["🎃"], 2)  # 1投稿で何回使っても1件
        words = dict(a.words)
        self.assertEqual(words["衣装"], 2)
        self.assertNotIn("ハロウィーン", words)  # 検索語は除く
        self.assertNotIn("声グラハロウィーン", words)
        self.assertEqual({g.label: g.count for g in a.reactions}, {"最高": 2, "感謝": 1})
        self.assertEqual(a.keywords[0].count, 2)
        self.assertEqual([p.likes for p in a.top_posts], [30, 5])  # 反応ゼロは載せない

    def test_empty(self):
        a = analyze([], Config())
        self.assertEqual((a.total, a.timeline, a.peak), (0, [], None))


if __name__ == "__main__":
    unittest.main()
