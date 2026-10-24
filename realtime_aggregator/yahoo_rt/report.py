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

CSS = """
:root{--bg:#f6f5f2;--card:#fff;--text:#1f1d1a;--muted:#6d6a64;--line:#e6e2da;
--accent:#d4600c;--accent-strong:#9a3f00;--accent-soft:#f5dcc8;}
@media (prefers-color-scheme:dark){:root{--bg:#141317;--card:#1f1d24;--text:#ecebef;
--muted:#9d9aa5;--line:#34313b;--accent:#f0883a;--accent-strong:#ffb36b;--accent-soft:#4a2f1d;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);line-height:1.6;
font-family:system-ui,-apple-system,"Segoe UI","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 16px 56px}
h1{font-size:1.6rem;margin:0 0 4px}
h2{font-size:1.1rem;margin:0 0 12px}
.meta{color:var(--muted);font-size:.85rem;margin:0}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 18px 14px;margin-top:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:20px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi b{display:block;font-size:1.7rem;line-height:1.2;font-variant-numeric:tabular-nums}
.kpi span{color:var(--muted);font-size:.8rem}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.grid3 section{margin-top:0}
svg{width:100%;height:auto;display:block}
.bar-rect{fill:var(--accent)} .bar-rect.peak{fill:var(--accent-strong)}
.axis{fill:var(--muted);font-size:12px} .gridline{stroke:var(--line);stroke-width:1}
ol.rank{list-style:none;margin:0;padding:0}
ol.rank li{display:grid;grid-template-columns:2em minmax(0,1.5fr) minmax(40px,1fr) 3.5em;gap:8px;align-items:center;
padding:3px 0;font-size:.9rem}
.rk{color:var(--muted);text-align:right;font-variant-numeric:tabular-nums}
.lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.track{background:var(--bg);border-radius:4px;height:10px;overflow:hidden}
.track i{display:block;height:100%;background:var(--accent);border-radius:4px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.group{padding:8px 0;border-top:1px solid var(--line)} .group:first-of-type{border-top:0}
.group-head{display:grid;grid-template-columns:minmax(0,12em) 1fr 7.5em;gap:10px;align-items:center}
.group-head .num{white-space:nowrap}
.terms{color:var(--muted);font-size:.78rem}
details summary{cursor:pointer;color:var(--muted);font-size:.82rem}
.post{border-top:1px solid var(--line);padding:10px 0}
.post:first-child{border-top:0}
.post p{margin:0 0 4px;white-space:pre-wrap;word-break:break-word}
.post small{color:var(--muted)}
.post a{color:var(--accent-strong)}
.empty,.note{color:var(--muted);font-size:.85rem}
footer{color:var(--muted);font-size:.8rem;margin-top:24px}
@media (max-width:560px){ol.rank li{grid-template-columns:1.6em minmax(0,1.5fr) minmax(40px,1fr) 3em}
.group-head{grid-template-columns:1fr auto}.group-head .track{display:none}}
"""


def _nice_ceiling(v: int) -> int:
    if v <= 4:
        return 4
    mag = 10 ** int(math.floor(math.log10(v)))
    for m in (1, 2, 4, 5, 8, 10):
        if m * mag >= v:
            return int(m * mag)
    return 10 * mag


def _timeline_svg(a: Analysis) -> str:
    tl = a.timeline
    if not tl:
        return '<p class="empty">投稿がありません</p>'
    W, H, L, R, T, B = 1000, 260, 46, 10, 10, 42
    pw, ph = W - L - R, H - T - B
    ymax = _nice_ceiling(max(n for _, n in tl))
    bw = pw / len(tl)
    peak_t = a.peak[0] if a.peak else None
    multi_day = fmt_jst(tl[0][0], "%Y%m%d") != fmt_jst(tl[-1][0], "%Y%m%d")
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="時間帯ごとの投稿数">']
    for k in range(5):
        y = T + ph - ph * k / 4
        out.append(f'<line class="gridline" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="axis" x="{L - 6}" y="{y + 4:.1f}" text-anchor="end">{ymax * k // 4:,}</text>')
    for i, (t, n) in enumerate(tl):
        h = ph * n / ymax
        x = L + i * bw + bw * 0.1
        cls = "bar-rect peak" if t == peak_t else "bar-rect"
        label = f"{fmt_jst(t, '%m/%d %H:%M')}〜 {n:,}件"
        out.append(
            f'<rect class="{cls}" x="{x:.2f}" y="{T + ph - h:.2f}" width="{max(bw * 0.8, 0.6):.2f}" '
            f'height="{h:.2f}"><title>{escape(label)}</title></rect>'
        )
    step = max(1, math.ceil(len(tl) / 10))
    prev_day = None
    for i in range(0, len(tl), step):
        t = tl[i][0]
        day = fmt_jst(t, "%m/%d")
        if a.bucket_minutes >= 1440:
            text = day
        elif multi_day and day != prev_day:
            text = fmt_jst(t, "%m/%d %H:%M")
        else:
            text = fmt_jst(t, "%H:%M")
        prev_day = day
        x = L + i * bw + bw / 2
        out.append(f'<text class="axis" x="{x:.1f}" y="{H - B + 18}" text-anchor="middle">{escape(text)}</text>')
    out.append("</svg>")
    return "".join(out)


