"""Field provenance tracking for deserialization.

This module implements the opt-in load result returned by
`Schema.load_with_sources <marshmallow.Schema.load_with_sources>`, which pairs
the deserialized data with the *source* of every field: whether it was
``supplied`` in the input, ``defaulted`` from its ``load_default``, or
``omitted`` from the output entirely.
"""

from __future__ import annotations

import contextvars
import enum
import typing


class FieldSource(str, enum.Enum):
    """How a field's value was obtained during deserialization.

    Sources are determined from the input data *after* ``pre_load``
    processors have run.
    """

    SUPPLIED = "supplied"
    """The field was present in the (post-``pre_load``) input data."""

    DEFAULTED = "defaulted"
    """The field was missing from the input and its ``load_default`` was used."""

    OMITTED = "omitted"
    """The field was missing from the input and produced no output value,
    e.g. because it was skipped by ``partial`` or has no ``load_default``.
    """


class SourceNode:
    """A node in the tree of field sources, mirroring the structure of the
    deserialized data.

    ``children`` is a dict keyed by output attribute for mapping nodes, a list
    for collection nodes (``many=True`` or `List <marshmallow.fields.List>` of
    nested schemas), or `None` for leaf nodes.
    """

    __slots__ = ("children", "key_map", "status")

    def __init__(
        self,
        status: FieldSource | None = None,
        children: dict[str, SourceNode] | list[SourceNode] | None = None,
    ):
        self.status = status
        self.children = children
        #: Maps external ``data_key`` to output attribute for child fields.
        self.key_map: dict[str, str] = {}

    def __repr__(self) -> str:
        return f"<SourceNode(status={self.status}, children={self.children!r})>"


def _plain_sources(node: SourceNode) -> typing.Any:
    """Convert a `SourceNode` tree into plain dicts/lists of `FieldSource`."""
    children = node.children
    if isinstance(children, dict):
        return {key: _plain_sources(child) for key, child in children.items()}
    if isinstance(children, list):
        return [_plain_sources(child) for child in children]
    return node.status


class LoadResult:
    """The result of `Schema.load_with_sources <marshmallow.Schema.load_with_sources>`.

    Pairs the deserialized ``data`` (post-``post_load``, so it may be a
    custom object) with the provenance of every field, queryable by path.

    .. code-block:: python

        result = UserSchema().load_with_sources({"name": "Ada"})
        result.data  # {'name': 'Ada', 'age': 0}
        result.source_of("name")  # FieldSource.SUPPLIED
        result.source_of("age")  # FieldSource.DEFAULTED
    """

    def __init__(self, data: typing.Any, root: SourceNode):
        self.data = data
        self._root = root

    def __repr__(self) -> str:
        return f"<LoadResult(data={self.data!r})>"

    @property
    def sources(self) -> typing.Any:
        """The field sources as a plain nested structure of `FieldSource`
        values, mirroring the shape of the deserialized data. Keys are output
        attributes; collections are represented as lists.
        """
        return _plain_sources(self._root)

    def source_of(self, path: str | typing.Sequence[str | int]) -> FieldSource | None:
        """Return the `FieldSource` for the field at ``path``.

        ``path`` may be a dot-delimited string or a sequence of segments.
        Segments are output attributes, but external ``data_key`` values are
        also resolved. Integer segments index into collections
        (``many=True`` or `List <marshmallow.fields.List>` of nested schemas).

        :raises KeyError: If the path does not correspond to a known field.
        """
        segments = path.split(".") if isinstance(path, str) else list(path)
        node = self._root
        for segment in segments:
            node = self._resolve(node, segment)
        return node.status

    @staticmethod
    def _resolve(node: SourceNode, segment: str | int) -> SourceNode:
        children = node.children
        if isinstance(children, list):
            try:
                index = int(segment)
            except (TypeError, ValueError):
                raise KeyError(segment) from None
            try:
                return children[index]
            except IndexError:
                raise KeyError(segment) from None
        if isinstance(children, dict):
            key = str(segment)
            if key in children:
                return children[key]
            mapped = node.key_map.get(key)
            if mapped is not None and mapped in children:
                return children[mapped]
        raise KeyError(segment)


#: Sink used to hand child source nodes from a nested schema load up to the
#: parent schema currently deserializing the corresponding field. The parent
#: sets a fresh list around each field deserialization; any nested schema
#: loads append their root node to it.
_source_sink: contextvars.ContextVar[list[SourceNode] | None] = contextvars.ContextVar(
    "marshmallow_source_sink", default=None
)
