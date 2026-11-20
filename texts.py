"""User-facing text in English, Russian and Latvian.

UI strings live in locales/<lang>.yaml, level criteria in criteria.<lang>.yaml.
en is the reference: other languages must have the same keys and {placeholders}.
"""
from __future__ import annotations

from pathlib import Path

import yaml

LANGS = ("en", "ru", "lv")
DEFAULT = "en"
_DIR = Path(__file__).parent


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


STRINGS: dict[str, dict] = {lang: _load(_DIR / "locales" / f"{lang}.yaml") for lang in LANGS}
CRITERIA: dict[str, dict[int, dict[str, str]]] = {
    lang: {lvl["level"]: lvl["criteria"] for lvl in _load(_DIR / f"criteria.{lang}.yaml")["levels"]}
    for lang in LANGS
}

CHOOSE_LANG = "Choose language / Выберите язык / Izvēlieties valodu"


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT


def lang_from_telegram(code: str | None) -> str:
    """Telegram's language_code ('ru', 'lv', 'en-US', ...) -> one of LANGS."""
    code = (code or "").lower()
    for lang in LANGS:
        if code.startswith(lang):
            return lang
    return DEFAULT


def t(lang: str, key: str, **kw) -> str:
    """Translated string; falls back to English if the key is missing."""
    s = STRINGS[norm(lang)].get(key)
    if s is None:
        s = STRINGS[DEFAULT][key]
    return s.format(**kw) if kw else s


def discipline(lang: str, key: str) -> str:
    return STRINGS[norm(lang)]["DISCIPLINES"][key]


def weekday(lang: str, i: int) -> str:
    """Short weekday name, i = 0 (Monday) .. 6."""
    return STRINGS[norm(lang)]["WEEKDAYS"][i]


def criteria(lang: str, level: int, key: str) -> str:
    return " ".join(CRITERIA[norm(lang)][level][key].split())


def plural(lang: str, n: int) -> str:
    """Plural category: en/lv use one/other, ru uses one/few/many."""
    n10, n100 = n % 10, n % 100
    if lang == "ru":
        if n10 == 1 and n100 != 11:
            return "one"
        if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
            return "few"
        return "many"
    if lang == "lv":
        return "one" if n10 == 1 and n100 != 11 else "other"
    return "one" if n == 1 else "other"


def ago(lang: str, days: int) -> str:
    lang = norm(lang)
    if days <= 0:
        return t(lang, "AGO_TODAY")
    if days == 1:
        return t(lang, "AGO_YESTERDAY")
    key, n = ("AGO_DAYS", days) if days < 60 else ("AGO_MONTHS", days // 30)
    return STRINGS[lang][key][plural(lang, n)].format(n=n)
