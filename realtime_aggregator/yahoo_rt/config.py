"""設定ファイル（JSON）の読み込み。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

from .models import parse_jst

DEFAULT_REACTIONS: dict[str, list[str]] = {
    "最高・楽しい": ["最高", "楽しかった", "楽しい", "たのしい", "たのしかった", "神", "優勝", "幸せ"],
    "かわいい・かっこいい": ["かわいい", "可愛い", "かわいすぎ", "かっこいい", "かっこよ", "カッコいい", "イケメン", "美しい"],
    "感動・尊い": ["感動", "泣いた", "泣ける", "涙", "エモい", "尊い", "尊すぎ"],
    "笑い": ["笑った", "笑いすぎ", "爆笑", "面白", "おもしろ"],
    "感謝・ねぎらい": ["ありがとう", "感謝", "お疲れ様", "おつかれ"],
    "次回への期待": ["また行きたい", "来年も", "次回", "またやって", "円盤化", "第2弾"],
    "不満・要望": ["残念", "見えなかった", "聞こえなかった", "待ち時間", "混雑", "改善", "トラブル"],
}


@dataclass
class Config:
    event_name: str = ""
    queries: list[str] = field(default_factory=list)
    db: str = "data/posts.sqlite3"
    out_dir: str = "reports"
    since: str | None = None  # 集計・過去分取得の開始（JST）
    until: str | None = None  # 集計の終了（JST）。collect はこの時刻で自動停止する
    interval_seconds: int = 60
    backfill_max_pages: int = 25
    request_delay_seconds: float = 3.0
    user_agent: str | None = None
    bucket_minutes: int = 10
    top_n: int = 30
    keywords: dict[str, list[str]] = field(default_factory=dict)
    reactions: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_REACTIONS))
    exclude_users: list[str] = field(default_factory=list)
    exclude_words: list[str] = field(default_factory=list)
    stopwords: list[str] = field(default_factory=list)
    dedupe_same_text: bool = True
    exclude_retweets: bool = True
    anonymize: bool = False
    base_dir: Path = field(default=Path("."), repr=False)

    @property
    def since_ts(self) -> int | None:
        return parse_jst(self.since) if self.since else None

    @property
    def until_ts(self) -> int | None:
        return parse_jst(self.until) if self.until else None

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else self.base_dir / path

    def validate(self) -> None:
        if self.interval_seconds < 20:
            raise ValueError("interval_seconds は 20 秒以上にしてください（Yahoo への負荷を抑えるため）")
        if self.request_delay_seconds < 1:
            raise ValueError("request_delay_seconds は 1 秒以上にしてください")
        if self.bucket_minutes <= 0:
            raise ValueError("bucket_minutes は 1 以上にしてください")
        # 日付の書式ミスはここで知らせる
        _ = self.since_ts, self.until_ts


def load_config(path: str | Path | None) -> Config:
    if path is None:
        return Config()
    path = Path(path)
    # メモ帳で保存すると BOM が付くことがあるので utf-8-sig で読む
    with path.open(encoding="utf-8-sig") as f:
        data = json.load(f)
    known = {f.name for f in fields(Config)} - {"base_dir"}
    unknown = sorted(k for k in data if k not in known and not k.startswith("_"))
    if unknown:
        raise ValueError(f"設定ファイルに不明な項目があります: {', '.join(unknown)}")
    cfg = Config(**{k: v for k, v in data.items() if k in known})
    cfg.base_dir = path.resolve().parent
    return cfg
