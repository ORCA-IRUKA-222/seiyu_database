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


def _write_hits(path: Path, classified: list[tuple[Post, str | None]], names: _Names) -> Path:
    return _write_csv(
        path,
        HITS_HEADER,
        (
            [fmt_jst(p.created_at, "%Y-%m-%d %H:%M:%S"), p.id, names.screen(p), names.display(p), p.text,
             p.likes, p.retweets, p.quotes, p.replies, " ".join("#" + h for h in p.hashtags),
             " / ".join(p.queries), "対象" if reason is None else "除外", reason or "", names.link(p)]
            for p, reason in classified
        ),
    )


def write_hits_csv(path: Path, classified: list[tuple[Post, str | None]], anonymize: bool = False) -> Path:
    """検索で見つかったすべての投稿を、集計から外したものも理由つきで CSV に書く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    return _write_hits(path, classified, _Names(anonymize))


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
    paths["all_hits"] = _write_hits(out_dir / "all_hits.csv", a.classified, names)
    paths["html"] = out_dir / "report.html"
    paths["html"].write_text(render_html(a, names), encoding="utf-8")
    return paths


# ---------------------------------------------------------------- HTML
#
# 調査会社の結果レポートの体裁に合わせる。白地に紺 1 色、書体は端末にある游ゴシック。
# 外部ファイルは読み込まないので、オフラインでも同じ見た目になる。

WEEKDAYS = "月火水木金土日"
TICK_MINUTES = (10, 20, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080)
JST_OFFSET = 9 * 3600
HITS_HEADER = ["投稿日時", "投稿ID", "アカウント", "表示名", "本文", "いいね", "リポスト", "引用", "返信",
               "ハッシュタグ", "一致した検索語", "集計", "除外した理由", "URL"]
TOP_POSTS_SHOWN = 10

CSS = """
:root{
  --text:#333; --sub:#666; --note:#888; --line:#dcdfe3; --line-soft:#eceef1;
  --bg:#fff; --head-bg:#f3f5f8; --main:#1f4e8c; --on-main:#fff; --bar:#86a6d1; --track:#edf1f6;
  --font:"游ゴシック体",YuGothic,"游ゴシック Medium","Yu Gothic Medium","游ゴシック","Yu Gothic",
    "ヒラギノ角ゴ ProN W3","Hiragino Kaku Gothic ProN","メイリオ",Meiryo,sans-serif;
  color-scheme:light;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font);font-weight:500;
  font-size:15px;line-height:1.8;border-top:4px solid var(--main)}
a{color:var(--main)}
.container{max-width:960px;margin:0 auto;padding-block:0 80px;
  padding-left:max(20px,env(safe-area-inset-left,0px));padding-right:max(20px,env(safe-area-inset-right,0px))}

.doc-head{padding:40px 0 28px;border-bottom:1px solid var(--line)}
.doc-type{margin:0;font-size:13px;font-weight:700;color:var(--main)}
h1{margin:4px 0 0;font-size:28px;line-height:1.45;font-weight:700}
.doc-meta{margin:8px 0 0;font-size:13px;color:var(--sub)}

section{margin-top:56px}
h2{position:relative;margin:0 0 24px;padding-bottom:10px;border-bottom:1px solid var(--line);
  font-size:20px;line-height:1.5;font-weight:700}
h2::after{content:"";position:absolute;left:0;bottom:-2px;width:64px;height:3px;background:var(--main)}
h2 .no{color:var(--main);margin-right:.5em}
.lead{margin:0 0 20px}
.note{margin:10px 0 0;font-size:12.5px;color:var(--note)}

