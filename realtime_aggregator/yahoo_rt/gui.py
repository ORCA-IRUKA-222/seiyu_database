"""検索条件を入れて投稿を集め、一覧・CSV・レポートで確かめる画面。

起動: app.bat をダブルクリック（または py -m yahoo_rt gui）
"""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from dataclasses import asdict, fields
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .models import JST, fmt_jst
from .report import write_hits_csv
from .search import APP_DIR, Form, SearchResult, build_config, preview_queries, run_search, safe_name, today_start

SETTINGS_PATH = APP_DIR / "gui_settings.json"
FONT_CANDIDATES = ("Yu Gothic UI", "游ゴシック", "Yu Gothic", "Meiryo UI", "メイリオ", "Meiryo", "Hiragino Sans")
COLUMNS = (
    ("time", "投稿日時", 110, "w"),
    ("account", "アカウント", 150, "w"),
    ("text", "本文", 520, "w"),
    ("likes", "いいね", 64, "e"),
    ("rts", "リポスト", 64, "e"),
    ("state", "集計", 120, "w"),
)


def load_form() -> Form:
    form = Form(since=today_start())
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return form
    names = {f.name for f in fields(Form)}
    for k, v in data.items():
        if k in names:
            setattr(form, k, v)
    return form


def save_form(form: Form) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(asdict(form), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    else:
        webbrowser.open(path.resolve().as_uri())


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.result: SearchResult | None = None
        self.cfg = None
        self.sort_key, self.sort_desc = "time", True

        root.title("リアルタイム検索 集計ツール")
        root.geometry("1180x860")
        root.minsize(940, 680)
        if sys.platform == "win32":
            root.state("zoomed")
        root.report_callback_exception = self._on_exception
        self._setup_fonts()
        self._build(load_form())
        self._update_preview()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(150, self._poll)

    # ------------------------------------------------------------ 画面づくり

    def _setup_fonts(self) -> None:
        families = set(tkfont.families(self.root))
        family = next((f for f in FONT_CANDIDATES if f in families), None)
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkCaptionFont"):
            f = tkfont.nametofont(name)
            if family:
                f.configure(family=family)
            f.configure(size=10)
        self.font = tkfont.nametofont("TkDefaultFont")
        self.small = tkfont.Font(family=self.font.cget("family"), size=9)
        self.bold = tkfont.Font(family=self.font.cget("family"), size=10, weight="bold")
        ttk.Style(self.root).configure("Treeview", rowheight=int(self.font.metrics("linespace") * 1.6))
        ttk.Style(self.root).configure("Hint.TLabel", foreground="#666666", font=self.small)
        ttk.Style(self.root).configure("Status.TLabel", font=self.bold)

    def _text(self, parent, value: str, height: int) -> tk.Text:
        t = tk.Text(parent, height=height, width=60, font=self.font, wrap="none", undo=True,
                    relief="solid", borderwidth=1, highlightthickness=0)
        t.insert("1.0", value)
        return t

    def _build(self, form: Form) -> None:
        outer = ttk.Frame(self.root, padding=(14, 12))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        cond = ttk.LabelFrame(outer, text=" 検索条件 ", padding=(12, 8))
        cond.grid(row=0, column=0, sticky="ew")
        cond.columnconfigure(1, weight=1)
        row = 0

        def label(text):
            ttk.Label(cond, text=text).grid(row=row, column=0, sticky="ne", padx=(0, 10), pady=4)

        def hint(text):
            ttk.Label(cond, text=text, style="Hint.TLabel", wraplength=300, justify="left").grid(
                row=row, column=2, sticky="nw", padx=(10, 0), pady=4)

        self.v_name = tk.StringVar(value=form.name)
        label("名前")
        ttk.Entry(cond, textvariable=self.v_name).grid(row=row, column=1, sticky="ew", pady=4)
        hint("保存ファイルとレポートの題名に使います。")
        row += 1

        label("キーワード")
        words = ttk.Frame(cond)
        words.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        words.columnconfigure(0, weight=1)
        words.columnconfigure(2, weight=1)
        self.t_keywords = self._text(words, form.keywords, 3)
        self.t_keywords.grid(row=0, column=0, sticky="ew")
        ttk.Label(words, text="表記ゆれ").grid(row=0, column=1, sticky="ne", padx=(16, 10))
        self.t_synonyms = self._text(words, form.synonyms, 3)
        self.t_synonyms.grid(row=0, column=2, sticky="ew")
        ttk.Label(words, text="1行に1つ。空白で区切ると、すべての語を含む投稿を探します。", style="Hint.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(words, text="同じ意味の語を = でつなぎます。1行に1組（例: 声グラ = 声優グランプリ）", style="Hint.TLabel").grid(
            row=1, column=2, sticky="w", pady=(2, 0))
        row += 1

        self.v_split = tk.BooleanVar(value=form.split)
        ttk.Checkbutton(cond, text="キーワードを表記ゆれの語で区切って探す（声グラハロウィーン → 声グラ ハロウィーン）",
                        variable=self.v_split, command=self._update_preview).grid(row=row, column=1, sticky="w")
        row += 1

        label("検索する語")
        self.v_preview = tk.StringVar()
        ttk.Label(cond, textvariable=self.v_preview, wraplength=640, justify="left").grid(
            row=row, column=1, columnspan=2, sticky="w", pady=4)
        row += 1

        label("期間")
        period = ttk.Frame(cond)
        period.grid(row=row, column=1, sticky="w", pady=4)
        self.v_since = tk.StringVar(value=form.since)
        self.v_until = tk.StringVar(value=form.until)
        ttk.Entry(period, textvariable=self.v_since, width=18).pack(side="left")
        ttk.Label(period, text=" 〜 ").pack(side="left")
        ttk.Entry(period, textvariable=self.v_until, width=18).pack(side="left")
        for text, hours in (("今日", None), ("24時間", 24), ("7日間", 24 * 7)):
            ttk.Button(period, text=text, width=7, command=lambda h=hours: self._set_period(h)).pack(
                side="left", padx=(8 if text == "今日" else 4, 0))
        hint("例: 2026-10-24 00:00\n終わりを空欄にすると現在までです。")
        row += 1

        label("集計から外す")
        ex = ttk.Frame(cond)
        ex.grid(row=row, column=1, sticky="ew", pady=4)
        ex.columnconfigure(1, weight=1)
        ex.columnconfigure(3, weight=1)
        self.v_ex_users = tk.StringVar(value=form.exclude_users)
        self.v_ex_words = tk.StringVar(value=form.exclude_words)
        ttk.Label(ex, text="アカウント").grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(ex, textvariable=self.v_ex_users).grid(row=0, column=1, sticky="ew")
        ttk.Label(ex, text="語").grid(row=0, column=2, padx=(12, 6))
        ttk.Entry(ex, textvariable=self.v_ex_words).grid(row=0, column=3, sticky="ew")
        self.v_trades = tk.BooleanVar(value=form.exclude_trades)
        ttk.Checkbutton(ex, text="取引の投稿（譲・求・交換）", variable=self.v_trades).grid(
            row=0, column=4, padx=(12, 0))
        hint("複数あるときは、読点か空白で区切ります。\n外した投稿もCSVには残ります。")
        row += 1

        label("注目ワード")
        self.t_focus = self._text(cond, form.focus, 2)
        self.t_focus.grid(row=row, column=1, sticky="ew", pady=4)
        hint("レポートで件数を数える語。1行に1項目。\n例: 薬師寺李有 = 李有")
        row += 1

        actions = ttk.Frame(cond)
        actions.grid(row=row, column=1, columnspan=2, sticky="w", pady=(10, 2))
        self.b_search = ttk.Button(actions, text="検索", width=12, command=self._start)
        self.b_search.pack(side="left")
        self.b_stop = ttk.Button(actions, text="停止", width=8, command=self._stop, state="disabled")
        self.b_stop.pack(side="left", padx=(6, 18))
        self.v_follow = tk.BooleanVar(value=form.follow)
        self.v_interval = tk.StringVar(value=str(form.interval))
        ttk.Checkbutton(actions, text="検索のあとも", variable=self.v_follow).pack(side="left")
        ttk.Spinbox(actions, from_=20, to=3600, increment=10, width=5, textvariable=self.v_interval).pack(side="left")
        ttk.Label(actions, text="秒ごとに新しい投稿を取りに行く").pack(side="left", padx=(4, 0))

        for t in (self.t_keywords, self.t_synonyms):
            t.bind("<KeyRelease>", lambda e: self._update_preview())

        res = ttk.LabelFrame(outer, text=" 検索結果 ", padding=(12, 8))
        res.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        res.columnconfigure(0, weight=1)
        res.rowconfigure(1, weight=1)
        bar = ttk.Frame(res)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.v_status = tk.StringVar(value="まだ検索していません。")
        ttk.Label(bar, textvariable=self.v_status, style="Status.TLabel").pack(side="left")
        self.b_folder = ttk.Button(bar, text="保存フォルダを開く", command=self._open_folder, state="disabled")
        self.b_folder.pack(side="right")
        self.b_report = ttk.Button(bar, text="レポートを開く", command=self._open_report, state="disabled")
        self.b_report.pack(side="right", padx=6)
        self.b_csv = ttk.Button(bar, text="CSVに保存…", command=self._save_csv, state="disabled")
        self.b_csv.pack(side="right")
        self.v_show_excluded = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="集計から外した投稿も表示", variable=self.v_show_excluded,
                        command=self._refresh_table).pack(side="right", padx=16)

        self.tree = ttk.Treeview(res, columns=[c[0] for c in COLUMNS], show="headings", selectmode="browse")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self._sort(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "text"))
        self.tree.tag_configure("excluded", foreground="#8a8a8a")
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(res, orient="vertical", command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self._open_post)
        self.tree.bind("<<TreeviewSelect>>", self._show_selected)
        self.detail = tk.Text(res, height=3, font=self.font, wrap="word", state="disabled",
                              relief="solid", borderwidth=1, highlightthickness=0)
        self.detail.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(res, text="行を選ぶと本文全体をここに表示します。ダブルクリックすると、その投稿をブラウザで開きます。",
                  style="Hint.TLabel").grid(row=3, column=0, sticky="w", pady=(2, 0))

        logf = ttk.LabelFrame(outer, text=" 進み具合 ", padding=(12, 6))
        logf.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        logf.columnconfigure(0, weight=1)
        self.log_text = tk.Text(logf, height=4, font=self.small, wrap="word", state="disabled",
                                relief="flat", background=self.root.cget("background"))
        self.log_text.grid(row=0, column=0, sticky="ew")

    # ------------------------------------------------------------ 入力

    def _read_form(self) -> Form:
        try:
            interval = int(self.v_interval.get())
        except ValueError:
            interval = 60
        return Form(
            name=self.v_name.get(),
            keywords=self.t_keywords.get("1.0", "end").strip(),
            synonyms=self.t_synonyms.get("1.0", "end").strip(),
            split=self.v_split.get(),
            since=self.v_since.get(),
            until=self.v_until.get(),
            exclude_users=self.v_ex_users.get(),
            exclude_words=self.v_ex_words.get(),
            exclude_trades=self.v_trades.get(),
            focus=self.t_focus.get("1.0", "end").strip(),
            follow=self.v_follow.get(),
            interval=interval,
        )

    def _update_preview(self) -> None:
        queries, truncated = preview_queries(self._read_form())
        if not queries:
            self.v_preview.set("（キーワードを入力してください）")
            return
        text = "　／　".join(queries) + f"　（{len(queries)}件）"
        if truncated:
            text += "　※多すぎるため、ここまでで打ち切ります"
        self.v_preview.set(text)

    def _set_period(self, hours: int | None) -> None:
        now = dt.datetime.now(JST)
        start = now.replace(hour=0, minute=0) if hours is None else now - dt.timedelta(hours=hours)
        self.v_since.set(start.strftime("%Y-%m-%d %H:%M"))
        self.v_until.set("")

    # ------------------------------------------------------------ 検索

    def _start(self) -> None:
        form = self._read_form()
        try:
            cfg, notes = build_config(form)
        except ValueError as e:
            messagebox.showerror("入力を確かめてください", str(e), parent=self.root)
            return
        follow = form.follow
        if follow and cfg.until_ts and cfg.until_ts <= time.time():
            follow = False
            notes.append("期間の終わりが過ぎているため、定期的な取得はしません。")
        save_form(form)
        self.cfg = cfg
        self.stop.clear()
        self._set_running(True)
        until = fmt_jst(cfg.until_ts) if cfg.until_ts else "現在"
        self._log(f"検索を始めます（{fmt_jst(cfg.since_ts)} から {until} まで、検索語 {len(cfg.queries)}件）")
        for n in notes:
            self._log(n)
        self.worker = threading.Thread(target=self._work, args=(cfg, follow), daemon=True)
        self.worker.start()

    def _work(self, cfg, follow: bool) -> None:
        try:
            run_search(
                cfg, follow=follow,
                log=lambda m: self.events.put(("log", m)),
                on_results=lambda r: self.events.put(("results", r)),
                should_stop=self.stop.is_set,
            )
        except Exception as e:  # 画面を止めずに内容を知らせる
            self.events.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            self.events.put(("done", None))

    def _stop(self) -> None:
        self.stop.set()
        self.b_stop.configure(state="disabled")
        self._log("停止しています…（読み込み中のページが終わりしだい止まります）")

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "results":
                    self._show(payload)
                elif kind == "error":
                    self._log(f"エラー: {payload}")
                    messagebox.showerror("検索中にエラーが起きました", payload, parent=self.root)
                elif kind == "done":
                    self._set_running(False)
                    self._log("終わりました。" if not self.stop.is_set() else "停止しました。")
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _set_running(self, running: bool) -> None:
        self.b_search.configure(state="disabled" if running else "normal")
        self.b_stop.configure(state="normal" if running else "disabled")

    # ------------------------------------------------------------ 結果

    def _show(self, result: SearchResult) -> None:
        self.result = result
        excluded = result.total - result.kept
        self.v_status.set(f"{result.total:,}件見つかりました（集計の対象 {result.kept:,}件／集計から外した投稿 {excluded:,}件）")
        state = "normal" if result.total else "disabled"
        self.b_csv.configure(state=state)
        self.b_report.configure(state="normal" if result.report else "disabled")
        self.b_folder.configure(state="normal" if result.report else "disabled")
        self._refresh_table()

    def _row(self, post, reason) -> tuple:
        text = " ".join(post.text.split())
        return (
            fmt_jst(post.created_at, "%m/%d %H:%M"),
            f"@{post.screen_name}" if post.screen_name else "",
            text,
            f"{post.likes or 0:,}",
            f"{post.retweets or 0:,}",
            "対象" if reason is None else reason,
        )

    def _refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.result:
            return
        show_excluded = self.v_show_excluded.get()
        keys = {
            "time": lambda pr: pr[0].created_at,
            "account": lambda pr: pr[0].screen_name.lower(),
            "text": lambda pr: pr[0].text,
            "likes": lambda pr: pr[0].likes or 0,
            "rts": lambda pr: pr[0].retweets or 0,
            "state": lambda pr: pr[1] or "",
        }
        rows = sorted(self.result.classified, key=keys[self.sort_key], reverse=self.sort_desc)
        for post, reason in rows:
            if reason and not show_excluded:
                continue
            self.tree.insert("", "end", iid=post.id, values=self._row(post, reason),
                             tags=("excluded",) if reason else ())

    def _sort(self, key: str) -> None:
        self.sort_desc = not self.sort_desc if self.sort_key == key else key in ("time", "likes", "rts")
        self.sort_key = key
        self._refresh_table()

    def _selected_post(self):
        sel = self.tree.selection()
        if not sel or not self.result:
            return None, None
        return next(((p, r) for p, r in self.result.classified if p.id == sel[0]), (None, None))

    def _show_selected(self, _event=None) -> None:
        post, reason = self._selected_post()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if post:
            state = "集計の対象" if reason is None else f"集計から外した投稿（{reason}）"
            head = f"{fmt_jst(post.created_at)}　{post.user_name} @{post.screen_name}　いいね {post.likes or 0:,}"
            head += f"・リポスト {post.retweets or 0:,}　{state}"
            if post.queries:
                head += f"　検索語: {' / '.join(post.queries)}"
            self.detail.insert("1.0", f"{head}\n{post.text}")
        self.detail.configure(state="disabled")

    def _open_post(self, _event=None) -> None:
        post, _ = self._selected_post()
        if post and post.permalink.startswith("https://"):
            webbrowser.open(post.permalink)

    def _save_csv(self) -> None:
        if not self.result or not self.result.total:
            return
        stamp = dt.datetime.now(JST).strftime("%Y%m%d_%H%M")
        path = filedialog.asksaveasfilename(
            parent=self.root, title="CSVに保存", defaultextension=".csv",
            initialfile=f"{safe_name(self.cfg.event_name)}_{stamp}.csv",
            filetypes=[("CSV ファイル", "*.csv"), ("すべてのファイル", "*.*")],
        )
        if not path:
            return
        try:
            write_hits_csv(Path(path), self.result.classified)
        except OSError as e:
            messagebox.showerror("CSVに保存できませんでした", f"{e}\nExcel で開いたままの場合は閉じてから保存してください。",
                                 parent=self.root)
            return
        self._log(f"CSVに保存しました（{self.result.total:,}件）: {path}")

    def _open_report(self) -> None:
        if self.result and self.result.report:
            webbrowser.open(self.result.report.resolve().as_uri())

    def _open_folder(self) -> None:
        if self.result and self.result.report:
            open_path(self.result.report.parent)

    # ------------------------------------------------------------ その他

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_exception(self, exc, value, tb) -> None:
        messagebox.showerror("エラーが起きました", f"{exc.__name__}: {value}", parent=self.root)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askokcancel("終了", "検索を止めて終了しますか？", parent=self.root):
                return
            self.stop.set()
        save_form(self._read_form())
        self.root.destroy()


def main() -> int:
    if sys.platform == "win32":
        try:  # 高解像度の画面で文字がぼやけないようにする
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
