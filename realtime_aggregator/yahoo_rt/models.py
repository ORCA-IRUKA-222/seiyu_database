"""投稿データの型と日時ユーティリティ。"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

# Windows では tzdata が無いことが多いので、固定オフセットで JST を扱う。
JST = dt.timezone(dt.timedelta(hours=9), "JST")

_JST_INPUT_RE = re.compile(
    r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})"
    r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$"
)


@dataclass
class Post:
    id: str
    created_at: int  # UNIX秒
    text: str
    screen_name: str = ""
    user_name: str = ""
    user_id: str = ""
    likes: int | None = None
    retweets: int | None = None
    replies: int | None = None
    quotes: int | None = None
    url: str = ""
    hashtags: list[str] = field(default_factory=list)
    media_count: int = 0
    raw: dict | None = None
    queries: list[str] = field(default_factory=list)  # この投稿が見つかった検索語（保存済みのものだけ）

    @property
    def created_jst(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.created_at, JST)

    @property
    def engagement(self) -> int:
        """いいね + リポスト + 引用。人気の投稿の並び順に使う。"""
        return (self.likes or 0) + (self.retweets or 0) + (self.quotes or 0)

    @property
    def permalink(self) -> str:
        if self.url:
            return self.url
        if self.screen_name:
            return f"https://x.com/{self.screen_name}/status/{self.id}"
        return f"https://x.com/i/web/status/{self.id}"


def parse_jst(value: str) -> int:
    """'2026-10-24 18:00' や '2026/10/24' を JST とみなして UNIX秒にする。"""
    m = _JST_INPUT_RE.match(value)
    if not m:
        raise ValueError(f"日時の形式が読み取れません: {value!r}（例: 2026-10-24 18:00）")
    y, mo, d, h, mi, s = (int(g) if g else 0 for g in m.groups())
    return int(dt.datetime(y, mo, d, h, mi, s, tzinfo=JST).timestamp())


def fmt_jst(ts: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return dt.datetime.fromtimestamp(ts, JST).strftime(fmt)
