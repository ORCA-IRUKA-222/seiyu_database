"""投稿の集計。ランキングはすべて「その語を含む投稿の件数」で数える。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .config import Config
from .models import Post
from .textutil import (
    BASE_STOPWORDS,
    MENTION_RE,
    URL_RE,
    extract_emoji,
    has_morphological_analyzer,
    normalize,
    tokenize,
)

BUCKET_STEPS = (1, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440)
MAX_BUCKETS = 300
TOP_POSTS = 20
JST_OFFSET = 9 * 3600


@dataclass
class GroupResult:
    label: str
    terms: list[str]
    count: int
    share: float
    samples: list[Post]


@dataclass
class Analysis:
    event_name: str
    queries: list[str]
    since: int | None
    until: int | None
    loaded: int
    excluded: Counter
    posts: list[Post]
    unique_users: int
    bucket_minutes: int
    timeline: list[tuple[int, int]]
    hashtags: list[tuple[str, int]]
    words: list[tuple[str, int]]
    emoji: list[tuple[str, int]]
    keywords: list[GroupResult]
    reactions: list[GroupResult]
    top_posts: list[Post]
    morphological: bool = field(default=False)

    @property
    def total(self) -> int:
        return len(self.posts)

    @property
    def peak(self) -> tuple[int, int] | None:
        if not self.timeline:
            return None
        return max(self.timeline, key=lambda b: b[1])


def filter_posts(posts: list[Post], cfg: Config) -> tuple[list[Post], Counter]:
    ex_users = {normalize(u.lstrip("@")) for u in cfg.exclude_users if u}
    ex_words = [normalize(w) for w in cfg.exclude_words if w]
    excluded: Counter = Counter()
    seen: set[str] = set()
    kept = []
    for p in posts:
        text = normalize(p.text)
        if ex_users and normalize(p.screen_name) in ex_users:
            excluded["除外アカウント"] += 1
            continue
        if cfg.exclude_retweets and text.startswith("rt @"):
            excluded["リポスト"] += 1
            continue
        if any(w in text for w in ex_words):
            excluded["除外ワード"] += 1
            continue
        if cfg.dedupe_same_text:
            key = re.sub(r"\s+", "", MENTION_RE.sub("", URL_RE.sub("", text)))
            if key and key in seen:
                excluded["同じ本文の重複"] += 1
                continue
            seen.add(key)
        kept.append(p)
    return kept, excluded


def choose_bucket_minutes(span_seconds: int, requested: int) -> int:
    """棒グラフが細かくなりすぎないよう、区切りを必要に応じて広げる。"""
    candidates = sorted({requested, *(s for s in BUCKET_STEPS if s > requested)})
    for step in candidates:
        if span_seconds / (step * 60) <= MAX_BUCKETS:
            return step
    return candidates[-1]


def build_timeline(posts: list[Post], bucket_minutes: int) -> list[tuple[int, int]]:
    if not posts:
        return []
    size = bucket_minutes * 60

    def floor(ts: int) -> int:  # JST の区切り（0時・毎時0分など）にそろえる
        return ts - ((ts + JST_OFFSET) % size)

    counts = Counter(floor(p.created_at) for p in posts)
    start, end = min(counts), max(counts)
    return [(t, counts.get(t, 0)) for t in range(start, end + size, size)]


def match_groups(posts: list[Post], groups: dict[str, list[str]], samples: int = 3) -> list[GroupResult]:
    texts = [normalize(p.text) for p in posts]
    results = []
    for label, terms in groups.items():
        terms = [t for t in (terms or [label]) if t]
        needles = [normalize(t) for t in terms]
        hits = [p for p, t in zip(posts, texts) if any(n in t for n in needles)]
        top = sorted(hits, key=lambda p: (p.engagement, p.created_at), reverse=True)[:samples]
        share = len(hits) / len(posts) if posts else 0.0
        results.append(GroupResult(label, terms, len(hits), share, top))
    return sorted(results, key=lambda g: g.count, reverse=True)


def count_hashtags(posts: list[Post]) -> Counter:
    counts: Counter = Counter()
    display: dict[str, Counter] = {}
    for p in posts:
        for key, tag in {normalize(t): t for t in p.hashtags}.items():
            counts[key] += 1
            display.setdefault(key, Counter())[tag] += 1
    return Counter({"#" + display[k].most_common(1)[0][0]: n for k, n in counts.items()})


def count_words(posts: list[Post], cfg: Config) -> Counter:
    stop = BASE_STOPWORDS | {normalize(w) for w in cfg.stopwords}
    # 検索語そのもの（とその一部）はどの投稿にも出るのでランキングから外す
    query_terms = [normalize(q).lstrip("#") for q in [*cfg.queries, cfg.event_name] if q]
    counts: Counter = Counter()
    for p in posts:
        for w in set(tokenize(p.text)):
            if w in stop or any(w in q for q in query_terms):
                continue
            counts[w] += 1
    return counts


def analyze(posts: list[Post], cfg: Config) -> Analysis:
    kept, excluded = filter_posts(posts, cfg)
    span = (kept[-1].created_at - kept[0].created_at) if kept else 0
    bucket = choose_bucket_minutes(span, cfg.bucket_minutes)
    users = {p.screen_name or p.user_id for p in kept if p.screen_name or p.user_id}
    emoji: Counter = Counter()
    for p in kept:
        emoji.update(set(extract_emoji(p.text)))
    top = sorted(kept, key=lambda p: (p.engagement, p.created_at), reverse=True)[:TOP_POSTS]
    return Analysis(
        event_name=cfg.event_name,
        queries=list(cfg.queries),
        since=cfg.since_ts,
        until=cfg.until_ts,
        loaded=len(posts),
        excluded=excluded,
        posts=kept,
        unique_users=len(users),
        bucket_minutes=bucket,
        timeline=build_timeline(kept, bucket),
        hashtags=count_hashtags(kept).most_common(cfg.top_n),
        words=count_words(kept, cfg).most_common(cfg.top_n),
        emoji=emoji.most_common(min(cfg.top_n, 20)),
        keywords=match_groups(kept, cfg.keywords),
        reactions=match_groups(kept, cfg.reactions),
        top_posts=[p for p in top if p.engagement > 0],
        morphological=has_morphological_analyzer(),
    )
