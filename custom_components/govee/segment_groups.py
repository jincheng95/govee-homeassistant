"""Parser for the user-defined segment-group option string (fork feature).

Grammar: ``Name: indices; Name: indices`` — semicolon-separated groups, each a
name and a comma/range index list, 1-based on the wire the user types
(``Left: 1-5; Right: 6-10``) and converted to 0-based indices for everything
downstream. Raises :class:`SegmentGroupsError` with a ``code`` matching a
``strings.json``/``translations/en.json`` ``options.error`` key, so the config
flow step can render it directly as a field error.
"""

from __future__ import annotations

import re
from typing import Final

from .const import SUFFIX_SEGMENT_GROUP

MAX_GROUP_SIZE: Final = 16
"""A group's frame mask is two bytes — 16 addressable bit positions."""

_GROUP_SEP: Final = ";"
_NAME_SEP: Final = ":"
_INDEX_LIST_SEP: Final = ","
_RANGE_SEP: Final = "-"
_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")


class SegmentGroupsError(Exception):
    """A rejected segment-group definition string.

    Args:
        code: One of the ``options.error`` keys in ``strings.json``.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_segment_groups(text: str, segment_count: int) -> dict[str, list[int]]:
    """Parse ``text`` into ``{group name: [0-based indices]}``.

    Args:
        text: The raw ``Name: indices; Name: indices`` string.
        segment_count: The device's segment count — the valid 1-based range
            is ``1..segment_count``.

    Returns:
        One entry per group, in the order they were written.

    Raises:
        SegmentGroupsError: For every rejection path — no groups, an empty or
            duplicate name, an empty or oversized group, an index outside the
            device's segment count, or indices claimed by more than one group.
    """
    groups: dict[str, list[int]] = {}
    claimed: set[int] = set()

    chunks = [chunk.strip() for chunk in text.split(_GROUP_SEP) if chunk.strip()]
    if not chunks:
        raise SegmentGroupsError("segment_groups_zero_groups")

    for chunk in chunks:
        if _NAME_SEP not in chunk:
            raise SegmentGroupsError("segment_groups_invalid_syntax")
        name, _, index_spec = chunk.partition(_NAME_SEP)
        name = name.strip()
        if not name:
            raise SegmentGroupsError("segment_groups_empty_name")
        if name in groups:
            raise SegmentGroupsError("segment_groups_duplicate_name")

        indices = _parse_index_spec(index_spec, segment_count)
        if not indices:
            raise SegmentGroupsError("segment_groups_empty_group")
        if len(indices) > MAX_GROUP_SIZE:
            raise SegmentGroupsError("segment_groups_too_large")

        for index in indices:
            if index in claimed:
                raise SegmentGroupsError("segment_groups_overlap")
            claimed.add(index)

        groups[name] = indices

    return groups


def _parse_index_spec(spec: str, segment_count: int) -> list[int]:
    """Expand one group's comma/range index list into 0-based indices."""
    indices: list[int] = []
    for token in spec.split(_INDEX_LIST_SEP):
        token = token.strip()
        if not token:
            continue
        if _RANGE_SEP in token:
            start_text, _, end_text = token.partition(_RANGE_SEP)
            start = _parse_one_based(start_text, segment_count)
            end = _parse_one_based(end_text, segment_count)
            if start > end:
                start, end = end, start
            indices.extend(range(start, end + 1))
        else:
            indices.append(_parse_one_based(token, segment_count))
    return indices


def _parse_one_based(token: str, segment_count: int) -> int:
    """Validate and convert a single 1-based index token to 0-based."""
    token = token.strip()
    if not token.isdigit():
        raise SegmentGroupsError("segment_groups_invalid_syntax")
    one_based = int(token)
    zero_based = one_based - 1
    if one_based < 1 or zero_based >= segment_count:
        raise SegmentGroupsError("segment_groups_out_of_range")
    return zero_based


def format_segment_groups(groups: dict[str, list[int]]) -> str:
    """The inverse of :func:`parse_segment_groups`, for re-populating the form.

    Args:
        groups: Stored ``{name: [0-based indices]}``, as saved in options.

    Returns:
        A ``Name: indices; Name: indices`` string using 1-based ranges, empty
        for no groups.
    """
    parts = []
    for name, indices in groups.items():
        parts.append(f"{name}: {_format_ranges(sorted(i + 1 for i in indices))}")
    return "; ".join(parts)


def _format_ranges(one_based: list[int]) -> str:
    """Collapse consecutive 1-based numbers into dash ranges."""
    if not one_based:
        return ""
    ranges: list[str] = []
    start = prev = one_based[0]
    for value in one_based[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = value
    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(ranges)


def group_suffix(name: str) -> str:
    """The unique-id suffix for a group entity named ``name``.

    Args:
        name: The group's user-given name.

    Returns:
        ``_segment_group_<slug>`` — see :data:`.const.SUFFIX_SEGMENT_GROUP`.
    """
    return f"{SUFFIX_SEGMENT_GROUP}{slugify_group_name(name)}"


def slugify_group_name(name: str) -> str:
    """A registry-safe slug for a group name (``"Left Bar"`` -> ``"left_bar"``)."""
    slug = _SLUG_RE.sub("_", name.strip().lower()).strip("_")
    return slug or "group"
