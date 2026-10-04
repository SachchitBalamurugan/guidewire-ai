"""Language codes across the three systems that need them.

Whisper reports ISO 639-1 ("fr"), NLLB-200 wants FLORES-200 ("fra_Latn") and the
browser's speechSynthesis wants BCP-47 ("fr-FR"). Only languages all three
handle well are listed; anything else is shown untranslated rather than
mistranslated.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str  # ISO 639-1, as Whisper reports it
    name: str
    native: str
    flores: str
    bcp47: str
    rtl: bool = False


LANGUAGES: dict[str, Language] = {
    lang.code: lang
    for lang in [
        Language("en", "English", "English", "eng_Latn", "en-US"),
        Language("ar", "Arabic", "العربية", "arb_Arab", "ar-SA", rtl=True),
        Language("fr", "French", "Français", "fra_Latn", "fr-FR"),
        Language("de", "German", "Deutsch", "deu_Latn", "de-DE"),
        Language("es", "Spanish", "Español", "spa_Latn", "es-ES"),
        Language("it", "Italian", "Italiano", "ita_Latn", "it-IT"),
        Language("pt", "Portuguese", "Português", "por_Latn", "pt-PT"),
        Language("nl", "Dutch", "Nederlands", "nld_Latn", "nl-NL"),
        Language("ru", "Russian", "Русский", "rus_Cyrl", "ru-RU"),
        Language("uk", "Ukrainian", "Українська", "ukr_Cyrl", "uk-UA"),
        Language("pl", "Polish", "Polski", "pol_Latn", "pl-PL"),
        Language("tr", "Turkish", "Türkçe", "tur_Latn", "tr-TR"),
        Language("el", "Greek", "Ελληνικά", "ell_Grek", "el-GR"),
        Language("he", "Hebrew", "עברית", "heb_Hebr", "he-IL", rtl=True),
        Language("fa", "Persian", "فارسی", "pes_Arab", "fa-IR", rtl=True),
        Language("ur", "Urdu", "اردو", "urd_Arab", "ur-PK", rtl=True),
        Language("hi", "Hindi", "हिन्दी", "hin_Deva", "hi-IN"),
        Language("bn", "Bengali", "বাংলা", "ben_Beng", "bn-IN"),
        Language("ta", "Tamil", "தமிழ்", "tam_Taml", "ta-IN"),
        Language("ne", "Nepali", "नेपाली", "npi_Deva", "ne-NP"),
        Language("zh", "Chinese", "中文", "zho_Hans", "zh-CN"),
        Language("ja", "Japanese", "日本語", "jpn_Jpan", "ja-JP"),
        Language("ko", "Korean", "한국어", "kor_Hang", "ko-KR"),
        Language("th", "Thai", "ไทย", "tha_Thai", "th-TH"),
        Language("vi", "Vietnamese", "Tiếng Việt", "vie_Latn", "vi-VN"),
        Language("id", "Indonesian", "Bahasa Indonesia", "ind_Latn", "id-ID"),
        Language("ms", "Malay", "Bahasa Melayu", "zsm_Latn", "ms-MY"),
        Language("sw", "Swahili", "Kiswahili", "swh_Latn", "sw-KE"),
        Language("sv", "Swedish", "Svenska", "swe_Latn", "sv-SE"),
    ]
}

DEFAULT_LANGUAGE = "en"


def normalize(code: str | None) -> str | None:
    """Map whatever a client or model sent ("fr-FR", "FR", "fra_Latn") to "fr"."""

    if not code:
        return None
    raw = str(code).strip()
    if not raw:
        return None
    for lang in LANGUAGES.values():
        if raw == lang.flores:
            return lang.code
    short = raw.replace("_", "-").split("-")[0].lower()
    return short if short in LANGUAGES else None


def flores(code: str) -> str | None:
    lang = LANGUAGES.get(normalize(code) or "")
    return lang.flores if lang else None


def bcp47(code: str) -> str:
    lang = LANGUAGES.get(normalize(code) or "")
    return lang.bcp47 if lang else "en-US"


def name(code: str | None) -> str:
    lang = LANGUAGES.get(normalize(code) or "")
    return lang.name if lang else (code or "Unknown")


def is_rtl(code: str | None) -> bool:
    lang = LANGUAGES.get(normalize(code) or "")
    return bool(lang and lang.rtl)


def to_json() -> list[dict[str, object]]:
    return [lang.__dict__ for lang in LANGUAGES.values()]
