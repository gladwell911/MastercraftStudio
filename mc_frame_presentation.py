"""Answer text, Markdown, and timestamp presentation."""

import markdown
import math
import re
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser

from claudecode_client import is_claudecode_model
from codex_client import is_codex_model
from kimi_server_client import is_kimi_model
from openclaw_client import is_openclaw_model

import mc_frame_bindings as bindings


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        return "".join(self.parts)


def is_cli_filtered_model(model_id: str) -> bool:
    return is_codex_model(model_id) or is_claudecode_model(model_id) or is_kimi_model(model_id) or is_openclaw_model(model_id)


def execution_answer_to_plain(answer_md: str, model: str, english_filter_enabled: bool) -> str:
    """Render answer context without accessing ChatFrame or wx state."""
    text = str(answer_md or "")
    if english_filter_enabled and getattr(bindings.app, "is_cli_filtered_model", is_cli_filtered_model)(model):
        text = re.sub(r"\[[^\]]+\]\([^)]+\)", "[文件路径]", text)
        text = re.sub(r"`[^`]*(?:[A-Za-z]:[\\/]|/)[^`]*`", "[文件路径]", text)
        text = re.sub(r"`[^`]*\.(?:py|md|txt|json|yaml|yml)[^`]*`", "[文件路径]", text)
        text = re.sub(r"(?<![\w/.-])(?:[A-Za-z]:[\\/][^\s\]\)]+|/[^\s\]\)]+)", "[文件路径]", text)
        text = re.sub(r"pytest\s+tests/[^\n]+", "pytest [文件路径]", text)
        text = re.sub(r"\btest_[A-Za-z0-9_]+\b", "[测试项]", text)
    return remove_emojis(md_to_plain_preserving_paragraphs(text)).strip()


def md_to_plain(md_text: str) -> str:
    if not md_text:
        return ""
    html = markdown.markdown(md_text, extensions=["extra", "fenced_code", "tables", "sane_lists"])
    st = _Stripper()
    st.feed(html)
    return unescape(st.text()).strip()


class _ParagraphStripper(_Stripper):
    """Keep parser-decoded literal characters and block boundaries for scratch."""
    _blocks = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "ul", "ol", "table"}
    _text_blocks = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th"}

    def __init__(self):
        super().__init__()
        self._lists = []
        self._pre = False
        self._text_depth = 0
        self._trailing_newlines = 0
        self._links = []
        self._table_column = 0

    def _append(self, text):
        if not text:
            return
        self.parts.append(text)
        trailing = len(text) - len(text.rstrip("\n"))
        self._trailing_newlines = self._trailing_newlines + trailing if trailing == len(text) else trailing

    def _boundary(self, count):
        missing = count - self._trailing_newlines
        if self.parts and missing > 0:
            self._append("\n" * missing)

    def handle_data(self, data):
        if self._pre or data.strip() or (self._text_depth and "\n" not in data):
            self._append(data)

    def handle_starttag(self, tag, attrs):
        if tag in self._text_blocks:
            self._text_depth += 1
        if tag == "img":
            self._append(dict(attrs).get("alt") or "")
        if tag == "a":
            self._links.append((dict(attrs).get("href"), len(self.parts)))
        if tag in self._blocks:
            self._boundary(1 if tag in {"ol", "ul"} and self._lists else 2)
        elif tag == "br":
            self._append("\n")
        elif tag in {"li", "tr"}:
            self._boundary(1)
        if tag == "tr":
            self._table_column = 0
        elif tag in {"td", "th"}:
            if self._table_column:
                self._append("\t")
            self._table_column += 1
        if tag in {"ol", "ul"}:
            try:
                start = int(dict(attrs).get("start", 1))
            except (ValueError, TypeError):
                start = 1
            self._lists.append(start if tag == "ol" else None)
        if tag == "li" and self._lists:
            self._append("  " * (len(self._lists) - 1))
            if self._lists[-1] is None:
                self._append("- ")
            else:
                self._append(f"{self._lists[-1]}. ")
                self._lists[-1] += 1
        if tag == "pre":
            self._pre = True

    def handle_endtag(self, tag):
        if tag == "a" and self._links:
            target, start = self._links.pop()
            if target and "".join(self.parts[start:]).strip() != target:
                self._append(f" ({target})")
        if tag in self._text_blocks:
            self._text_depth -= 1
        if tag == "pre":
            self._pre = False
        if tag in {"ol", "ul"} and self._lists:
            self._lists.pop()
        if tag in self._blocks:
            self._boundary(1 if tag in {"ol", "ul"} and self._lists else 2)
        elif tag in {"li", "tr"}:
            self._boundary(1)


def answer_detail_plain_text(md_text: str) -> str:
    parser = _ParagraphStripper()
    parser.feed(markdown.markdown(str(md_text or ""), extensions=["extra", "fenced_code", "tables", "sane_lists"]))
    return parser.text().strip("\n")


def answer_time_projection(timestamps) -> list[bool]:
    """Unknown time emits a label without advancing the last shown trusted time."""
    anchor = None
    shown = []
    for raw in timestamps:
        timestamp = _finite_timestamp(raw)
        show = timestamp is None or anchor is None or timestamp - anchor >= WECHAT_TIME_GAP_SECONDS
        shown.append(show)
        if show and timestamp is not None:
            anchor = timestamp
    return shown


