from __future__ import annotations

import re
from dataclasses import dataclass

UNKNOWN_LANGUAGE = "Unknown"


@dataclass(frozen=True)
class Language:
    name: str
    short: str
    codes: tuple[str, ...]
    aliases: tuple[str, ...] = ()


LANGUAGE_CATALOG: tuple[Language, ...] = (
    Language("Hindi", "Hin", ("hi", "hin"), ("hindi", "hindhi")),
    Language("English", "Eng", ("en", "eng"), ("english", "eng")),
    Language("Japanese", "Jap", ("ja", "jpn"), ("japanese", "jap", "nihongo")),
    Language("Tamil", "Tam", ("ta", "tam"), ("tamil",)),
    Language("Telugu", "Tel", ("te", "tel"), ("telugu",)),
    Language("French", "Fre", ("fr", "fra", "fre"), ("french", "francais", "français")),
    Language("Spanish", "Spa", ("es", "spa"), ("spanish", "espanol", "español")),
    Language("German", "Ger", ("de", "deu", "ger"), ("german", "deutsch")),
    Language("Portuguese", "Por", ("pt", "por"), ("portuguese", "portugues", "português")),
    Language("Korean", "Kor", ("ko", "kor"), ("korean",)),
    Language("Chinese", "Chi", ("zh", "zho", "chi"), ("chinese", "mandarin", "cantonese")),
    Language("Arabic", "Ara", ("ar", "ara"), ("arabic",)),
    Language("Bengali", "Ben", ("bn", "ben"), ("bengali", "bangla")),
    Language("Malayalam", "Mal", ("ml", "mal"), ("malayalam",)),
    Language("Kannada", "Kan", ("kn", "kan"), ("kannada",)),
    Language("Marathi", "Mar", ("mr", "mar"), ("marathi",)),
    Language("Urdu", "Urd", ("ur", "urd"), ("urdu",)),
    Language("Thai", "Tha", ("th", "tha"), ("thai",)),
    Language("Indonesian", "Ind", ("id", "ind"), ("indonesian", "bahasa indonesia")),
    Language("Russian", "Rus", ("ru", "rus"), ("russian",)),
    Language("Italian", "Ita", ("it", "ita"), ("italian",)),
    Language("Turkish", "Tur", ("tr", "tur"), ("turkish",)),
    Language("Vietnamese", "Vie", ("vi", "vie"), ("vietnamese",)),
    Language("Filipino", "Fil", ("fil", "tgl"), ("filipino", "tagalog")),
)

_BY_NAME = {language.name.lower(): language for language in LANGUAGE_CATALOG}
_BY_CODE: dict[str, Language] = {}
for _language in LANGUAGE_CATALOG:
    for _code in _language.codes:
        _BY_CODE[_code.lower()] = _language
_BY_ALIAS = dict(_BY_NAME.items())
for _language in LANGUAGE_CATALOG:
    for _alias in _language.aliases:
        _BY_ALIAS[_alias.lower()] = _language

_WORD_RE = re.compile(r"[a-zà-ÿ']+", re.IGNORECASE)
_UNKNOWN_CODES = {"", "und", "unknown", "unk", "zxx", "mis", "mul", "qaa"}


@dataclass(frozen=True)
class DetectedLanguage:
    name: str
    code: str | None
    known: bool

    @property
    def short(self) -> str:
        return short_label(self.name) if self.known else UNKNOWN_LANGUAGE


def _looks_like_code(value: str) -> bool:
    lowered = value.strip().lower()
    return bool(re.fullmatch(r"[a-z]{2,3}(?:[-_][a-z]{2,4})?", lowered))


def normalize_language(raw_code: str | None, title: str | None = None) -> DetectedLanguage:
    """Resolve an ffprobe language tag (and optionally a track title) to a canonical language."""
    code = (raw_code or "").strip().lower().replace("_", "-")
    primary = code.split("-", 1)[0]
    if primary in _BY_CODE:
        language = _BY_CODE[primary]
        return DetectedLanguage(language.name, raw_code or primary, True)
    if primary in _BY_NAME:
        language = _BY_NAME[primary]
        return DetectedLanguage(language.name, raw_code or primary, True)
    search_space = " ".join(part for part in (title or "", code) if part)
    for token in _WORD_RE.findall(search_space):
        key = token.lower()
        if key in _BY_ALIAS:
            language = _BY_ALIAS[key]
            return DetectedLanguage(language.name, raw_code, True)
    if code and code not in _UNKNOWN_CODES and not _looks_like_code(code):
        for token in _WORD_RE.findall(code):
            key = token.lower()
            if key in _BY_ALIAS:
                language = _BY_ALIAS[key]
                return DetectedLanguage(language.name, raw_code, True)
    if code and code not in _UNKNOWN_CODES and _looks_like_code(code):
        return DetectedLanguage(code.upper(), raw_code, False)
    if title and title.strip():
        return DetectedLanguage(UNKNOWN_LANGUAGE, raw_code, False)
    return DetectedLanguage(UNKNOWN_LANGUAGE, raw_code, False)


def canonical_language(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in _BY_ALIAS:
        return _BY_ALIAS[lowered].name
    if lowered in _BY_CODE:
        return _BY_CODE[lowered].name
    if not lowered:
        raise ValueError("Language name cannot be empty")
    raise ValueError(f"Unknown language: {value}")


def language_names() -> list[str]:
    return [language.name for language in LANGUAGE_CATALOG]


def short_label(language_name: str) -> str:
    language = _BY_NAME.get(language_name.strip().lower())
    return language.short if language else language_name


def iso639_2(language_name: str) -> str | None:
    language = _BY_NAME.get(language_name.strip().lower())
    if not language:
        return None
    for code in language.codes:
        if len(code) == 3:
            return code
    return language.codes[0] if language.codes else None


def dedupe_languages(languages: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for language in languages:
        key = language.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(language.strip())
    return result


def order_languages(languages: list[str], preferred: list[str]) -> list[str]:
    canonical_preferred = []
    for item in preferred:
        try:
            canonical_preferred.append(canonical_language(item))
        except ValueError:
            canonical_preferred.append(item.strip())
    unique = dedupe_languages(languages)
    ranked: list[str] = []
    for preference in canonical_preferred:
        for language in unique:
            if language.lower() == preference.lower() and language not in ranked:
                ranked.append(language)
    for language in unique:
        if language not in ranked:
            ranked.append(language)
    return ranked


def build_audio_label(
    languages: list[str],
    *,
    style: str = "multi",
    dual_label: str = "Dual",
    multi_label: str = "Multi Audio",
) -> str:
    """Create a truthful filename label from the languages actually embedded in the file."""
    names = dedupe_languages(languages)
    if not names:
        return "No Audio"
    if style == "joined":
        return "-".join(short_label(name) for name in names)
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return dual_label
    return multi_label
