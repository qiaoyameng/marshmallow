"""Utilities for storing collections of error messages.

.. warning::

    This module is treated as private API.
    Users should not need to use this module directly.
"""

from marshmallow.exceptions import (
    DEFAULT_ERROR_CODE,
    SCHEMA,
    _default_detail_tree,
)


class ErrorStore:
    def __init__(self):
        #: Dictionary of errors stored during serialization
        self.errors = {}
        #: Dictionary of :class:`marshmallow.exceptions.ErrorDetail` trees
        #: parallel to ``errors``
        self.error_details = {}

    def store_error(
        self, messages, field_name=SCHEMA, index=None, detail_tree=None
    ):
        # field error  -> store/merge error messages under field name key
        # schema error -> if string or list, store/merge under _schema key
        #              -> if dict, store/merge with other top-level keys
        messages = copy_containers(messages)
        if detail_tree is None:
            detail_tree = _default_detail_tree(
                messages, code=DEFAULT_ERROR_CODE, params={}
            )
        else:
            detail_tree = copy_containers(detail_tree)
        if field_name != SCHEMA or not isinstance(messages, dict):
            messages = {field_name: messages}
            detail_tree = {field_name: detail_tree}
        if index is not None:
            messages = {index: messages}
            detail_tree = {index: detail_tree}
        self.errors = merge_errors(self.errors, messages)
        self.error_details = merge_errors(self.error_details, detail_tree)


def copy_containers(errors):
    if isinstance(errors, list):
        return [copy_containers(val) for val in errors]
    if isinstance(errors, dict):
        return {key: copy_containers(val) for key, val in errors.items()}
    return errors


def merge_errors(errors1, errors2):  # noqa: PLR0911
    """Deeply merge two error messages.

    The format of ``errors1`` and ``errors2`` matches the ``message``
    parameter of :exc:`marshmallow.exceptions.ValidationError`.
    """
    if not errors1:
        return errors2
    if not errors2:
        return errors1
    if isinstance(errors1, list):
        if isinstance(errors2, list):
            errors1.extend(errors2)
            return errors1
        if isinstance(errors2, dict):
            errors2[SCHEMA] = merge_errors(errors1, errors2.get(SCHEMA))
            return errors2
        errors1.append(errors2)
        return errors1
    if isinstance(errors1, dict):
        if isinstance(errors2, dict):
            for key, val in errors2.items():
                if key in errors1:
                    errors1[key] = merge_errors(errors1[key], val)
                else:
                    errors1[key] = val
            return errors1
        errors1[SCHEMA] = merge_errors(errors1.get(SCHEMA), errors2)
        return errors1
    if isinstance(errors2, list):
        return [errors1, *errors2]
    if isinstance(errors2, dict):
        errors2[SCHEMA] = merge_errors(errors1, errors2.get(SCHEMA))
        return errors2
    return [errors1, errors2]
