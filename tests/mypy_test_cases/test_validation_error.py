from __future__ import annotations

import marshmallow as ma

# OK types for 'message'
ma.ValidationError("foo")
ma.ValidationError(["foo"])
ma.ValidationError({"foo": "bar"})

# non-OK types for 'message'
ma.ValidationError(0)  # type: ignore[arg-type]

# 'messages' is a dict|list
err = ma.ValidationError("foo")
a: dict | list = err.messages
# union type can't assign to non-union type
b: str = err.messages  # type: ignore[assignment]
c: dict = err.messages  # type: ignore[assignment]
# 'messages_dict' is a dict, so that it can assign to a dict
d: dict = err.messages_dict

# machine-readable details
detail: ma.ErrorDetail = err.details[0]
code: str = detail.code
params: dict = detail.params
path: tuple = detail.path

ma.ValidationError("foo", code="custom", params={"input": 1})
default_code: str = ma.DEFAULT_ERROR_CODE
