from __future__ import annotations

MOJIBAKE_REPLACEMENTS = {
    'Ã¡': 'á', 'Ã©': 'é', 'Ã­': 'í', 'Ã³': 'ó', 'Ãº': 'ú', 'Ã±': 'ñ', 'Ã¼': 'ü',
    'ÃÁ': 'Á', 'Ã‰': 'É', 'Ã': 'Í', 'Ã“': 'Ó', 'Ãš': 'Ú', 'Ã‘': 'Ñ', 'Ãœ': 'Ü',
    'Â¿': '¿', 'Â¡': '¡', 'Â°': '°', 'Â·': '·', 'Â ': ' ', 'Â': '',
    'â': '“', 'â': '”', 'â': '’', 'â': '‘', 'â': '–', 'â': '—',
    'â€¦': '…', 'â¬': '€',
}
MOJIBAKE_MARKERS = ('Ã', 'Â', 'â')


def _mojibake_score(text: str) -> int:
    return sum(text.count(marker) for marker in MOJIBAKE_MARKERS)


def normalize_utf8_text(value: str | None) -> str:
    """Repair common UTF-8-as-Latin-1 mojibake without touching valid text."""
    text = str(value or '')
    if not text or not any(marker in text for marker in MOJIBAKE_MARKERS):
        return text

    original_score = _mojibake_score(text)
    try:
        candidate = text.encode('latin1').decode('utf-8')
        if _mojibake_score(candidate) < original_score:
            return candidate
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass

    fixed = text
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        fixed = fixed.replace(bad, good)
    return fixed
