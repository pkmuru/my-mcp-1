"""Turns what STAAT insights returns (HTML, or JSON in older builds) into plain text: the smallest output,
for when even markdown is too long. Same output as the .NET lookup service's "text" format."""

import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, PageElement, Tag
from bs4.element import PreformattedString

from ._markdown import looks_like_html

_LINE_BREAK = re.compile(r"\r\n?|\n")
_SPACES = re.compile(r"[  ]+")
_CELL_SEPARATORS = re.compile(r" *\t+ *")
_BLANK_LINES = re.compile(r"\n{3,}")
_INDENT = "  "

# Tags that stand apart as a paragraph: a blank line before and after.
_PARAGRAPH_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote", "pre", "hr", "dl",
}  # fmt: skip
# Tags that start a new line, but not a new paragraph.
_LINE_TAGS = {
    "div", "section", "article", "header", "footer", "main", "aside", "nav",
    "li", "thead", "tbody", "tfoot", "tr", "dt", "dd", "address", "figure", "figcaption",
}  # fmt: skip


def html_to_text(html: str) -> str:
    """One line per block or table row, cells separated by a tab. Styles, scripts and comments are dropped."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript", "head"]):
        element.decompose()
    parts: list[str] = []
    _append_html(parts, soup.body or soup)
    lines = (
        _CELL_SEPARATORS.sub("\t", _SPACES.sub(" ", line)).strip() for line in "".join(parts).split("\n")
    )
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _append_html(parts: list[str], node: PageElement) -> None:
    if isinstance(node, NavigableString):
        if not isinstance(node, PreformattedString):  # comments, doctypes, CDATA: not content
            parts.append(_LINE_BREAK.sub(" ", node))
        return
    if not isinstance(node, Tag):
        return
    if node.name == "br":
        # A line break inside a table cell must not break the row.
        parts.append(" " if node.find_parent(["td", "th"]) else "\n")
        return

    newlines = 2 if node.name in _PARAGRAPH_TAGS else 1 if node.name in _LINE_TAGS else 0
    _ensure_newlines(parts, newlines)
    for child in node.children:
        _append_html(parts, child)
    if node.name in ("td", "th"):
        parts.append("\t")
    _ensure_newlines(parts, newlines)


def _ensure_newlines(parts: list[str], count: int) -> None:
    """Appends line breaks until the text ends with `count` of them (ignoring trailing spaces and tabs)."""
    if not count or not parts:
        return
    existing = 0
    trailing_blanks = True
    for char in (char for part in reversed(parts) for char in reversed(part)):
        if trailing_blanks and char in " \t":
            continue
        trailing_blanks = False
        if char != "\n" or existing == count:
            break
        existing += 1
    parts.append("\n" * (count - existing))


def json_to_text(title: str, payload: Any) -> str:
    """A JSON document as plain text: "key: value" lines, nested objects and lists indented under their
    key, list items repeated. HTML inside string values is reduced to its text."""
    lines = [title.strip(), ""]
    _append_json(lines, payload, depth=0)
    return "\n".join(lines).rstrip() + "\n"


def _append_json(lines: list[str], node: Any, depth: int) -> None:
    indent = _INDENT * depth
    if isinstance(node, list):
        for index, item in enumerate(node):
            if isinstance(item, dict | list):
                if index:
                    lines.append("")
                _append_json(lines, item, depth)
            elif item is not None:
                _append_block(lines, "- ", _scalar(item), depth)
    elif isinstance(node, dict):
        for key, value in node.items():
            if value is None:
                continue
            if isinstance(value, dict | list):
                lines.append(f"{indent}{key}:")
                _append_json(lines, value, depth + 1)
                continue
            text = _scalar(value)
            if "\n" in text:
                lines.append(f"{indent}{key}:")
                _append_block(lines, "", text, depth + 1)
            else:
                lines.append(f"{indent}{key}: {text}")
    elif node is not None:
        _append_block(lines, "", _scalar(node), depth)


def _append_block(lines: list[str], prefix: str, block: str, depth: int) -> None:
    """Every line of `block` at `depth`, the first one behind `prefix` and the others aligned with it."""
    indent = _INDENT * depth
    first, *rest = block.split("\n")
    lines.append(f"{indent}{prefix}{first}")
    lines.extend(f"{indent}{' ' * len(prefix)}{line}" for line in rest)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return html_to_text(value) if looks_like_html(value) else value.strip()
    return str(value)
