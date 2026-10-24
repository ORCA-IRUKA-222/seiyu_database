"""集計結果を CSV・HTML・コンソールに出力する。"""

from __future__ import annotations

import csv
import datetime as dt
import math
from html import escape
from pathlib import Path

from .analyze import Analysis, GroupResult
from .models import JST, Post, fmt_jst
from .textutil import normalize


class _Names:
    """--anonymize のときはアカウント名を連番に置き換え、投稿へのリンクも出さない。"""

    def __init__(self, anonymize: bool):
        self.anonymize = anonymize
        self._ids: dict[str, str] = {}

    def screen(self, p: Post) -> str:
        if not self.anonymize:
            return p.screen_name
        key = p.screen_name or p.user_id or p.id
        return self._ids.setdefault(key, f"user{len(self._ids) + 1:04d}")

    def display(self, p: Post) -> str:
        return "" if self.anonymize else p.user_name

    def link(self, p: Post) -> str:
        if self.anonymize:
            return ""
        url = p.permalink
        return url if url.startswith(("https://", "http://")) else ""


def _labels(text: str, groups: list[GroupResult]) -> str:
    t = normalize(text)
    return " / ".join(g.label for g in groups if any(normalize(x) in t for x in g.terms))


def _write_csv(path: Path, header: list[str], rows) -> Path:
    # Excel でそのまま開けるよう BOM 付き UTF-8 で書く
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def write_reports(a: Analysis, out_dir: Path, anonymize: bool = False) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    names = _Names(anonymize)
    paths = {
        "posts": _write_csv(
            out_dir / "posts.csv",
            ["投稿日時", "投稿ID", "アカウント", "表示名", "本文", "いいね", "リポスト", "引用",
             "返信", "ハッシュタグ", "注目ワード", "感想の傾向", "URL"],
            (
                [fmt_jst(p.created_at, "%Y-%m-%d %H:%M:%S"), p.id, names.screen(p), names.display(p),
                 p.text, p.likes, p.retweets, p.quotes, p.replies, " ".join("#" + h for h in p.hashtags),
                 _labels(p.text, a.keywords), _labels(p.text, a.reactions), names.link(p)]
                for p in a.posts
            ),
        ),
        "timeline": _write_csv(
            out_dir / "timeline.csv",
            ["時間帯（開始）", f"投稿数（{a.bucket_minutes}分ごと）"],
            ([fmt_jst(t), n] for t, n in a.timeline),
        ),
        "words": _write_csv(out_dir / "words.csv", ["順位", "言葉", "投稿数"],
                            ([i, w, n] for i, (w, n) in enumerate(a.words, 1))),
        "hashtags": _write_csv(out_dir / "hashtags.csv", ["順位", "ハッシュタグ", "投稿数"],
                               ([i, w, n] for i, (w, n) in enumerate(a.hashtags, 1))),
        "groups": _write_csv(
            out_dir / "groups.csv",
            ["種別", "項目", "投稿数", "割合(%)", "照合する語"],
            [["感想の傾向", g.label, g.count, round(g.share * 100, 1), " / ".join(g.terms)] for g in a.reactions]
            + [["注目ワード", g.label, g.count, round(g.share * 100, 1), " / ".join(g.terms)] for g in a.keywords],
        ),
    }
    paths["html"] = out_dir / "report.html"
    paths["html"].write_text(render_html(a, names), encoding="utf-8")
    return paths


# ---------------------------------------------------------------- HTML
#
# 「Voiceprint（声紋）」という1冊の誌面として組む。投稿数の推移を音声波形に見立てて
# 全幅で見せ、ほかは余白と文字組みだけで階層をつける。強い色はピークと引用符だけに使う。

FONTS_URL = (
    "https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400..600;1,6..96,400..600"
    "&family=DM+Mono:wght@300;400&family=Shippori+Mincho+B1:wght@500;600"
    "&family=Zen+Kaku+Gothic+New:wght@400;500&display=swap"
)

