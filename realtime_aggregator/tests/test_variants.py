import unittest

from yahoo_rt.variants import expand_queries, parse_groups, parse_lines, query_terms, split_keyword

GROUPS = [["声グラ", "声優グランプリ"], ["ハロウィーン", "ハロウィン"]]


class VariantsTest(unittest.TestCase):
    def test_parse_groups_accepts_several_separators(self):
        text = "声グラ = 声優グランプリ\n# メモ\n\nハロウィーン、ハロウィン／ﾊﾛｳｨﾝ\n李有"
        self.assertEqual(
            parse_groups(text),
            [["声グラ", "声優グランプリ"], ["ハロウィーン", "ハロウィン"], ["李有"]],
        )

    def test_parse_lines(self):
        self.assertEqual(parse_lines(" 声グラハロウィーン \n\n# 無視\n声優グランプリ "), ["声グラハロウィーン", "声優グランプリ"])

    def test_split_compound_keyword(self):
        self.assertEqual(split_keyword("声グラハロウィーン2026", GROUPS), ["声グラ", "ハロウィーン", "2026"])
        self.assertEqual(split_keyword("声優グランプリのハロウィン", GROUPS), ["声優グランプリ", "ハロウィン"])
        self.assertEqual(split_keyword("声グラ　ハロウィーン", GROUPS), ["声グラ", "ハロウィーン"])
        self.assertEqual(split_keyword("ムジカ", GROUPS), ["ムジカ"])

    def test_every_spelling_expands_to_the_same_queries(self):
        expected = {"声グラ ハロウィーン", "声グラ ハロウィン", "声優グランプリ ハロウィーン", "声優グランプリ ハロウィン"}
        for kw in ["声グラハロウィーン", "声優グランプリハロウィーン", "声グラ ハロウィン", "声優グランプリのハロウィン"]:
            queries, truncated = expand_queries([kw], GROUPS)
            self.assertEqual(set(queries), expected, kw)
            self.assertFalse(truncated)

    def test_duplicates_across_lines_and_limit(self):
        queries, _ = expand_queries(["声グラハロウィーン", "ハロウィーン 声グラ"], GROUPS)
        self.assertEqual(len(queries), 4)
        queries, truncated = expand_queries(["声グラハロウィーン"], GROUPS, limit=3)
        self.assertEqual((len(queries), truncated), (3, True))

    def test_without_split_keeps_keyword(self):
        queries, _ = expand_queries(["声グラハロウィーン"], GROUPS, split=False)
        self.assertEqual(queries, ["声グラハロウィーン"])
        queries, _ = expand_queries(["声グラ ハロウィーン"], GROUPS, split=False)
        self.assertEqual(len(queries), 4)

    def test_query_terms(self):
        terms = query_terms(["声グラ ハロウィーン", "#声グラハロウィーン2026"], GROUPS)
        for t in ["声グラ", "ハロウィン", "声優グランプリ", "声グラハロウィーン2026"]:
            self.assertIn(t, terms)


if __name__ == "__main__":
    unittest.main()
