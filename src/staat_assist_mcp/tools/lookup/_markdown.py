"""Turns what STAAT insights returns (HTML, or JSON in older builds) into markdown for the LLM."""

import re
from typing import Any

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s[^>]*)?/?>")
_BLANK_LINES = re.compile(r"\n{3,}")


def looks_like_html(value: str) -> bool:
    """True when the value holds at least one HTML tag, so it is worth running through a converter."""
    return _TAG.search(value) is not None


def html_to_markdown(html: str) -> str:
    """Headings, lists, emphasis and pipe tables. Styles, scripts and comments are dropped."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript", "head"]):
        element.decompose()
    markdown = MarkdownConverter(heading_style="ATX", bullets="-").convert_soup(soup)
    return _BLANK_LINES.sub("\n\n", markdown).strip()


def json_to_markdown(title: str, payload: Any) -> str:
    """A JSON document as markdown: values become "- **key:** value" items, nested objects and
    lists become headed sections. HTML inside string values is converted too."""
    lines = [f"# {title.strip()}", ""]
    _append(lines, payload, depth=1)
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip() + "\n"


def _append(lines: list[str], node: Any, depth: int) -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            if isinstance(item, dict | list):
                if index:
                    # Top-level items are separate documents (one per household): a rule keeps them apart.
                    lines.extend(["", "---", ""] if depth == 1 else [""])
                _append(lines, item, depth)
            elif item is not None:
                lines.append(f"- {_scalar(item)}")
    elif isinstance(node, dict):
        for key, value in node.items():
            if value is None:
                continue
            if isinstance(value, dict | list):
                lines.extend(["", f"{'#' * min(depth + 1, 6)} {key}", ""])
                _append(lines, value, depth + 1)
                lines.append("")
            else:
                text = _scalar(value)
                # A converted table or several paragraphs get their own block under the key.
                lines.extend(["", f"**{key}:**", "", text, ""] if "\n" in text else [f"- **{key}:** {text}"])
    elif node is not None:
        lines.append(_scalar(node))


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return html_to_markdown(value) if looks_like_html(value) else value.strip()
    return str(value)
