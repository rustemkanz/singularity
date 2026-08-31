"""Parse a work-item reference (a bare id or a provider URL) into an integer id.

The PR commands already accept a full URL; work-item commands historically took
only a bare id. ``work_item_id_arg`` is an ``argparse`` ``type=`` callable so that
pasting an Azure DevOps ``.../_workitems/edit/<id>`` link (or a GitLab issue URL)
works anywhere an id is expected.
"""

from __future__ import annotations

import argparse
import re
import urllib.parse


_ADO_EDIT_PATH = re.compile(r"/_workitems/edit/(\d+)")
_GITLAB_ISSUE_PATH = re.compile(r"/-/issues/(\d+)")
_QUERY_ID = re.compile(r"(?:^|&)id=(\d+)(?:&|$)")


def parse_work_item_reference(value: str) -> int:
    """Return the work-item id for a bare number or a supported work-item URL."""
    text = (value or "").strip()
    if not text:
        raise ValueError("empty work-item reference")

    if text.isdigit():
        item_id = int(text)
        if item_id <= 0:
            raise ValueError(f"work-item id must be positive: {value!r}")
        return item_id

    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            f"expected a work-item id or an Azure DevOps / GitLab work-item URL, got {value!r}"
        )

    for pattern in (_ADO_EDIT_PATH, _GITLAB_ISSUE_PATH):
        match = pattern.search(parsed.path)
        if match:
            return int(match.group(1))

    query_match = _QUERY_ID.search(parsed.query)
    if query_match:
        return int(query_match.group(1))

    raise ValueError(f"could not find a work-item id in URL {value!r}")


def work_item_id_arg(value: str) -> int:
    """``argparse`` ``type=`` wrapper that raises ``ArgumentTypeError``."""
    try:
        return parse_work_item_reference(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