CSS = """
/* Layout: 12-column sheet. Section notes sit in the left third, readings start at column 6;
   the waveform alone runs full width. */
:root{
  --bg:#f2f0f4; --fg:#1b1720; --fg-2:#645d6c; --fg-3:#9d96a6; --rule:#dcd7e2;
  --wave:#2b2532; --accent:#8f6a2a;
  --f-display:"Bodoni Moda","Bodoni 72","Didot","Times New Roman",serif;
  --f-mincho:"Shippori Mincho B1","Yu Mincho","YuMincho","Hiragino Mincho ProN","Noto Serif JP",serif;
  --f-body:"Zen Kaku Gothic New","Yu Gothic","YuGothic","Hiragino Sans","Meiryo","Noto Sans JP",sans-serif;
  --f-mono:"DM Mono","Consolas","Menlo",monospace;
  --s1:4px; --s2:8px; --s3:16px; --s4:24px; --s5:40px; --s6:64px; --s7:clamp(80px,11vw,152px);
  --gutter:clamp(16px,2.4vw,32px); --edge:clamp(16px,5.5vw,88px);
  color-scheme:light;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#121016; --fg:#ece7f0; --fg-2:#a39bad; --fg-3:#665f6f; --rule:#2a2531;
  --wave:#d3cbdd; --accent:#c9a56a; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#121016; --fg:#ece7f0; --fg-2:#a39bad; --fg-3:#665f6f; --rule:#2a2531;
  --wave:#d3cbdd; --accent:#c9a56a; color-scheme:dark}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--f-body);font-size:16px;
  line-height:1.85;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration-color:var(--fg-3);text-underline-offset:.25em}
a:hover{text-decoration-color:var(--accent)}
:focus-visible{outline:1px solid var(--accent);outline-offset:4px}
.sheet{max-width:1320px;margin:0 auto;padding-block:var(--s5) var(--s6);
  padding-left:max(var(--edge),env(safe-area-inset-left,0px));
  padding-right:max(var(--edge),env(safe-area-inset-right,0px))}
.label{font-family:var(--f-mono);font-size:11px;font-weight:400;letter-spacing:.14em;
  text-transform:uppercase;color:var(--fg-2);line-height:1.7}
.num{font-family:var(--f-display);font-variant-numeric:lining-nums tabular-nums;font-weight:400}

.mast{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:var(--s2) var(--s4)}
.wordmark{font-family:var(--f-display);font-style:italic;font-size:28px;line-height:1;letter-spacing:.005em}
.wordmark span{font-family:var(--f-mincho);font-style:normal;font-size:12px;letter-spacing:.4em;
  color:var(--fg-2);margin-left:.9em;vertical-align:.3em}

.hero{padding-block:clamp(72px,11vw,160px) 0}
.title{font-family:var(--f-mincho);font-weight:500;font-size:clamp(30px,8.6vw,108px);line-height:1.12;
  letter-spacing:.05em;margin:var(--s4) 0 0;max-width:12em;text-wrap:balance;overflow-wrap:anywhere}
.lede{color:var(--fg-2);font-size:15px;max-width:36em;margin:var(--s4) 0 0}

.wave{margin:clamp(56px,8vw,112px) 0 0}
.wave-plot{position:relative;height:clamp(160px,24vw,300px)}
.wave-bars{position:absolute;inset:40px 0 0;display:flex;align-items:center}
.wave-bars::before{content:"";position:absolute;left:0;right:0;top:50%;border-top:1px solid var(--rule)}
.b{position:relative;flex:1 1 0;min-width:0;height:calc(var(--v) * 100%);min-height:2px;display:flex;
  justify-content:center;transform-origin:50% 50%;
  animation:rise 1.1s cubic-bezier(.2,.75,.15,1) both;animation-delay:calc(var(--i) * 1200ms / var(--n))}
.b::before{content:"";width:max(1px, min(36%, 7px));height:100%;background:var(--wave);border-radius:999px}
.b.peak::before{background:var(--accent)}
@keyframes rise{from{transform:scaleY(.03)}to{transform:scaleY(1)}}
.wave-peak{position:absolute;top:0;bottom:0;left:var(--x);border-left:1px solid var(--accent)}
.wave-peak .label{position:absolute;top:-.35em;left:10px;white-space:nowrap;color:var(--accent)}
.wave-peak.end .label{left:auto;right:10px}
.wave-ticks{position:relative;height:30px;margin-top:var(--s3)}
.wave-ticks span{position:absolute;top:0;left:var(--x);transform:translateX(-50%);white-space:nowrap;
  font-family:var(--f-mono);font-size:11px;color:var(--fg-3);font-variant-numeric:tabular-nums}
.wave-ticks span.start{transform:none}
.wave-ticks span.end{transform:translateX(-100%)}
.wave-note{display:flex;justify-content:space-between;flex-wrap:wrap;gap:var(--s2) var(--s4);margin-top:var(--s2)}
.wave-empty{color:var(--fg-2);padding-block:var(--s6)}

.facts{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));column-gap:var(--gutter);row-gap:var(--s5);
  align-items:end;padding-block:clamp(64px,9vw,128px) var(--s7)}
.lead{grid-column:1 / span 5;min-width:0}
.lead .num{display:block;font-size:clamp(88px,13.5vw,196px);line-height:.82;letter-spacing:-.025em;
  margin-top:var(--s3)}
.lead .unit{font-family:var(--f-mincho);font-size:17px;letter-spacing:.2em;color:var(--fg-2);
  display:block;margin-top:var(--s3)}
.fact-grid{grid-column:7 / -1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:var(--s5) var(--gutter);margin:0;min-width:0}
.fact-grid div{min-width:0}
.fact-grid dt{font-size:13px;color:var(--fg-2);letter-spacing:.06em}
.fact-grid dd{margin:var(--s2) 0 0}
.fact-grid .num{font-size:clamp(32px,3.4vw,48px);line-height:1}
.fact-grid .span{font-family:var(--f-mono);font-weight:300;font-size:clamp(17px,1.7vw,21px);line-height:1.4;
  letter-spacing:.02em;font-variant-numeric:tabular-nums}
.fact-grid small{display:block;font-size:13px;color:var(--fg-2);margin-top:var(--s2);line-height:1.6}

.sec{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));column-gap:var(--gutter);row-gap:var(--s5);
  padding-block:var(--s7);border-top:1px solid var(--rule)}
.sec-head{grid-column:1 / span 4;align-self:start;position:sticky;top:var(--s5);min-width:0}
.sec-head h2{font-family:var(--f-mincho);font-weight:500;font-size:clamp(24px,2.5vw,32px);line-height:1.4;
  letter-spacing:.08em;margin:var(--s3) 0 0;text-wrap:balance}
.sec-head p{color:var(--fg-2);font-size:14px;line-height:1.85;margin:var(--s3) 0 0;max-width:22em}
.sec-body{grid-column:6 / -1;min-width:0}
.empty{color:var(--fg-2);font-size:15px;margin:0}

.measures{list-style:none;margin:0;padding:0;display:grid;gap:var(--s6)}
.m{display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:var(--s4);align-items:end}
.m-name{font-size:18px;font-weight:500;letter-spacing:.06em;line-height:1.5}
.m-terms{grid-column:1;font-size:12px;color:var(--fg-3);line-height:1.7;margin-top:var(--s1)}
.m-pct{grid-column:2;grid-row:1 / span 2;font-size:clamp(40px,4.4vw,60px);line-height:.85;text-align:right}
.m-pct small{font-size:.36em;color:var(--fg-2);margin-left:.2em;letter-spacing:0}
.m-track{grid-column:1 / -1;position:relative;height:1px;background:var(--rule);margin-top:var(--s3)}
.m-track i{position:absolute;left:0;top:-1px;height:3px;width:var(--w);background:var(--fg)}
.m-foot{grid-column:1 / -1;display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;
  gap:var(--s2) var(--s4);margin-top:var(--s3)}
details{min-width:0}
details summary{cursor:pointer;list-style:none;font-family:var(--f-mono);font-size:11px;letter-spacing:.14em;
  color:var(--fg-2)}
details summary::-webkit-details-marker{display:none}
details summary::after{content:" +";color:var(--accent)}
details[open] summary::after{content:" \\2212"}
details[open]{flex-basis:100%}
.samples{list-style:none;margin:var(--s4) 0 0;padding:0 0 0 var(--s4);border-left:1px solid var(--rule);
  display:grid;gap:var(--s4)}

.lexicon{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;align-items:baseline;
  gap:.2em 1em;font-family:var(--f-mincho);font-weight:500;line-height:1.3}
.lexicon li{font-size:max(14px, calc(var(--k) * clamp(34px,4.6vw,60px)));min-width:0;overflow-wrap:anywhere;
  letter-spacing:.02em}
.lexicon li.minor{color:var(--fg-2)}
.lexicon sup{font-family:var(--f-mono);font-weight:400;font-size:11px;color:var(--fg-3);margin-left:.35em;
  letter-spacing:0;vertical-align:super;line-height:0}
.method{color:var(--fg-3);font-size:12px;margin:var(--s5) 0 0}

.pairs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--s6) var(--gutter)}
.tally{list-style:none;margin:var(--s4) 0 0;padding:0;display:grid;gap:var(--s3)}
.tally li{display:flex;align-items:baseline;gap:var(--s4)}
.tally .t{flex:1;min-width:0;overflow-wrap:anywhere;font-size:16px}
.tally.emoji .t{font-size:24px;line-height:1.3}
.tally .n{font-family:var(--f-mono);font-size:13px;color:var(--fg-2);font-variant-numeric:tabular-nums}

.feature{margin:0}
.feature blockquote{margin:0;font-family:var(--f-mincho);font-weight:500;font-size:clamp(22px,2.6vw,34px);
  line-height:1.75;letter-spacing:.04em;white-space:pre-wrap;overflow-wrap:anywhere}
.feature blockquote::before{content:"\\300C";color:var(--accent)}
.feature blockquote::after{content:"\\300D";color:var(--accent)}
.feature figcaption{margin-top:var(--s4)}
.voices{list-style:none;margin:var(--s7) 0 0;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:var(--s6) var(--gutter)}
.voice{min-width:0}
.voice p{margin:0;font-size:15px;line-height:1.95;white-space:pre-wrap;overflow-wrap:anywhere}
.voice .label{margin-top:var(--s3)}
.who{text-transform:none;letter-spacing:.04em}
.mi{white-space:nowrap}

.colophon{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));column-gap:var(--gutter);row-gap:var(--s3);
  padding-top:var(--s5);border-top:1px solid var(--rule)}
.colophon .wordmark{grid-column:1 / span 4;font-size:20px}
.colophon div{grid-column:6 / -1;color:var(--fg-2);font-size:13px;min-width:0}
.colophon p{margin:0}

@media (max-width:860px){
  .lead,.fact-grid,.sec-head,.sec-body,.colophon .wordmark,.colophon div{grid-column:1 / -1}
  .sec-head{position:static}
}
@media (max-width:600px){
  .pairs,.voices{grid-template-columns:1fr}
  .wave-ticks span:nth-child(even){display:none}
  .wave-peak .label{font-size:10px}
}
@media (prefers-reduced-motion:reduce){.b{animation:none}}
"""