table{width:100%;border-collapse:collapse;font-size:14px}
th,td{border:1px solid var(--line);padding:10px 14px;text-align:left;vertical-align:top}
th{background:var(--head-bg);font-weight:700}
.overview th{width:9.5em;white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.rank{width:4.5em;text-align:center;color:var(--sub);white-space:nowrap;font-variant-numeric:tabular-nums}
.emoji-cell{font-size:18px;line-height:1.4}

.findings{margin:0;padding:18px 24px 18px 2.4em;background:var(--head-bg);border:1px solid var(--line)}
.findings li+li{margin-top:6px}

.chart{margin-top:36px}
.chart-unit{font-size:11px;color:var(--note);margin:0 0 4px}
.chart-body{display:grid;grid-template-columns:auto minmax(0,1fr);column-gap:8px}
.yaxis{position:relative;width:2.6em;height:240px}
.yaxis span{position:absolute;right:0;transform:translateY(50%);font-size:11px;color:var(--note);
  font-variant-numeric:tabular-nums;line-height:1}
.plot{position:relative;height:240px;border-left:1px solid #b9bec5;border-bottom:1px solid #b9bec5}
.gridline{position:absolute;left:0;right:0;border-top:1px dashed var(--line)}
.cols{position:absolute;inset:0;display:flex;align-items:flex-end;padding:0 1px}
.col{position:relative;flex:1 1 0;min-width:0;height:var(--h);margin:0 var(--m);background:var(--bar)}
.col.peak{background:var(--main)}
.col .val{position:absolute;bottom:100%;left:50%;transform:translateX(-50%);margin-bottom:4px;
  font-size:12px;font-weight:700;color:var(--main);white-space:nowrap}
.xaxis{grid-column:2;position:relative;height:24px}
.xaxis span{position:absolute;top:5px;transform:translateX(-50%);font-size:11px;color:var(--note);
  white-space:nowrap;font-variant-numeric:tabular-nums}

.hbars{display:grid;gap:14px}
.hbar{display:grid;grid-template-columns:11em minmax(0,1fr) 9em;align-items:center;column-gap:16px}
.hbar-label{font-size:14px;font-weight:700}
.hbar-track{height:18px;background:var(--track)}
.hbar-fill{height:100%;width:var(--w);background:var(--main)}
.hbar-value{font-size:14px;font-variant-numeric:tabular-nums;white-space:nowrap}
.hbar-value b{font-size:16px}
.hbar-value span{color:var(--sub);font-size:13px}

details{margin-top:24px;border:1px solid var(--line)}
summary{cursor:pointer;padding:10px 14px;background:var(--head-bg);font-size:14px;font-weight:700;color:var(--main)}
details[open] summary{border-bottom:1px solid var(--line)}
.details-body{padding:14px;overflow-x:auto}
.details-body td{font-size:13px}
.details-body th{white-space:nowrap}
.terms{color:var(--sub)}

.empty-box{margin:0;padding:18px 20px;border:1px dashed var(--line);color:var(--sub);font-size:14px}

.cols2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;align-items:start}
.cols2 h3{margin:0 0 10px;font-size:15px;font-weight:700}

.posts{display:grid;gap:16px}
.post{border:1px solid var(--line);padding:16px 18px}
.post-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 10px;font-size:13px;color:var(--sub)}
.post-rank{min-width:1.9em;padding:0 6px;background:var(--main);color:var(--on-main);font-size:12px;
  font-weight:700;text-align:center}