def _rank_list(items: list[tuple[str, int]]) -> str:
    if not items:
        return '<p class="empty">該当なし</p>'
    top = items[0][1] or 1
    rows = [
        f'<li><span class="rk">{i}</span><span class="lbl" title="{escape(w)}">{escape(w)}</span>'
        f'<span class="track"><i style="width:{n / top * 100:.1f}%"></i></span><span class="num">{n:,}</span></li>'
        for i, (w, n) in enumerate(items, 1)
    ]
    return '<ol class="rank">' + "".join(rows) + "</ol>"


def _post_html(p: Post, names: _Names, limit: int | None = None) -> str:
    text = p.text if limit is None or len(p.text) <= limit else p.text[:limit] + "…"
    who = names.screen(p)
    who = f"@{who}" + (f"（{names.display(p)}）" if names.display(p) else "") if who else "（不明）"
    stats = f"♥ {p.likes or 0:,}　RP {p.retweets or 0:,}　引用 {p.quotes or 0:,}"
    link = names.link(p)
    link_html = f' · <a href="{escape(link)}" target="_blank" rel="noopener">投稿を開く</a>' if link else ""
    return (
        f'<div class="post"><p>{escape(text)}</p>'
        f"<small>{escape(who)} · {fmt_jst(p.created_at, '%m/%d %H:%M')} · {stats}{link_html}</small></div>"
    )


def _groups_html(groups: list[GroupResult], names: _Names, empty: str) -> str:
    if not groups:
        return f'<p class="empty">{escape(empty)}</p>'
    out = []
    for g in groups:
        samples = "".join(_post_html(p, names, 160) for p in g.samples)
        detail = f"<details><summary>反応の多い投稿</summary>{samples}</details>" if samples else ""
        out.append(
            f'<div class="group"><div class="group-head"><b>{escape(g.label)}</b>'
            f'<span class="track"><i style="width:{g.share * 100:.1f}%"></i></span>'
            f'<span class="num">{g.count:,}件（{g.share * 100:.1f}%）</span></div>'
            f'<div class="terms">照合: {escape(" / ".join(g.terms))}</div>{detail}</div>'
        )
    return "".join(out)


def _period(a: Analysis) -> str:
    if not a.posts:
        return "—"
    return f"{fmt_jst(a.posts[0].created_at)} 〜 {fmt_jst(a.posts[-1].created_at)}"


def render_html(a: Analysis, names: _Names | None = None) -> str:
    names = names or _Names(False)
    title = f"{a.event_name or '・'.join(a.queries) or '投稿'} 感想集計"
    now = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    excluded = sum(a.excluded.values())
    excluded_detail = "、".join(f"{k} {v:,}" for k, v in a.excluded.most_common()) or "なし"
    peak = a.peak
    peak_text = f"{fmt_jst(peak[0], '%m/%d %H:%M')}〜" if peak else "—"
    peak_sub = f"{peak[1]:,}件／{a.bucket_minutes}分" if peak else ""
    filt = []
    if a.since:
        filt.append(f"{fmt_jst(a.since)} 以降")
    if a.until:
        filt.append(f"{fmt_jst(a.until)} より前")
    word_note = (
        "形態素解析（janome）で名詞・形容詞を抽出"
        if a.morphological
        else "簡易抽出（カタカナ・漢字・英字の連続）。janome を入れると精度が上がります"
    )
    top_posts = (
        "".join(_post_html(p, names) for p in a.top_posts)
        or '<p class="empty">いいね数などの反応データがありません</p>'
    )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head>
<body><main>
<header>
<h1>{escape(title)}</h1>
<p class="meta">検索語: {escape(" / ".join(a.queries) or "—")}　集計条件: {escape("、".join(filt) or "全期間")}</p>
<p class="meta">投稿の期間: {escape(_period(a))}　作成: {now}</p>
</header>
<div class="kpis">
<div class="kpi"><span>集計した投稿</span><b>{a.total:,}</b><span>件</span></div>
<div class="kpi"><span>投稿したアカウント</span><b>{a.unique_users:,}</b><span>件</span></div>
<div class="kpi"><span>投稿のピーク</span><b>{escape(peak_text)}</b><span>{escape(peak_sub)}</span></div>
<div class="kpi"><span>除外した投稿</span><b>{excluded:,}</b><span>{escape(excluded_detail)}</span></div>
</div>
<section><h2>時間帯ごとの投稿数（{a.bucket_minutes}分ごと）</h2>{_timeline_svg(a)}</section>
<section><h2>感想の傾向</h2>
<p class="note">各項目の語を1つ以上含む投稿の件数です。1件の投稿が複数の項目に入ることがあります。</p>
{_groups_html(a.reactions, names, "感想の分類が設定されていません")}</section>
<section><h2>注目ワード</h2>
{_groups_html(a.keywords, names, "config.json の keywords に出演者名などを登録すると、ここに件数が出ます")}</section>
<div class="grid3" style="margin-top:16px">
<section><h2>よく使われた言葉</h2>{_rank_list(a.words)}<p class="note">{escape(word_note)}</p></section>
<section><h2>ハッシュタグ</h2>{_rank_list(a.hashtags)}</section>
<section><h2>絵文字</h2>{_rank_list(a.emoji)}</section>
</div>
<section><h2>反応の多い投稿</h2>{top_posts}</section>
<footer>Yahoo!リアルタイム検索で取得した公開投稿をもとに集計。件数は取得できた投稿の範囲に限られます。</footer>
</main></body></html>
"""


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