def _span_text(start: int, end: int) -> str:
    if fmt_jst(start, "%Y%m%d") == fmt_jst(end, "%Y%m%d"):
        return f"{fmt_jst(start, '%H:%M')} – {fmt_jst(end, '%H:%M')}"
    return f"{fmt_jst(start, '%m/%d %H:%M')} – {fmt_jst(end, '%m/%d %H:%M')}"


def _pos(i: int, n: int) -> float:
    return (i + 0.5) / n * 100


def _edge_class(x: float) -> str:
    return "start" if x < 6 else "end" if x > 94 else ""


def _waveform(a: Analysis) -> str:
    tl = a.timeline
    if not tl:
        return '<p class="wave-empty">まだ投稿がありません。</p>'
    n = len(tl)
    top = max(c for _, c in tl) or 1
    peak = a.peak
    peak_i = next(i for i, (t, _) in enumerate(tl) if t == peak[0])
    bars = "".join(
        f'<span class="b{" peak" if i == peak_i else ""}" style="--v:{c / top:.4f};--i:{i}" '
        f'title="{escape(fmt_jst(t, "%m/%d %H:%M"))}〜 {c:,}件"></span>'
        for i, (t, c) in enumerate(tl)
    )
    px = _pos(peak_i, n)
    peak_side = " end" if px > 62 else ""
    peak_html = (
        f'<div class="wave-peak{peak_side}" style="--x:{px:.3f}%">'
        f'<span class="label">Peak {fmt_jst(peak[0], "%H:%M")}　{peak[1]:,}件</span></div>'
    )
    multi_day = fmt_jst(tl[0][0], "%Y%m%d") != fmt_jst(tl[-1][0], "%Y%m%d")
    step = max(1, math.ceil(n / (4 if multi_day else 6)))
    ticks = []
    for i in range(0, n, step):
        x = _pos(i, n)
        fmt = "%m/%d" if a.bucket_minutes >= 1440 else "%m/%d %H:%M" if multi_day else "%H:%M"
        ticks.append(f'<span class="{_edge_class(x)}" style="--x:{x:.3f}%">{fmt_jst(tl[i][0], fmt)}</span>')
    return (
        f'<figure class="wave" aria-label="{a.bucket_minutes}分ごとの投稿数。最大は{peak[1]:,}件">'
        f'<div class="wave-plot">{peak_html}'
        f'<div class="wave-bars" style="--n:{n}">{bars}</div></div>'
        f'<div class="wave-ticks">{"".join(ticks)}</div>'
        f'<figcaption class="wave-note label"><span>振幅は{a.bucket_minutes}分ごとの投稿数</span>'
        f"<span>最大 {top:,}件</span></figcaption></figure>"
    )