.post-name{color:var(--text);font-weight:700}
.post-text{margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
.post-foot{display:flex;flex-wrap:wrap;gap:2px 16px;margin-top:10px;font-size:13px;color:var(--sub)}
.post-foot a{margin-left:auto}

.doc-foot{margin-top:64px;padding-top:20px;border-top:1px solid var(--line);font-size:12.5px;color:var(--note)}
.doc-foot p{margin:0 0 4px}

@media (max-width:640px){
  body{font-size:14px}
  h1{font-size:22px}
  h2{font-size:18px}
  th,td{padding:8px 10px}
  .overview th{width:7.5em;white-space:normal}
  .cols2{grid-template-columns:1fr}
  .yaxis,.plot{height:190px}
  .xaxis span:nth-child(even){display:none}
  .hbar{grid-template-columns:minmax(0,1fr) auto;row-gap:4px}
  .hbar-label{grid-column:1 / -1}
}
@media print{
  body{font-size:10.5pt;border-top:0}
  .container{max-width:none;padding:0}
  section{margin-top:28px}
  .post,.hbar,tr,.chart{break-inside:avoid}
}
"""


def _dt(ts: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, JST)


def _hm(ts: int) -> str:
    d = _dt(ts)
    return f"{d.hour}:{d.minute:02d}"


def _jdate(ts: int, time: bool = False) -> str:
    d = _dt(ts)
    s = f"{d.year}年{d.month}月{d.day}日（{WEEKDAYS[d.weekday()]}）"
    return f"{s} {_hm(ts)}" if time else s


def _range_text(start: int, end: int) -> str:
    if _dt(start).date() == _dt(end).date():
        return f"{_jdate(start)} {_hm(start)}〜{_hm(end)}"
    return f"{_jdate(start, True)}〜{_jdate(end, True)}"


def _nice_scale(top: int) -> tuple[int, int]:
    """y 軸の最大値と目盛り間隔。目盛りが 4〜5 本になるようにする。"""
    raw = max(top, 1) / 4
    mag = 10 ** int(math.floor(math.log10(raw))) if raw >= 1 else 1
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    step = max(1, int(step)) if step >= 1 else 1
    return step * math.ceil(max(top, 1) / step), step


def _column_chart(a: Analysis) -> str:
    tl = a.timeline
    if not tl:
        return '<p class="empty-box">まだ投稿がありません。</p>'
    n = len(tl)
    ymax, step = _nice_scale(max(c for _, c in tl))
    peak_t = a.peak[0] if a.peak else None
    ticks = "".join(
        f'<span style="bottom:{v / ymax * 100:.3f}%">{v:,}</span>' for v in range(0, ymax + 1, step)
    )
    grid = "".join(
        f'<div class="gridline" style="bottom:{v / ymax * 100:.3f}%"></div>' for v in range(step, ymax + 1, step)
    )
    margin = "1px" if n <= 80 else "0.5px" if n <= 160 else "0px"
    cols = []
    for t, c in tl:
        label = f'<span class="val">{c:,}件</span>' if t == peak_t else ""
        cols.append(
            f'<div class="col{" peak" if t == peak_t else ""}" style="--h:{c / ymax * 100:.3f}%" '
            f'title="{_hm(t)}〜 {c:,}件">{label}</div>'
        )
    multi_day = _dt(tl[0][0]).date() != _dt(tl[-1][0]).date()
    # 目盛りは 0:00・3:00 のようなちょうどの時刻に置く。棒の左端がその時刻
    span = (n * a.bucket_minutes)
    tick = next((m for m in TICK_MINUTES if m >= a.bucket_minutes and span / m <= (5 if multi_day else 8)),
                TICK_MINUTES[-1])
    xlabels = []
    for i, (t, _) in enumerate(tl):
        if (t + JST_OFFSET) % (tick * 60):
            continue
        d = _dt(t)
        text = f"{d.month}/{d.day}" if tick >= 1440 or (multi_day and d.hour == 0 and d.minute == 0) else (
            f"{d.month}/{d.day} {_hm(t)}" if multi_day and tick >= 360 else _hm(t))
        xlabels.append(f'<span style="left:{i / n * 100:.3f}%">{text}</span>')
    return (
        f'<figure class="chart" style="margin-inline:0" aria-label="{a.bucket_minutes}分ごとの投稿数">'
        '<p class="chart-unit">（件）</p>'
        f'<div class="chart-body" style="--m:{margin}"><div class="yaxis">{ticks}</div>'
        f'<div class="plot">{grid}<div class="cols">{"".join(cols)}</div></div>'
        f'<div class="xaxis">{"".join(xlabels)}</div></div></figure>'
    )


def _hbars(groups: list[GroupResult], caption: str) -> str:
    rows = "".join(
        f'<div class="hbar"><div class="hbar-label">{escape(g.label)}</div>'
        f'<div class="hbar-track"><div class="hbar-fill" style="--w:{g.share * 100:.2f}%"></div></div>'
        f'<div class="hbar-value"><b>{g.share * 100:.1f}%</b><span>（{g.count:,}件）</span></div></div>'
        for g in groups
    )
    defs = "".join(
        f"<tr><th>{escape(g.label)}</th><td class=\"terms\">{escape('、'.join(g.terms))}</td>"
        f"<td>{escape(_shorten(g.samples[0].text, 80)) if g.samples else '—'}</td></tr>"
        for g in groups
    )
    return (
        f'<div class="hbars">{rows}</div>'
        f"<details><summary>{escape(caption)}</summary><div class=\"details-body\"><table>"
        "<thead><tr><th>項目</th><th>照合した語</th><th>投稿例（反応が多いもの）</th></tr></thead>"
        f"<tbody>{defs}</tbody></table></div></details>"
    )


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _rank_table(head: str, items: list[tuple[str, int]], start: int = 1, cell: str = "") -> str:
    if not items:
        return '<p class="empty-box">該当なし</p>'
    rows = "".join(
        f'<tr><td class="rank">{i}</td><td class="{cell}">{escape(w)}</td><td class="num">{n:,}件</td></tr>'
        for i, (w, n) in enumerate(items, start)
    )
    return (
        f'<table><thead><tr><th class="rank">順位</th><th>{escape(head)}</th><th class="num">投稿数</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def _post_box(rank: int, p: Post, names: _Names) -> str:
    screen = names.screen(p)
    display = names.display(p)
    who = f'<span class="post-name">{escape(display or (("@" + screen) if screen else "アカウント不明"))}</span>'
    if display and screen:
        who += f"<span>@{escape(screen)}</span>"
    link = names.link(p)
    link_html = f'<a href="{escape(link)}" target="_blank" rel="noopener">Xで見る</a>' if link else ""
    quotes = f"<span>引用 {p.quotes:,}</span>" if p.quotes else ""
    return (
        f'<article class="post"><div class="post-head"><span class="post-rank">{rank}</span>{who}'
        f"<span>{_dt(p.created_at):%m/%d} {_hm(p.created_at)}</span></div>"
        f'<p class="post-text">{escape(p.text)}</p>'
        f'<div class="post-foot"><span>いいね {p.likes or 0:,}</span><span>リポスト {p.retweets or 0:,}</span>'
        f"{quotes}{link_html}</div></article>"
    )


def _findings(a: Analysis) -> list[str]:
    out = []
    if a.peak:
        end = a.peak[0] + a.bucket_minutes * 60
        out.append(f"投稿が最も多かった時間帯は {_hm(a.peak[0])}〜{_hm(end)} で、{a.peak[1]:,}件でした。")
    if a.reactions and a.reactions[0].count:
        g = a.reactions[0]
        out.append(f"感想の傾向では「{g.label}」に当てはまる投稿が最も多く、全体の{g.share * 100:.1f}%（{g.count:,}件）でした。")
    complaint = next((g for g in a.reactions if "不満" in g.label or "要望" in g.label), None)
    if complaint and complaint is not (a.reactions[0] if a.reactions else None):
        out.append(f"「{complaint.label}」に当てはまる投稿は{complaint.share * 100:.1f}%（{complaint.count:,}件）でした。")
    if a.keywords and a.keywords[0].count:
        g = a.keywords[0]
        out.append(f"注目ワードでは「{g.label}」が最も多く、{g.count:,}件の投稿で触れられていました。")
    if a.words:
        top = "」「".join(w for w, _ in a.words[:3])
        out.append(f"よく使われた言葉は「{top}」の順でした。")
    if a.top_posts:
        p = a.top_posts[0]
        out.append(f"最も反応が多かった投稿は、いいね {p.likes or 0:,}件・リポスト {p.retweets or 0:,}件でした。")
    return out


def _section(no: int, title: str, body: str, lead: str = "") -> str:
    lead_html = f'<p class="lead">{escape(lead)}</p>' if lead else ""
    return f'<section><h2><span class="no">{no}.</span>{escape(title)}</h2>{lead_html}{body}</section>'


def render_html(a: Analysis, names: _Names | None = None) -> str:
    names = names or _Names(False)
    event = a.event_name or "・".join(a.queries) or "投稿"
    now = int(dt.datetime.now(JST).timestamp())
    excluded = sum(a.excluded.values())
    excluded_detail = "、".join(f"{k} {v:,}件" for k, v in a.excluded.most_common())

    cond = []
    if a.since:
        cond.append(f"{_jdate(a.since, True)} 以降")
    if a.until:
        cond.append(f"{_jdate(a.until, True)} より前")
    overview = [
        ("調査方法", "Yahoo!リアルタイム検索で公開投稿を収集"),
        ("検索語", "、".join(a.queries) or "—"),
        ("集計の対象", "、".join(cond) or "収集したすべての投稿"),
        ("投稿の期間", _range_text(a.posts[0].created_at, a.posts[-1].created_at) if a.posts else "—"),
        ("集計した投稿", f"{a.total:,}件（{a.unique_users:,}アカウント）"),
        ("集計から外した投稿", f"{excluded:,}件" + (f"（{excluded_detail}）" if excluded_detail else "")),
    ]
    overview_html = '<table class="overview"><tbody>' + "".join(
        f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in overview
    ) + "</tbody></table>"

    findings = _findings(a)
    findings_html = (
        '<ul class="findings">' + "".join(f"<li>{escape(s)}</li>" for s in findings) + "</ul>"
        if findings else '<p class="empty-box">まだ投稿がありません。</p>'
    )

    keywords_html = (
        _hbars(a.keywords, "項目ごとの照合語と投稿例")
        if a.keywords else
        '<p class="empty-box">注目ワードが登録されていません。画面の「注目ワード」欄（コマンドで使う場合は config.json の keywords）に'
        '出演者名などを登録すると、ここに件数が出ます。</p>'
    )
    reactions_html = (
        _hbars(a.reactions, "分類ごとの照合語と投稿例")
        if a.reactions else '<p class="empty-box">感想の分類が設定されていません。</p>'
    )

    half = (len(a.words) + 1) // 2
    words_html = (
        '<div class="cols2">'
        f'<div>{_rank_table("言葉", a.words[:half])}</div>'
        f'<div>{_rank_table("言葉", a.words[half:], start=half + 1) if a.words[half:] else ""}</div></div>'
        if a.words else '<p class="empty-box">該当する言葉がありません。</p>'
    )
    method = (
        "形態素解析（janome）で名詞と形容詞を取り出しています。"
        if a.morphological else
        "カタカナ・漢字・英字の連続を1語として数えています。janome を入れると精度が上がります。"
    )
    tags_html = (
        '<div class="cols2">'
        f'<div><h3>ハッシュタグ</h3>{_rank_table("ハッシュタグ", a.hashtags[:10])}</div>'
        f'<div><h3>絵文字</h3>{_rank_table("絵文字", a.emoji[:10], cell="emoji-cell")}</div></div>'
    )
    shown = a.top_posts[:TOP_POSTS_SHOWN]
    posts_html = (
        '<div class="posts">' + "".join(_post_box(i, p, names) for i, p in enumerate(shown, 1)) + "</div>"
        if shown else '<p class="empty-box">いいね数などの反応データがありません。</p>'
    )

    sections = [
        ("調査概要", overview_html, ""),
        ("結果のまとめ", findings_html, ""),
        ("時間帯別の投稿数", _column_chart(a), f"{a.bucket_minutes}分ごとの投稿数です。最も多い時間帯を濃い色で示しています。"),
        ("感想の傾向", reactions_html, "各分類の語を1つ以上含む投稿の割合です。1件の投稿が複数の分類に入ることがあります。"),
        ("注目ワード", keywords_html, "出演者名やグッズなど、登録した語に触れている投稿の割合です。"),
        ("よく使われた言葉", words_html + f'<p class="note">※{escape(method)}その語を含む投稿の数で並べています。</p>',
         f"上位{len(a.words)}語です。"),
        ("ハッシュタグと絵文字", tags_html, "それぞれ上位10件です。"),
        ("反応の多い投稿", posts_html,
         f"いいね・リポスト・引用の合計が多い順に{len(shown)}件を掲載しています。すべての投稿は posts.csv にあります。"),
    ]
    body = "".join(_section(i, t, b, lead) for i, (t, b, lead) in enumerate(sections, 1))
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(event)} 感想集計レポート</title>
<style>{CSS}</style></head>
<body><div class="container">
<header class="doc-head">
<p class="doc-type">感想集計レポート</p>
<h1>{escape(event)}</h1>
<p class="doc-meta">作成日時：{_jdate(now, True)}</p>
</header>
{body}
<footer class="doc-foot">
<p>※ Yahoo!リアルタイム検索で取得できた公開投稿だけを集計しています。X（旧Twitter）のすべての投稿ではありません。</p>
<p>※ 件数は投稿の数です。1件の投稿が複数の項目に入ることがあります。</p>
</footer>
</div></body></html>
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
