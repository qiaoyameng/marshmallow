"""Exception classes for marshmallow-related errors."""

from __future__ import annotations

import typing

# Key used for schema-level validation errors
SCHEMA = "_schema"

#: Default :attr:`ErrorDetail.code` assigned to errors that do not declare a
#: stable code, including plain-string or dict ``ValidationError`` messages
#: raised by third-party code.
DEFAULT_ERROR_CODE = "invalid"


class ErrorDetail(typing.NamedTuple):
    """Machine-readable information about a single validation error.

    Unlike the human-readable, already-formatted English messages exposed on
    :attr:`ValidationError.messages`, ``ErrorDetail`` keeps the pieces a
    client needs to render its own localized message.

    :param code: Stable identifier of the error, e.g. ``"required"``.
    :param message: The human-readable, formatted message (kept for reference).
    :param params: Unformatted parameters that were interpolated into the
        message template, e.g. ``{"input": -1}``.
    :param path: Location of the offending value in the raw input as a
        sequence of data keys and list indices, e.g. ``["items", 2, "qty"]``.
        It is empty for schema-level errors.
    """

    code: str
    message: str
    params: dict[str, typing.Any]
    path: tuple[typing.Union[str, int], ...] = ()


def _default_detail_tree(
    messages: typing.Any,
    *,
    code: str = DEFAULT_ERROR_CODE,
    params: dict[str, typing.Any] | None = None,
) -> typing.Any:
    """Build a detail tree with the exact same shape as ``messages``.

    Every string message leaf becomes an :class:`ErrorDetail`; dict and list
    containers are mirrored so both trees stay aligned.
    """
    params = params or {}
    if isinstance(messages, list):
        return [
            _default_detail_tree(message, code=code, params=params)
            for message in messages
        ]
    if isinstance(messages, dict):
        return {
            key: _default_detail_tree(value, code=code, params=params)
            for key, value in messages.items()
        }
    return ErrorDetail(
        code=code, message=str(messages), params=dict(params), path=()
    )


def _flatten_detail_tree(
    detail_tree: typing.Any,
    path: tuple[typing.Union[str, int], ...] = (),
) -> list[ErrorDetail]:
    """Flatten a detail tree into a list of :class:`ErrorDetail`.

    The location of each leaf within the tree is attached as its ``path``, so
    list indices and data keys are preserved.
    """
    details: list[ErrorDetail] = []
    if isinstance(detail_tree, ErrorDetail):
        details.append(detail_tree._replace(path=path + detail_tree.path))
    elif isinstance(detail_tree, list):
        for item in detail_tree:
            details.extend(_flatten_detail_tree(item, path))
    elif isinstance(detail_tree, dict):
        for key, value in detail_tree.items():
            key_path = path if key == SCHEMA else (*path, key)
            details.extend(_flatten_detail_tree(value, key_path))
    return details


class MarshmallowError(Exception):
    """Base class for all marshmallow-related errors."""


class ValidationError(MarshmallowError):
    """Raised when validation fails on a field or schema.

    Validators and custom fields should raise this exception.

    :param message: An error message, list of error messages, or dict of
        error messages. If a dict, the keys are subitems and the values are error messages.
    :param field_name: Field name to store the error on.
    :param data: Raw input data.
    :param valid_data: Valid (de)serialized data.
    :param code: Stable machine-readable error code. Defaults to
        :data:`DEFAULT_ERROR_CODE`. Applies to every string message in
        ``message`` unless ``detail_tree`` is given.
    :param params: Unformatted parameters used to render the message(s),
        exposed on each :class:`ErrorDetail` so that clients can localize the
        message themselves.
    :param detail_tree: Private, internal use only. A structure parallel to
        ``messages`` holding :class:`ErrorDetail` objects, used while errors
        bubble up through fields and schemas.
    """

    def __init__(
        self,
        message: str | list | dict,
        field_name: str = SCHEMA,
        data: typing.Mapping[str, typing.Any]
        | typing.Iterable[typing.Mapping[str, typing.Any]]
        | None = None,
        valid_data: list[typing.Any] | dict[str, typing.Any] | None = None,
        *,
        code: str = DEFAULT_ERROR_CODE,
        params: dict[str, typing.Any] | None = None,
        detail_tree: typing.Any = None,
        **kwargs,
    ):
        self.messages = [message] if isinstance(message, (str, bytes)) else message
        self.field_name = field_name
        self.data = data
        self.valid_data = valid_data
        self.kwargs = kwargs
        self.code = code
        self.params = dict(params or ())
        #: Tree of :class:`ErrorDetail` objects, parallel to ``messages``.
        self.detail_tree: typing.Any = (
            detail_tree
            if detail_tree is not None
            else _default_detail_tree(self.messages, code=code, params=self.params)
        )
        super().__init__(message)

    def normalized_messages(self):
        if self.field_name == SCHEMA and isinstance(self.messages, dict):
            return self.messages
        return {self.field_name: self.messages}

    def normalized_detail_tree(self):
        if self.field_name == SCHEMA and isinstance(self.detail_tree, dict):
            return self.detail_tree
        return {self.field_name: self.detail_tree}

    @property
    def details(self) -> list[ErrorDetail]:
        """Flat list of all :class:`ErrorDetail` errors carried by this exception.

        Each detail's :attr:`ErrorDetail.path` identifies the offending value
        in the raw input using data keys and list indices.
        """
        return _flatten_detail_tree(self.normalized_detail_tree())

    @property
    def messages_dict(self) -> dict[str, typing.Any]:
        if not isinstance(self.messages, dict):
            raise TypeError(
                "cannot access 'messages_dict' when 'messages' is of type "
                + type(self.messages).__name__
            )
        return self.messages


class RegistryError(NameError):
    """Raised when an invalid operation is performed on the serializer
    class registry.
    """


class StringNotCollectionError(MarshmallowError, TypeError):
    """Raised when a string is passed when a list of strings is expected."""


class _FieldInstanceResolutionError(MarshmallowError, TypeError):
    """Raised when an argument is passed to a field class that cannot be resolved to a Field instance."""
