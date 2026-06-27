"""i18n: language resolution + catalog integrity (placeholders, key coverage)."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nonya import i18n  # noqa: E402

_PH = re.compile(r"\{(\d+)\}")


def _set(**env):
    for k in ("NONYA_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        os.environ.pop(k, None)
    os.environ["NONYA_NO_OS_LANG"] = "1"   # deterministic en-base (ignore the host Mac's language)
    os.environ.update(env)


def test_resolution_order():
    _set(NONYA_LANG="ja")
    assert i18n.resolve_lang() == "ja", "explicit NONYA_LANG wins"
    _set(LANG="ko_KR.UTF-8")
    assert i18n.resolve_lang() == "ko", "OS locale used when no NONYA_LANG"
    _set(LANG="en_US.UTF-8")
    assert i18n.resolve_lang() == "en"
    _set()                                  # nothing set -> base
    assert i18n.resolve_lang() == "en"
    _set(NONYA_LANG="qx")                   # unsupported -> base
    assert i18n.resolve_lang() == "en"


def test_chinese_and_pt_script_aware():
    _set(LANG="zh_TW.UTF-8")
    assert i18n.resolve_lang() == "zh-Hant"
    _set(LANG="zh_CN.UTF-8")
    assert i18n.resolve_lang() == "zh-Hans"
    _set(NONYA_LANG="pt")
    assert i18n.resolve_lang() == "pt-BR"


def test_all_langs_cover_base_keys_and_preserve_placeholders():
    base = i18n.CATALOG["en"]
    # nudge/persona/briefing are en-base with optional translations (fall back to en —
    # no string leaks in the wrong language); the notify chrome must exist in EVERY lang.
    en_fallback = ("nudge.", "persona.", "briefing.", "ambiguous.", "scan.", "ratelimit.")
    for lang, table in i18n.CATALOG.items():
        for key, en in base.items():
            if key in table:
                want = set(_PH.findall(en))
                got = set(_PH.findall(table[key]))
                assert want == got, "%s/%s placeholder mismatch: %s vs %s" % (lang, key, want, got)
            else:
                assert key.startswith(en_fallback), "%s missing fully-translated key %s" % (lang, key)


def test_t_formats_and_falls_back():
    _set(NONYA_LANG="de")
    assert "9" in i18n.t("giveup.body", "Codex", 9)
    _set(NONYA_LANG="es")
    assert i18n.t("waiting.title") == "nonya: requiere entrada"
    _set(NONYA_LANG="ja")                   # nudge.default has no ja -> en fallback
    assert i18n.t("nudge.default").startswith("Continue")
    _set(NONYA_LANG="en")
    assert i18n.t("no.such.key") == "no.such.key"   # unknown key -> key itself


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    _set()
    print("i18n: all tests passed")
