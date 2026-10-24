"""Yahoo!リアルタイム検索への HTTP アクセス。

アクセスは 1 本ずつ、リクエスト間に最低 `min_delay` 秒あけて行う。
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .parser import ParseResult, parse_text

BASE_URL = "https://search.yahoo.co.jp/realtime"
# 検索結果を下へスクロールしたときに呼ばれる、過去分の読み込み口。
# 公開仕様ではないため、使えなくなった場合は最新分の定期取得だけで動き続ける。
PAGINATION_PATH = "/api/v1/pagination"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Page:
    url: str
    body: str
    result: ParseResult


class YahooRealtimeClient:
    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 20.0,
        min_delay: float = 3.0,
        retries: int = 2,
        base_url: str = BASE_URL,
        urlopen=urllib.request.urlopen,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_delay = min_delay
        self.retries = retries
        self.base_url = base_url.rstrip("/")
        self._urlopen = urlopen
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None

    def search_url(self, query: str) -> str:
        return f"{self.base_url}/search?" + urllib.parse.urlencode({"p": query, "ei": "UTF-8"})

    def pagination_url(self, query: str, oldest_id: str, start: int) -> str:
        params = {"p": query, "oldestTweetId": oldest_id, "start": start, "results": 40}
        return f"{self.base_url}{PAGINATION_PATH}?" + urllib.parse.urlencode(params)

    def fetch_latest(self, query: str) -> Page:
        url = self.search_url(query)
        body = self.get(url, accept="text/html,application/xhtml+xml")
        return Page(url, body, parse_text(body))

    def fetch_older(self, query: str, oldest_id: str, start: int) -> Page:
        url = self.pagination_url(query, oldest_id, start)
        body = self.get(url, accept="application/json", referer=self.search_url(query))
        return Page(url, body, parse_text(body))

    def get(self, url: str, *, accept: str = "*/*", referer: str | None = None) -> str:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Accept-Language": "ja,en;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        for attempt in range(self.retries + 1):
            self._wait_turn()
            req = urllib.request.Request(url, headers=headers)
            try:
                with self._urlopen(req, timeout=self.timeout) as res:
                    raw = res.read()
                    charset = res.headers.get_content_charset() or "utf-8"
                self._last_request = self._monotonic()
                return raw.decode(charset, errors="replace")
            except urllib.error.HTTPError as e:
                self._last_request = self._monotonic()
                e.close()
                if e.code in RETRY_STATUS and attempt < self.retries:
                    self._sleep(self._backoff(attempt, e.headers.get("Retry-After")))
                    continue
                raise FetchError(f"HTTP {e.code} が返りました: {url}", status=e.code) from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                self._last_request = self._monotonic()
                if attempt < self.retries:
                    self._sleep(self._backoff(attempt, None))
                    continue
                reason = getattr(e, "reason", e)
                raise FetchError(f"接続できませんでした（{reason}）: {url}") from e
        raise AssertionError("unreachable")

    def _wait_turn(self) -> None:
        if self._last_request is None:
            return
        wait = self.min_delay - (self._monotonic() - self._last_request)
        if wait > 0:
            self._sleep(wait)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after and retry_after.strip().isdigit():
            return min(float(retry_after), 300.0)
        return 10.0 * (2**attempt)
