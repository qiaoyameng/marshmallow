"""Exception classes for marshmallow-related errors."""

from __future__ import annotations

import typing

#: Stable code used when an error does not provide explicit details.
DEFAULT_VALIDATION_ERROR_CODE = "invalid"

# Key used for schema-level validation errors
SCHEMA = "_schema"


class ErrorDetail(typing.TypedDict):
    """Program-readable information about one validation error."""

    code: str
    params: dict[str, typing.Any]
    path: list[typing.Any]


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
    :param details: Program-readable error details.
    :param code: Stable code used when ``details`` is not supplied.
    :param params: Unformatted parameters used when ``details`` is not supplied.
    """

    def __init__(
        self,
        message: str | bytes | list | dict,
        field_name: str = SCHEMA,
        data: typing.Mapping[str, typing.Any]
        | typing.Iterable[typing.Mapping[str, typing.Any]]
        | None = None,
        valid_data: list[typing.Any] | dict[str, typing.Any] | None = None,
        details: typing.Sequence[ErrorDetail] | ErrorDetail | None = None,
        code: str | None = None,
        params: dict[str, typing.Any] | None = None,
        **kwargs,
    ):
        self.messages = [message] if isinstance(message, (str, bytes)) else message
        self.details = self._normalize_details(details, code, params, self.messages)
        self.field_name = field_name
        self.data = data
        self.valid_data = valid_data
        self.kwargs = kwargs
        super().__init__(message)

    @staticmethod
    def _detail(
        code: str = DEFAULT_VALIDATION_ERROR_CODE,
        params: dict[str, typing.Any] | None = None,
        path: typing.Sequence[typing.Any] = (),
    ) -> ErrorDetail:
        return {"code": code, "params": dict(params or {}), "path": list(path)}

    @classmethod
    def _normalize_details(
        cls,
        details: typing.Sequence[ErrorDetail] | ErrorDetail | None,
        code: str | None,
        params: dict[str, typing.Any] | None,
        messages: str | bytes | list | dict,
    ) -> list[ErrorDetail]:
        if details is not None:
            if isinstance(details, typing.Mapping):
                details = [typing.cast(ErrorDetail, details)]
            return [
                cls._detail(
                    detail.get("code", DEFAULT_VALIDATION_ERROR_CODE),
                    detail.get("params", {}),
                    detail.get("path", []),
                )
                for detail in details
            ]
        if code is not None:
            return [cls._detail(code, params)]
        return cls._details_from_messages(messages)

    @classmethod
    def _details_from_messages(
        cls, messages: typing.Any, path: tuple[typing.Any, ...] = ()
    ) -> list[ErrorDetail]:
        if isinstance(messages, typing.Mapping):
            details: list[ErrorDetail] = []
            for key, value in messages.items():
                details.extend(cls._details_from_messages(value, (*path, key)))
            return details
        if isinstance(messages, list):
            details = []
            for index, message in enumerate(messages):
                if isinstance(message, typing.Mapping):
                    details.append(cls._detail(params={"message": message}, path=path))
                elif isinstance(message, list):
                    details.extend(
                        cls._details_from_messages(message, (*path, index))
                    )
                else:
                    details.append(cls._detail(path=path))
            return details
        return [cls._detail(path=path)]

    def normalized_messages(self):
        if self.field_name == SCHEMA and isinstance(self.messages, dict):
            return self.messages
        return {self.field_name: self.messages}

    @property
    def messages_dict(self) -> dict[str, typing.Any]:
        if not isinstance(self.messages, dict):
            raise TypeError(
                "cannot access 'messages_dict' when 'messages' is of type "
                + type(self.messages).__name__
            )
        return self.messages
