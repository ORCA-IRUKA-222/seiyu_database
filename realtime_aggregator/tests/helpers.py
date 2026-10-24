"""テスト用に Yahoo!リアルタイム検索の応答に似せたデータを作る。"""

from __future__ import annotations

import json

from yahoo_rt.models import parse_jst

BASE_ID = 1849000000000000000


def entry(n: int, when: str = "2026-10-24 18:00", text: str | None = None, **extra) -> dict:
    d = {
        "id": str(BASE_ID + n),
        "createdAt": parse_jst(when),
        "displayText": text if text is not None else f"\tSTART\t声グラハロウィーン\tEND\t 投稿{n}",
        "screenName": f"user{n}",
        "name": f"ユーザー{n}",
        "userId": str(1000 + n),
        "likesCount": n,
        "rtCount": 0,
        "qtCount": 0,
        "replyCount": 0,
        "url": f"https://twitter.com/user{n}/status/{BASE_ID + n}",
    }
    d.update(extra)
    return d


def next_data(entries: list[dict], oldest: str | None = None, **page_extra) -> dict:
    head = {"totalResultsAvailable": 9999}
    if oldest is not None:
        head["oldestTweetId"] = oldest
    return {"props": {"pageProps": {"pageData": {"timeline": {"head": head, "entry": entries}, **page_extra}}}}


def html_page(data: dict) -> str:
    # Next.js と同じく < > & をエスケープして埋め込む（本文に </script> があっても壊れない）
    payload = json.dumps(data, ensure_ascii=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return (
        "<!DOCTYPE html><html><head><title>リアルタイム検索</title></head><body><div id=\"__next\"></div>"
        f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'
        "</body></html>"
    )


def api_json(entries: list[dict], oldest: str | None = None) -> str:
    head = {"oldestTweetId": oldest} if oldest else {}
    return json.dumps({"timeline": {"head": head, "entry": entries}}, ensure_ascii=False)
