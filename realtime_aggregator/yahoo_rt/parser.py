"""Yahoo!リアルタイム検索のページ／API応答／HARファイルから投稿を取り出す。

Yahoo 側のページ構造（JSON のキーの位置）は予告なく変わるため、決め打ちの
パスはたどらない。JSON 全体を探索し「数字のID・本文・投稿日時」を持つ辞書を
投稿とみなす。キー名の揺れは候補リストで吸収する。
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import json
import re
from dataclasses import dataclass, field

from .models import JST, Post

_NEXT_DATA_RE = re.compile(
    r"<script[^>]*\bid=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", re.S | re.I
)
# 検索語のハイライト位置を示す区切り文字（例: "\tSTART\t声グラ\tEND\t"）
_MARKER_RE = re.compile(r"\tSTART\t|\tEND\t")
_HASHTAG_RE = re.compile(r"[#＃](\w+)")

ID_KEYS = ("id", "tweetId", "postId", "id_str")
TEXT_KEYS = ("displayText", "text", "displayTextBody", "body", "fullText", "full_text")
TIME_KEYS = ("createdAt", "created_at", "postedAt", "date", "time")
SCREEN_KEYS = ("screenName", "screen_name", "userName", "username")
NAME_KEYS = ("name", "userDisplayName", "displayName", "user_name")
USER_ID_KEYS = ("userId", "user_id", "authorId")
LIKE_KEYS = ("likesCount", "likeCount", "favoriteCount", "favorite_count", "likes")
RT_KEYS = ("rtCount", "retweetCount", "retweet_count", "repostCount")
REPLY_KEYS = ("replyCount", "reply_count")
QUOTE_KEYS = ("qtCount", "quoteCount", "quote_count")
CURSOR_KEYS = ("oldestTweetId", "oldestId", "oldest_id")


@dataclass
class ParseResult:
    posts: list[Post] = field(default_factory=list)
    cursor: str | None = None  # これより古い投稿を取るための ID
    recognized: bool = False  # ページ内に解析できるデータがあったか


def _first(d: dict, keys: tuple[str, ...]):
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return None


def _to_int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = v.replace(",", "").strip()
        if s.isdigit():
            return int(s)
    return None


def parse_time(v) -> int | None:
    """UNIX秒・ミリ秒・ISO 8601・旧Twitter形式を UNIX秒にそろえる。"""
    n = _to_int(v)
    if n is not None:
        if n > 10**12:
            n //= 1000
        return n if n > 10**9 else None
    if not isinstance(v, str):
        return None
    s = v.strip()
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = dt.datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=JST)
    return int(d.timestamp())


def clean_text(s: str) -> str:
    return html.unescape(_MARKER_RE.sub("", s)).strip()


def looks_like_post(d: dict) -> bool:
    pid = _first(d, ID_KEYS)
    if pid is None or not str(pid).isdigit():
        return False
    if not isinstance(_first(d, TEXT_KEYS), str):
        return False
    return parse_time(_first(d, TIME_KEYS)) is not None


def _hashtags(d: dict, text: str) -> list[str]:
    tags: list[str] = []
    raw = d.get("hashtags")
    if isinstance(raw, list):
        for h in raw:
            if isinstance(h, dict):
                h = h.get("text") or h.get("tag")
            if isinstance(h, str) and h.strip("#＃"):
                tags.append(h.strip().lstrip("#＃"))
    tags.extend(_HASHTAG_RE.findall(text))
    seen: set[str] = set()
    out = []
    for t in tags:
        key = t.casefold()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def to_post(d: dict) -> Post | None:
    if not looks_like_post(d):
        return None
    user = d.get("user") or d.get("author") or {}
    if not isinstance(user, dict):
        user = {}
    text = clean_text(_first(d, TEXT_KEYS))
    media = d.get("media")
    return Post(
        id=str(_first(d, ID_KEYS)),
        created_at=parse_time(_first(d, TIME_KEYS)),
        text=text,
        screen_name=str(_first(d, SCREEN_KEYS) or _first(user, SCREEN_KEYS) or "").lstrip("@"),
        user_name=str(_first(d, NAME_KEYS) or _first(user, NAME_KEYS) or ""),
        user_id=str(_first(d, USER_ID_KEYS) or _first(user, ("id", "id_str")) or ""),
        likes=_to_int(_first(d, LIKE_KEYS)),
        retweets=_to_int(_first(d, RT_KEYS)),
        replies=_to_int(_first(d, REPLY_KEYS)),
        quotes=_to_int(_first(d, QUOTE_KEYS)),
        url=str(d.get("url") or ""),
        hashtags=_hashtags(d, text),
        media_count=len(media) if isinstance(media, list) else 0,
        raw=d,
    )


def find_posts(obj) -> list[Post]:
    """JSON 全体から投稿らしい辞書を出現順に集める。

    投稿と判定した辞書の中には入らない（引用元の投稿は検索ヒットではないため）。
    """
    found: list[Post] = []

    def walk(o):
        if isinstance(o, dict):
            p = to_post(o)
            if p is not None:
                found.append(p)
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return dedupe(found)


def dedupe(posts: list[Post]) -> list[Post]:
    seen: set[str] = set()
    out = []
    for p in posts:
        if p.id not in seen:
            seen.add(p.id)
            out.append(p)
    return out


def find_key(obj, keys: tuple[str, ...]):
    if isinstance(obj, dict):
        v = _first(obj, keys)
        if v is not None:
            return v
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for c in children:
        v = find_key(c, keys)
        if v is not None:
            return v
    return None


def oldest_id(posts: list[Post]) -> str | None:
    ids = [int(p.id) for p in posts if p.id.isdigit()]
    return str(min(ids)) if ids else None


def extract_next_data(text: str) -> dict | None:
    m = _NEXT_DATA_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def parse_data(data) -> ParseResult:
    posts = find_posts(data)
    cursor = find_key(data, CURSOR_KEYS)
    return ParseResult(
        posts=posts,
        cursor=str(cursor) if cursor is not None else oldest_id(posts),
        recognized=True,
    )


def parse_text(text: str) -> ParseResult:
    """HTML（__NEXT_DATA__ 入り）か JSON 文字列を解析する。"""
    data = extract_next_data(text)
    if data is not None:
        return parse_data(data)
    s = text.lstrip("﻿ \t\r\n")
    if s[:1] in "{[":
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return ParseResult()
        if isinstance(obj, dict) and isinstance(obj.get("log"), dict):
            return parse_har(obj)
        return parse_data(obj)
    return ParseResult()


def parse_har(har: dict, host_filter: str = "yahoo.co.jp") -> ParseResult:
    """ブラウザの開発者ツールで保存した HAR から、Yahoo の応答だけを解析する。"""
    posts: list[Post] = []
    for entry in har.get("log", {}).get("entries", []):
        url = entry.get("request", {}).get("url", "")
        if host_filter and host_filter not in url:
            continue
        content = entry.get("response", {}).get("content", {})
        body = content.get("text")
        if not body:
            continue
        if content.get("encoding") == "base64":
            try:
                body = base64.b64decode(body).decode("utf-8", errors="replace")
            except ValueError:
                continue
        posts.extend(parse_text(body).posts)
    posts = dedupe(posts)
    return ParseResult(posts=posts, cursor=oldest_id(posts), recognized=True)
