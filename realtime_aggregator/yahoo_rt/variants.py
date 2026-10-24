"""検索語の表記ゆれを広げる。

「声グラ = 声優グランプリ」「ハロウィーン = ハロウィン」のように同じ意味の語を組にしておくと、
キーワード「声グラハロウィーン」を、Yahoo で「すべての語を含む」検索になる
「声グラ ハロウィーン」「声優グランプリ ハロウィン」などに広げる。
語の間に空白を入れて検索するので「声グラさんのハロウィンイベント」のような書き方も拾える。
"""

from __future__ import annotations

import itertools
import re

from .textutil import normalize

MAX_QUERIES = 16
_SEP_RE = re.compile(r"\s*[=＝,，、/／]\s*")
_SPACE_RE = re.compile(r"\s+")
_HIRAGANA_RE = re.compile(r"^[ぁ-ゖー]+$")


def parse_groups(text: str) -> list[list[str]]:
    """1 行に 1 組。「声グラ = 声優グランプリ」のように = や読点で区切る。# で始まる行は無視する。"""
    groups = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words: list[str] = []
        for w in _SEP_RE.split(line):
            w = w.strip()
            if w and normalize(w) not in {normalize(x) for x in words}:
                words.append(w)
        if words:
            groups.append(words)
    return groups


def parse_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def split_keyword(keyword: str, groups: list[list[str]]) -> list[str]:
    """空白と、表記ゆれに登録した語の境目でキーワードを区切る。

    「声グラハロウィーン2026」→ ["声グラ", "ハロウィーン", "2026"]。
    登録語のあいだに残ったひらがなだけの部分（「の」など）は検索語にしない。
    """
    variants = sorted({normalize(w) for g in groups for w in g}, key=len, reverse=True)
    parts: list[str] = []
    for token in _SPACE_RE.split(normalize(keyword).strip()):
        if not token:
            continue
        pieces: list[str] = []
        buf, i = "", 0
        while i < len(token):
            match = next((v for v in variants if token.startswith(v, i)), None)
            if match:
                if buf:
                    pieces.append(buf)
                    buf = ""
                pieces.append(match)
                i += len(match)
            else:
                buf += token[i]
                i += 1
        if buf:
            pieces.append(buf)
        if len(pieces) > 1:
            pieces = [p for p in pieces if not _HIRAGANA_RE.match(p)]
        parts.extend(pieces)
    return parts


def expand_queries(
    keywords: list[str], groups: list[list[str]], *, split: bool = True, limit: int = MAX_QUERIES
) -> tuple[list[str], bool]:
    """キーワードを Yahoo に渡す検索語の一覧にする。(検索語, 上限で打ち切ったか)"""
    lookup = {normalize(w): g for g in groups for w in g}
    out: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        parts = split_keyword(kw, groups) if split else [p for p in _SPACE_RE.split(kw.strip()) if p]
        options = [lookup.get(normalize(p), [p]) for p in parts]
        for combo in itertools.product(*options):
            q = " ".join(combo)
            key = " ".join(sorted(normalize(q).split()))
            if q and key not in seen:
                seen.add(key)
                out.append(q)
    return out[:limit], len(out) > limit


def query_terms(queries: list[str], groups: list[list[str]] | None = None) -> list[str]:
    """検索語を構成する語（表記ゆれを含む）。言葉ランキングから外すのに使う。"""
    terms = {t for q in queries for t in normalize(q).replace("#", " ").split()}
    for g in groups or []:
        terms.update(normalize(w) for w in g)
    return sorted(terms, key=len, reverse=True)