def _who(p: Post, names: _Names) -> str:
    screen = names.screen(p)
    display = names.display(p)
    if not screen:
        return "アカウント不明"
    return f"{display}　@{screen}" if display else f"@{screen}"


def _meta(p: Post, names: _Names) -> str:
    link = names.link(p)
    parts = [
        f'<span class="who">{escape(_who(p, names))}</span>',
        fmt_jst(p.created_at, "%m/%d %H:%M"),
        f"いいね {p.likes or 0:,}",
        f"RP {p.retweets or 0:,}",
    ]
    if p.quotes:
        parts.append(f"引用 {p.quotes:,}")
    if link:
        parts.append(f'<a href="{escape(link)}" target="_blank" rel="noopener">投稿を開く ↗</a>')
    return "　·　".join(f'<span class="mi">{x}</span>' for x in parts)


def _voice(p: Post, names: _Names, limit: int | None = None) -> str:
    text = p.text if limit is None or len(p.text) <= limit else p.text[:limit] + "…"
    return f'<li class="voice"><p>{escape(text)}</p><div class="label">{_meta(p, names)}</div></li>'


def _measures(groups: list[GroupResult], names: _Names, empty: str) -> str:
    if not groups:
        return f'<p class="empty">{escape(empty)}</p>'
    rows = []
    for g in groups:
        pct = g.share * 100
        samples = ""
        if g.samples:
            items = "".join(_voice(p, names, 160) for p in g.samples)
            samples = f'<details><summary>投稿を読む</summary><ul class="samples">{items}</ul></details>'
        rows.append(
            f'<li class="m"><span class="m-name">{escape(g.label)}</span>'
            f'<span class="m-pct num">{pct:.1f}<small>%</small></span>'
            f'<span class="m-terms">{escape(" / ".join(g.terms))}</span>'
            f'<span class="m-track"><i style="--w:{pct:.2f}%"></i></span>'
            f'<div class="m-foot"><span class="label">{g.count:,}件</span>{samples}</div></li>'
        )
    return '<ol class="measures">' + "".join(rows) + "</ol>"


