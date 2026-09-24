"""Opt-in tracking of where each field's value came from during deserialization.

This module powers `Schema.load_with_provenance <marshmallow.Schema.load_with_provenance>`.
It distinguishes, for every load field, whether the value was

- ``supplied``: present in the input (as seen after ``pre_load`` processing),
- ``defaulted``: absent from the input and filled in from the field's
  ``load_default``, or
- ``omitted``: absent from the input and left out of the result, either
  because ``partial`` allowed it to be skipped or because the field has no
  ``load_default``.
"""

from __future__ import annotations

import contextlib
import contextvars
import typing

SUPPLIED: typing.Final = "supplied"
"""The field's key was present in the (post-``pre_load``) input data."""

DEFAULTED: typing.Final = "defaulted"
"""The field was missing from the input and its ``load_default`` was used."""

OMITTED: typing.Final = "omitted"
"""The field was missing from the input and no value was loaded for it."""

#: A path to a field within (possibly nested) loaded data. Segments are
#: attribute names for mapping values and indices for collection items.
FieldPath = tuple[str | int, ...]

#: Mapping of field paths to their source (``supplied``/``defaulted``/``omitted``).
SourceMap = dict[FieldPath, str]

_T = typing.TypeVar("_T")


def _parse_path(path: str) -> FieldPath:
    parts: list[str | int] = []
    for part in path.split("."):
        parts.append(int(part) if part.isdigit() else part)
    return tuple(parts)


def _format_path(path: FieldPath) -> str:
    return ".".join(str(segment) for segment in path)


class LoadResult(typing.Generic[_T]):
    """The result of `Schema.load_with_provenance <marshmallow.Schema.load_with_provenance>`.

    Wraps the deserialized ``data`` (identical to what
    `Schema.load <marshmallow.Schema.load>` returns, including any object
    produced by ``post_load`` processors) and records the source of every
    load field.

    .. versionadded:: 4.4.0
    """

    def __init__(self, data: _T, sources: SourceMap):
        #: The deserialized data.
        self.data: _T = data
        self._sources: SourceMap = dict(sources)

    @property
    def sources(self) -> dict[str, str]:
        """A mapping of dotted field paths to their source.

        Paths use attribute names (as they appear in the loaded output, not
        ``data_key``) and integer indices for items of collections, e.g.
        ``"friends.0.name"`` for a ``List`` of ``Nested`` schemas.
        """
        return {_format_path(path): source for path, source in self._sources.items()}

    def source(self, *path: str | int, default: str | None = None) -> str | None:
        """Return the source of the field at ``path``.

        The path may be given as a single dotted string
        (``result.source("friends.0.name")``) or as separate segments
        (``result.source("friends", 0, "name")``). Segments are output
        attribute names; use integer indices for collection items.

        :param default: Value returned when the path was not tracked.
        :return: ``"supplied"``, ``"defaulted"``, ``"omitted"``, or `default`.
        """
        if len(path) == 1 and isinstance(path[0], str):
            parsed = _parse_path(path[0])
            if parsed in self._sources:
                return self._sources[parsed]
            # A field whose attribute itself contains dots.
            return self._sources.get((path[0],), default)
        return self._sources.get(tuple(path), default)

    def __repr__(self) -> str:
        return f"<LoadResult(data={self.data!r}, sources={self.sources!r})>"


class _ProvenanceTracker:
    """Mutable state for a single tracked load. This class is private API."""

    def __init__(self) -> None:
        self.path: list[str | int] = []
        self.records: SourceMap = {}

    def record(self, key: str, source: str) -> None:
        self.records[(*self.path, key)] = source

    @contextlib.contextmanager
    def segment(self, key: str | int) -> typing.Iterator[None]:
        self.path.append(key)
        try:
            yield
        finally:
            self.path.pop()


_tracker_var: contextvars.ContextVar[_ProvenanceTracker | None] = (
    contextvars.ContextVar("marshmallow_provenance_tracker", default=None)
)


def current_tracker() -> _ProvenanceTracker | None:
    """Return the tracker for the in-progress tracked load, if any."""
    return _tracker_var.get()


@contextlib.contextmanager
def track_provenance() -> typing.Iterator[_ProvenanceTracker]:
    """Enable provenance tracking for the dynamic scope of the ``with`` block."""
    tracker = _ProvenanceTracker()
    token = _tracker_var.set(tracker)
    try:
        yield tracker
    finally:
        _tracker_var.reset(token)
