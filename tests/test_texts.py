import re
import sqlite3

import pytest

import texts as T
from db import DB
from rating import DISCIPLINES, MAX_LEVEL, MIN_LEVEL

PLACEHOLDER = re.compile(r"\{(\w+)")


def placeholders(value) -> set[str]:
    if isinstance(value, dict):
        return {p for v in value.values() for p in placeholders(v)}
    if isinstance(value, list):
        return {p for v in value for p in placeholders(v)}
    return set(PLACEHOLDER.findall(value))


@pytest.mark.parametrize("lang", ["ru", "lv"])
def test_locale_has_same_keys_and_placeholders(lang):
    en, other = T.STRINGS["en"], T.STRINGS[lang]
    assert set(other) == set(en)
    for key in en:
        assert placeholders(other[key]) == placeholders(en[key]), key
    assert set(other["DISCIPLINES"]) == set(DISCIPLINES)
    assert len(other["WEEKDAYS"]) == 7


@pytest.mark.parametrize("lang", T.LANGS)
def test_criteria_complete(lang):
    for level in range(MIN_LEVEL, MAX_LEVEL + 1):
        for d in DISCIPLINES:
            assert len(T.criteria(lang, level, d)) > 20, (lang, level, d)


def test_no_division_codes_in_criteria():
    for lang in T.LANGS:
        for level in range(MIN_LEVEL, MAX_LEVEL + 1):
            for d in DISCIPLINES:
                assert not re.search(r"\b(AA|BB|A|B)\b", T.criteria(lang, level, d)), (lang, level, d)


@pytest.mark.parametrize("lang,days,expected", [
    ("en", 0, "today"), ("en", 1, "yesterday"), ("en", 5, "5 days ago"), ("en", 90, "3 months ago"),
    ("ru", 2, "2 дня назад"), ("ru", 5, "5 дней назад"), ("ru", 21, "21 день назад"),
    ("ru", 11, "11 дней назад"), ("ru", 60, "2 месяца назад"),
    ("lv", 5, "pirms 5 dienām"), ("lv", 21, "pirms 21 dienas"), ("lv", 11, "pirms 11 dienām"),
    ("lv", 30 * 7, "pirms 7 mēnešiem"),
])
def test_ago(lang, days, expected):
    assert T.ago(lang, days) == expected


def test_lang_from_telegram():
    assert T.lang_from_telegram("ru") == "ru"
    assert T.lang_from_telegram("lv") == "lv"
    assert T.lang_from_telegram("en-US") == "en"
    assert T.lang_from_telegram("de") == "en"
    assert T.lang_from_telegram(None) == "en"


def test_t_falls_back_to_english(monkeypatch):
    monkeypatch.delitem(T.STRINGS["lv"], "MENU")
    assert T.t("lv", "MENU") == T.STRINGS["en"]["MENU"]
    assert T.t("xx", "BTN_CANCEL") == "Cancel"


def test_lang_column_and_migration(tmp_path):
    db = DB(":memory:")
    db.add_player(1)
    db.add_player(2, "ru")
    assert db.player(1)["lang"] == "en" and db.player(2)["lang"] == "ru"
    db.set_lang(1, "lv")
    assert db.player(1)["lang"] == "lv"

    # a database created before the lang column existed
    path = str(tmp_path / "old.sqlite")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, consent_at TEXT NOT NULL, "
                "level REAL, tag_shown TEXT)")
    old.execute("INSERT INTO players VALUES (5, '2026-01-01T00:00:00Z', NULL, NULL)")
    old.commit()
    old.close()
    assert DB(path).player(5)["lang"] == "en"