def _lexicon(words: list[tuple[str, int]]) -> str:
    if not words:
        return '<p class="empty">該当する言葉がありません。</p>'
    top = words[0][1] or 1
    items = []
    for i, (w, n) in enumerate(words):
        k = 0.26 + 0.74 * (n / top) ** 0.8
        cls = "" if i < 8 else ' class="minor"'
        items.append(f'<li{cls} style="--k:{k:.3f}">{escape(w)}<sup>{n:,}</sup></li>')
    return '<ol class="lexicon">' + "".join(items) + "</ol>"


def _tally(title: str, items: list[tuple[str, int]], cls: str = "", limit: int = 12) -> str:
    body = (
        "".join(f'<li><span class="t">{escape(w)}</span><span class="n">{n:,}</span></li>' for w, n in items[:limit])
        or '<li><span class="t empty">該当なし</span></li>'
    )
    return f'<div><div class="label">{escape(title)}</div><ol class="tally {cls}">{body}</ol></div>'


def _section(title: str, eyebrow: str, note: str, body: str) -> str:
    note_html = f"<p>{escape(note)}</p>" if note else ""
    return (
        f'<section class="sec"><header class="sec-head"><div class="label">{escape(eyebrow)}</div>'
        f"<h2>{escape(title)}</h2>{note_html}</header>"
        f'<div class="sec-body">{body}</div></section>'
    )


