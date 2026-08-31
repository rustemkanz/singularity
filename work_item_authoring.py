"""Parse a lightly-structured plan file into a flat list of child work items.

Used only by ``sg draft-items`` to turn "here is the work I just did" notes into
a previewed, approval-gated batch of child work items under one parent. Scope is
deliberately tiny: type, title, description, tags, assignee. Nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
import html as html_module
import json
import re

from errors import CliError


RECOGNIZED_TYPES = (
    "Bug",
    "User Story",
    "Task",
    "Feature",
    "Epic",
    "Issue",
    "Product Backlog Item",
)
_TYPES_BY_CASEFOLD = {name.casefold(): name for name in RECOGNIZED_TYPES}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_LABEL_PREFIX_RE = re.compile(r"^P\d+(?:\.\d+)*\s*[-—:.)]\s+", re.IGNORECASE)
_TYPE_TOKEN_RE = re.compile(r"\[(bug|user story|task|feature|epic|issue|product backlog item)\]", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")


@dataclass(frozen=True)
class ItemSpec:
    """One child work item to create. ``None`` overrides fall back to CLI defaults."""

    type: str
    title: str
    description_html: str
    tags: tuple[str, ...] | None = None
    assigned_to: str | None = None


def _render_inline(text: str) -> str:
    escaped = html_module.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def markdown_to_html(markdown: str) -> str:
    """Render a small Markdown subset to HTML for an Azure DevOps rich-text field.

    Supports paragraphs, ``-``/``*`` bullet lists, ``**bold**``, ``` `code` ```,
    and treats ``###``+ headings as bold lead-ins. Not a general renderer.
    """
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    bullets: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append("<p>" + "<br>".join(_render_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            blocks.append("<ul>" + "".join(f"<li>{_render_inline(item)}</li>" for item in bullets) + "</ul>")
            bullets.clear()

    for line in lines:
        stripped = line.strip()
        heading = _HEADING_RE.match(line)
        bullet = _BULLET_RE.match(line)
        if not stripped:
            flush_paragraph()
            flush_bullets()
        elif heading:
            flush_paragraph()
            flush_bullets()
            blocks.append(f"<p><strong>{_render_inline(heading.group(2))}</strong></p>")
        elif bullet:
            flush_paragraph()
            bullets.append(bullet.group(1).strip())
        else:
            flush_bullets()
            paragraph.append(stripped)

    flush_paragraph()
    flush_bullets()
    return "".join(blocks)


def _normalize_type(raw: str | None, *, default_type: str) -> str:
    candidate = (raw or "").strip() or default_type
    normalized = _TYPES_BY_CASEFOLD.get(candidate.casefold())
    if normalized is None:
        raise CliError(
            f"ERROR: Unsupported work-item type '{candidate}'. "
            f"Use one of: {', '.join(RECOGNIZED_TYPES)}."
        )
    return normalized


def _parse_markdown(text: str, *, default_type: str) -> list[ItemSpec]:
    specs: list[ItemSpec] = []
    current_title: str | None = None
    current_type = default_type
    body: list[str] = []

    def flush() -> None:
        nonlocal current_title
        if current_title is None:
            return
        if not current_title.strip():
            raise CliError("ERROR: A plan section has an empty title.")
        specs.append(
            ItemSpec(
                type=current_type,
                title=current_title.strip(),
                description_html=markdown_to_html("\n".join(body).strip("\n")),
            )
        )
        current_title = None
        body.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        heading = _HEADING_RE.match(line)
        level = len(heading.group(1)) if heading else 0
        if level == 2:
            flush()
            title = heading.group(2)
            type_match = _TYPE_TOKEN_RE.search(title)
            current_type = _normalize_type(
                type_match.group(1) if type_match else None,
                default_type=default_type,
            )
            title = _TYPE_TOKEN_RE.sub("", title).strip()
            title = _LABEL_PREFIX_RE.sub("", title).strip()
            current_title = title
        elif level == 1:
            flush()
        elif current_title is not None:
            body.append(line)

    flush()
    if not specs:
        raise CliError(
            "ERROR: No '## <title>' sections found in the plan file. "
            "Each child work item is one level-2 heading."
        )
    return specs


def _parse_json(text: str, *, default_type: str) -> list[ItemSpec]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(f"ERROR: Plan file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or not data["items"]:
        raise CliError('ERROR: JSON plan must be {"items": [ {"title": ...}, ... ]} with at least one item.')

    specs: list[ItemSpec] = []
    for index, entry in enumerate(data["items"], start=1):
        if not isinstance(entry, dict):
            raise CliError(f"ERROR: JSON plan item #{index} is not an object.")
        title = str(entry.get("title") or "").strip()
        if not title:
            raise CliError(f"ERROR: JSON plan item #{index} has no title.")
        item_type = _normalize_type(entry.get("type"), default_type=default_type)
        if isinstance(entry.get("descriptionHtml"), str):
            description_html = entry["descriptionHtml"]
        else:
            description_html = markdown_to_html(str(entry.get("description") or ""))
        tags = entry.get("tags")
        if tags is not None and not isinstance(tags, list):
            raise CliError(f"ERROR: JSON plan item #{index} 'tags' must be a list.")
        assigned_to = entry.get("assignedTo")
        specs.append(
            ItemSpec(
                type=item_type,
                title=title,
                description_html=description_html,
                tags=tuple(str(tag) for tag in tags) if tags is not None else None,
                assigned_to=str(assigned_to) if assigned_to else None,
            )
        )
    return specs


def parse_item_specs(path: str, *, default_type: str) -> list[ItemSpec]:
    """Read ``path`` (``.json`` or Markdown) into an ordered list of ``ItemSpec``."""
    _normalize_type(default_type, default_type=default_type)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise CliError(f"ERROR: Could not read plan file '{path}': {exc}") from exc

    if path.lower().endswith(".json"):
        return _parse_json(text, default_type=default_type)
    return _parse_markdown(text, default_type=default_type)


def html_to_summary(html_text: str, *, limit: int = 140) -> str:
    """Flatten rendered HTML to a one-line summary for the preview."""
    text = re.sub(r"<[^>]+>", " ", html_text or "")
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
