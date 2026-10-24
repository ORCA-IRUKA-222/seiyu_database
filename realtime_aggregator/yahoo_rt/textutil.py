"""日本語テキストの正規化と単語の切り出し。

janome（`pip install janome`）が入っていれば形態素解析を使う。
無い場合はカタカナ・漢字・英数字の連続を単語とみなす簡易版で動く。
"""

from __future__ import annotations

import re
import unicodedata

URL_RE = re.compile(r"https?://\S+")
MENTION_RE = re.compile(r"@\w+")
HASHTAG_RE = re.compile(r"[#＃]\w+")
_FALLBACK_WORD_RE = re.compile(r"[ァ-ヴー]{2,}|[一-龠々〆ヵヶ]{2,}|[a-z][a-z0-9]{2,}")
_ALL_HIRAGANA_RE = re.compile(r"^[ぁ-ゖー]+$")

BASE_STOPWORDS = {
    "さん", "ちゃん", "くん", "様", "こと", "もの", "ところ", "よう", "ため", "それ", "これ",
    "あれ", "どれ", "今日", "今回", "本当", "ほんと", "みたい", "感じ", "とき", "時間",
    "http", "https", "www", "com", "amp", "なに", "なん", "そう", "いい", "ない",
}

_tokenizer = None
_tokenizer_checked = False


def normalize(s: str) -> str:
    """全角・半角、大文字・小文字の違いをそろえる（照合用）。"""
    return unicodedata.normalize("NFKC", s).casefold()


def strip_noise(text: str) -> str:
    return HASHTAG_RE.sub(" ", MENTION_RE.sub(" ", URL_RE.sub(" ", text)))


def _get_tokenizer():
    global _tokenizer, _tokenizer_checked
    if not _tokenizer_checked:
        _tokenizer_checked = True
        try:
            from janome.tokenizer import Tokenizer
        except ImportError:
            _tokenizer = None
        else:
            _tokenizer = Tokenizer()
    return _tokenizer


def has_morphological_analyzer() -> bool:
    return _get_tokenizer() is not None


def tokenize(text: str) -> list[str]:
    """本文から、ランキングに使う単語（名詞・形容詞）を取り出す。"""
    body = normalize(strip_noise(text))
    tok = _get_tokenizer()
    if tok is None:
        return _FALLBACK_WORD_RE.findall(body)
    words = []
    for t in tok.tokenize(body):
        pos = t.part_of_speech.split(",")
        if pos[0] == "名詞" and pos[1] not in ("非自立", "代名詞", "数", "接尾"):
            w = t.surface
        elif pos[0] == "形容詞" and pos[1] == "自立":
            w = t.base_form
        else:
            continue
        if len(w) < 2 or (_ALL_HIRAGANA_RE.match(w) and len(w) <= 2):
            continue
        if not any(ch.isalnum() for ch in w):
            continue
        words.append(w)
    return words


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x1F000 <= cp <= 0x1F2FF
    ) and not (0x1F3FB <= cp <= 0x1F3FF)  # 肌の色の修飾子は数えない


def extract_emoji(text: str) -> list[str]:
    return [ch for ch in text if _is_emoji(ch)]