def render_html(a: Analysis, names: _Names | None = None) -> str:
    names = names or _Names(False)
    event = a.event_name or "・".join(a.queries) or "投稿"
    now = dt.datetime.now(JST)
    excluded = sum(a.excluded.values())
    excluded_detail = "、".join(f"{k} {v:,}" for k, v in a.excluded.most_common()) or "なし"
    peak = a.peak
    cond = []
    if a.since:
        cond.append(f"{fmt_jst(a.since)} 以降")
    if a.until:
        cond.append(f"{fmt_jst(a.until)} より前")
    queries = "".join(f"「{q}」" for q in a.queries)
    lede = f"検索語{queries}で集めた公開投稿のうち、{'、'.join(cond) or '全期間'}の分を集計しました。" if queries else ""
    if a.posts:
        span = _span_text(a.posts[0].created_at, a.posts[-1].created_at)
        span_day = fmt_jst(a.posts[0].created_at, "%Y.%m.%d")
    else:
        span, span_day = "—", ""
    method = (
        "形態素解析（janome）で名詞と形容詞を取り出し、その語を含む投稿の数で並べています。"
        if a.morphological
        else "カタカナ・漢字・英字の連続を言葉とみなし、その語を含む投稿の数で並べています。"
        "janome を入れると精度が上がります。"
    )

    if a.top_posts:
        first, rest = a.top_posts[0], a.top_posts[1:]
        voices = (
            f'<figure class="feature"><blockquote>{escape(first.text)}</blockquote>'
            f'<figcaption class="label">{_meta(first, names)}</figcaption></figure>'
        )
        if rest:
            voices += '<ul class="voices">' + "".join(_voice(p, names) for p in rest) + "</ul>"
    else:
        voices = '<p class="empty">いいね数などの反応データがありません。</p>'

    sections = "".join([
        _section("感想の傾向", f"{len(a.reactions)}分類 · {a.total:,}件中", "各分類の語を1つ以上含む投稿の割合です。1件の投稿が複数の分類に入ることがあります。",
                 _measures(a.reactions, names, "感想の分類が設定されていません。")),
        _section("注目ワード", f"{len(a.keywords)}項目", "出演者名やグッズなど、config.json の keywords に登録した語です。",
                 _measures(a.keywords, names, "config.json の keywords に出演者名などを登録すると、ここに件数が出ます。")),
        _section("よく使われた言葉", f"上位{len(a.words)}語 · 投稿数", "",
                 _lexicon(a.words) + f'<p class="method">{escape(method)}</p>'),
        _section("ハッシュタグと絵文字", "投稿数", "",
                 '<div class="pairs">' + _tally("Hashtags", a.hashtags) + _tally("Emoji", a.emoji, "emoji") + "</div>"),
        _section("反応の多い投稿", f"上位{len(a.top_posts)}件", "いいね・リポスト・引用の合計が多い順です。", voices),
    ])

    peak_html = (
        f'<span class="num">{fmt_jst(peak[0], "%H:%M")}</span><small>{a.bucket_minutes}分で{peak[1]:,}件</small>'
        if peak else '<span class="num">—</span>'
    )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{escape(event)} 感想集計</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS_URL}">