def md_to_plain_preserving_paragraphs(md_text: str) -> str:
    if not md_text:
        return ""
    blocks = re.split(r"\n\s*\n", str(md_text).replace("\r\n", "\n").replace("\r", "\n"))
    plain_blocks = []
    for block in blocks:
        plain = _md_block_to_plain_preserving_ordered_lists(block).strip()
        if plain:
            plain_blocks.append(plain)
    return "\n\n".join(plain_blocks).strip()


def _md_block_to_plain_preserving_ordered_lists(block: str) -> str:
    plain_text = getattr(bindings.app, "md_to_plain", md_to_plain)
    lines = str(block or "").splitlines()
    if not any(re.match(r"^\s*\d+[.)]\s+", line) for line in lines):
        return plain_text(block)
    plain_lines = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        match = re.match(r"^(\d+)[.)]\s+(.*)$", raw)
        if match:
            item_text = plain_text(match.group(2)).strip()
            if item_text:
                plain_lines.append(f"{match.group(1)}. {item_text}")
            continue
        plain = plain_text(raw).strip()
        if plain:
            plain_lines.append(plain)
    return "\n".join(plain_lines)


def remove_emojis(text: str) -> str:
    # Remove common emoji/pictograph unicode ranges (works with surrogate pairs too).
    out = []
    for ch in text:
        cp = ord(ch)
        is_emoji = (
            0x1F1E6 <= cp <= 0x1F1FF
            or 0x1F300 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x27BF
            or cp in (0x200D, 0xFE0F)  # ZWJ and emoji variation selector
            or 0xD800 <= cp <= 0xDFFF  # surrogate pairs on narrow builds
        )
        if not is_emoji:
            out.append(ch)
    return "".join(out)


def remove_trailing_punctuation(text: str) -> str:
    if not text:
        return ""
    trailing = ".,!?;:，。！？；：、…~～'\"“”‘’（）()[]【】<>《》"
    return text.strip().rstrip(trailing).strip()


def sanitize_optimized_text(text: str) -> str:
    if not text:
        return ""
    def _normalize_for_dedupe(line: str) -> str:
        # Keep CJK/alnum only so punctuation-only differences are treated as duplicates.
        kept = []
        for ch in line:
            cp = ord(ch)
            if ch.isalnum() or (0x4E00 <= cp <= 0x9FFF):
                kept.append(ch.lower())
        return "".join(kept)

    lines = str(text).replace("\r", "\n").split("\n")
    cleaned_lines = []
    seen_norm = set()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # Strip common markdown prefixes.
        for pfx in ("### ", "## ", "# ", "- ", "* ", "+ ", "> "):
            if line.startswith(pfx):
                line = line[len(pfx):].strip()
                break
        # Strip ordered-list markdown like "1. ".
        if len(line) > 3 and line[0].isdigit() and line[1:3] == ". ":
            line = line[3:].strip()
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = remove_emojis(line)
        if line:
            norm = _normalize_for_dedupe(line)
            if norm and norm in seen_norm:
                continue
            if norm:
                seen_norm.add(norm)
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


WECHAT_TIME_GAP_SECONDS = 300.0


UNKNOWN_TIME_LABEL = "\u65f6\u95f4\u672a\u77e5"


def _finite_timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if math.isfinite(timestamp) else None


def _execution_timestamp(item) -> float | None:
    if not isinstance(item, dict):
        return None
    created_at = _finite_timestamp(item.get("created_at"))
    return created_at if created_at is not None else _finite_timestamp(item.get("ts"))


def compact_markdown_summary(md_text: str) -> str:
    """Remove semantic ordered-list markers without changing source Markdown."""
    output = []
    fenced = False
    fence_char = ""
    fence_length = 0
    for line in str(md_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.lstrip()
        fence = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence:
            marker = fence.group(1)
            if not fenced:
                fenced, fence_char, fence_length = True, marker[0], len(marker)
            elif (marker[0] == fence_char and len(marker) >= fence_length
                  and not stripped[len(marker):].strip()):
                fenced, fence_char, fence_length = False, "", 0
            output.append(line)
            continue
        if not fenced and not line.startswith(("    ", "\t")):
            line = re.sub(r"^(\s*(?:>\s*)*)\d+[.)](\s+)", r"\1", line)
        output.append(line)
    return "\n".join(output)


def should_show_time(prev_ts, ts, gap_seconds: float = WECHAT_TIME_GAP_SECONDS) -> bool:
    current = _finite_timestamp(ts)
    previous = _finite_timestamp(prev_ts)
    if previous is None or current is None:
        return True
    return current - previous >= float(gap_seconds)


def wechat_time_label(ts: float, now: float) -> str:
    timestamp = _finite_timestamp(ts)
    reference_timestamp = _finite_timestamp(now)
    if timestamp is None:
        return UNKNOWN_TIME_LABEL
    try:
        dates = getattr(bindings.app, "datetime", datetime)
        wall_clock = getattr(bindings.app, "time", time)
        moment = dates.fromtimestamp(timestamp)
        reference = dates.fromtimestamp(reference_timestamp if reference_timestamp is not None else wall_clock.time())
    except (ValueError, OSError, OverflowError):
        return UNKNOWN_TIME_LABEL
    clock = moment.strftime("%H:%M")
    day_delta = (reference.date() - moment.date()).days
    if day_delta <= 0:
        return clock
    if day_delta == 1:
        return f"昨天 {clock}"
    if day_delta <= 7:
        return f"星期{'一二三四五六日'[moment.weekday()]} {clock}"
    return f"{moment.year}年{moment.month}月{moment.day}日 {clock}"
