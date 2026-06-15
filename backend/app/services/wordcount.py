"""Word counting & text extraction from rich-text JSON (spec §4.3).

Rules (spec §4.3):
- Chinese: counted per CJK character.
- English/numbers: counted as whitespace-separated tokens.
- Extract plain text from the rich-text JSON first, then count.
- Whitespace and punctuation are not counted toward the word count.
"""
import json
import re

# CJK ranges: covers common Han ideographs plus extensions A and compatibility.
_CJK_RE = re.compile(
    r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002a6df]"
)
# English / number tokens: runs of latin letters, digits, apostrophes, hyphens.
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[''\-][A-Za-z0-9]+)*")


def extract_text(content_json: str) -> str:
    """Recursively extract plain text from a ProseMirror/TipTap-style doc.

    Each text node contributes its `text`. Block-level nodes are separated by a
    newline so paragraph counting works. Falls back gracefully on bad JSON.
    """
    try:
        doc = json.loads(content_json) if content_json else {}
    except (json.JSONDecodeError, TypeError):
        return ""

    parts: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                parts.append(node["text"])
            for child in node.get("content", []) or []:
                walk(child)
            # block separator
            if node.get("type") in {
                "paragraph", "heading", "blockquote", "listItem",
                "codeBlock", "bulletList", "orderedList",
            }:
                parts.append("\n")
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(doc)
    return "".join(parts)


def count_words(content_json: str) -> int:
    """Count words from rich-text JSON per spec §4.3 (CJK + latin tokens)."""
    text = extract_text(content_json)
    cjk = len(_CJK_RE.findall(text))
    # Remove CJK chars before counting latin tokens to avoid double counting.
    latin_only = _CJK_RE.sub(" ", text)
    latin = len(_LATIN_TOKEN_RE.findall(latin_only))
    return cjk + latin


def count_words_from_text(text: str) -> int:
    cjk = len(_CJK_RE.findall(text))
    latin_only = _CJK_RE.sub(" ", text)
    latin = len(_LATIN_TOKEN_RE.findall(latin_only))
    return cjk + latin


def count_paragraphs(content_json: str) -> int:
    """Count non-empty paragraph/heading blocks (spec FR-61)."""
    try:
        doc = json.loads(content_json) if content_json else {}
    except (json.JSONDecodeError, TypeError):
        return 0

    count = 0

    def walk(node):
        nonlocal count
        if isinstance(node, dict):
            if node.get("type") in {"paragraph", "heading"}:
                # Only count if it has some text content.
                txt = extract_text(json.dumps(node)).strip()
                if txt:
                    count += 1
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(doc)
    return count


def reading_minutes(text: str) -> float:
    """Estimate reading minutes (spec FR-61): CJK 300 wpm, English 200 wpm."""
    cjk = len(_CJK_RE.findall(text))
    latin_only = _CJK_RE.sub(" ", text)
    latin = len(_LATIN_TOKEN_RE.findall(latin_only))
    minutes = cjk / 300.0 + latin / 200.0
    return round(minutes, 1)