<style>{CSS}</style></head>
<body><main class="sheet">
<header class="mast">
<div class="wordmark">Voiceprint<span>声紋</span></div>
<div class="label">Audience report　{now:%Y.%m.%d %H:%M}</div>
</header>
<section class="hero">
<div class="label">Yahoo!リアルタイム検索 · 公開投稿の集計</div>
<h1 class="title">{escape(event)}</h1>
<p class="lede">{escape(lede)}</p>
{_waveform(a)}
</section>
<section class="facts" aria-label="概要">
<div class="lead"><div class="label">集計した感想</div><span class="num">{a.total:,}</span><span class="unit">件の投稿</span></div>
<dl class="fact-grid">
<div><dt>投稿したアカウント</dt><dd><span class="num">{a.unique_users:,}</span></dd></div>
<div><dt>いちばん多かった時間</dt><dd>{peak_html}</dd></div>
<div><dt>投稿の期間</dt><dd><span class="span">{escape(span)}</span><small>{escape(span_day)}</small></dd></div>
<div><dt>集計から外した投稿</dt><dd><span class="num">{excluded:,}</span><small>{escape(excluded_detail)}</small></dd></div>
</dl>
</section>
{sections}
<footer class="colophon">
<div class="wordmark">Voiceprint</div>
<div><p>Yahoo!リアルタイム検索で取得した公開投稿をもとに集計しました。件数は取得できた投稿の範囲に限られます。</p>
<p class="label">作成 {now:%Y-%m-%d %H:%M} JST</p></div>
</footer>
</main></body></html>
"""


def _period(a: Analysis) -> str:
    if not a.posts:
        return "—"
    return f"{fmt_jst(a.posts[0].created_at)} 〜 {fmt_jst(a.posts[-1].created_at)}"


def print_summary(a: Analysis, paths: dict[str, Path] | None = None) -> None:
    title = a.event_name or " / ".join(a.queries)
    print(f"=== {title} 感想集計 ===")
    excluded = sum(a.excluded.values())
    detail = "、".join(f"{k} {v:,}" for k, v in a.excluded.most_common())
    print(f"投稿数: {a.total:,}件（アカウント {a.unique_users:,}）  除外: {excluded:,}件" + (f"（{detail}）" if detail else ""))
    print(f"期間: {_period(a)}")
    if a.peak:
        print(f"ピーク: {fmt_jst(a.peak[0])}〜（{a.peak[1]:,}件／{a.bucket_minutes}分）")
    if a.reactions:
        print("感想の傾向: " + " / ".join(f"{g.label} {g.count:,}件({g.share * 100:.0f}%)" for g in a.reactions))
    if a.keywords:
        print("注目ワード: " + " / ".join(f"{g.label} {g.count:,}件" for g in a.keywords))
    if a.words:
        print("よく使われた言葉: " + "、".join(f"{w}({n})" for w, n in a.words[:15]))
    if a.hashtags:
        print("ハッシュタグ: " + "、".join(f"{w}({n})" for w, n in a.hashtags[:10]))
    if paths:
        print(f"レポート: {paths['html']}")
