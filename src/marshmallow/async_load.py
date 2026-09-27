"""Utilities for asynchronous deserialization.

This module implements the machinery behind `Schema.load_async <marshmallow.Schema.load_async>`.
It allows field-level and schema-level validators to return awaitables, which are
run concurrently with an optional, shared concurrency limit.

.. warning::

    This module is treated as private API.
    Users should not need to use this module directly.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import typing

from marshmallow.error_store import ErrorStore, merge_errors
from marshmallow.exceptions import MarshmallowError, ValidationError

if typing.TYPE_CHECKING:
    from marshmallow.schema import Schema

_CURRENT_LOAD_CONTEXT: contextvars.ContextVar[_AsyncLoadContext | None] = (
    contextvars.ContextVar("marshmallow_async_load_context", default=None)
)


def current_load_context() -> _AsyncLoadContext | None:
    """Return the async load context for the in-progress `Schema.load_async` call,
    or `None` if no asynchronous load is in progress.
    """
    return _CURRENT_LOAD_CONTEXT.get()


def reject_awaitable(awaitable: typing.Awaitable) -> typing.NoReturn:
    """Raise a usage error for an awaitable returned by a validator during a
    synchronous load.
    """
    # Close coroutines to avoid "coroutine was never awaited" warnings
    if inspect.iscoroutine(awaitable):
        awaitable.close()
    raise MarshmallowError(
        "A validator returned an awaitable during synchronous deserialization. "
        "Schema.load() cannot run asynchronous validators. "
        "Use Schema.load_async() instead."
    )


class _AsyncLoadContext:
    """State shared by one `Schema.load_async` call, including nested schemas.

    Holds the semaphore limiting concurrent asynchronous validators and the
    list of scopes (one per schema involved in the load) in registration order.
    """

    def __init__(self, max_concurrency: int | None = None):
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer or None.")
        self.semaphore = (
            asyncio.Semaphore(max_concurrency) if max_concurrency is not None else None
        )
        self.scopes: list[_AsyncLoadScope] = []

    def new_scope(self) -> _AsyncLoadScope:
        scope = _AsyncLoadScope(self)
        self.scopes.append(scope)
        return scope

    def _tasks(self) -> list[asyncio.Task]:
        return [task for scope in self.scopes for task in scope.tasks]

    async def run(self) -> None:
        """Run all scheduled validator tasks, then merge their errors.

        Errors are merged scope by scope in reverse registration order
        (innermost schemas first) so that nested errors are available when
        parent scopes collect them. The merge order is fully determined by
        declaration and input order, never by task completion order.

        If the surrounding coroutine is cancelled, the spawned tasks are
        cancelled and awaited before the cancellation propagates.
        """
        tasks = self._tasks()
        if tasks:
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                await self._shutdown(tasks)
                raise
        for scope in reversed(self.scopes):
            scope.merge()

    async def shutdown(self) -> None:
        """Cancel and await all spawned tasks. Used when loading fails before
        the scheduled validators could be run.
        """
        await self._shutdown(self._tasks())

    async def _shutdown(self, tasks: list[asyncio.Task]) -> None:
        for task in tasks:
            task.cancel()
        if tasks:
            # Wait for the spawned tasks to exit before propagating
            await asyncio.gather(*tasks, return_exceptions=True)
        # Close awaitables that were scheduled but never started, so they
        # are not reported as "never awaited"
        for scope in self.scopes:
            scope.close_unstarted()


class _AsyncLoadScope:
    """Per-schema bookkeeping for an asynchronous load.

    Tracks the validator tasks scheduled by one schema (in declaration/input
    order) and the deferred nested loads recorded while deserializing its
    fields.
    """

    def __init__(self, context: _AsyncLoadContext):
        self.context = context
        #: The error store of the schema this scope belongs to. Bound by the schema.
        self.error_store: ErrorStore | None = None
        #: Deferred nested loads, as (field_name, index, value) triples
        self.deferreds: list[tuple[str, int | None, typing.Any]] = []
        #: Scheduled validators, in scheduling order
        self.slots: list[_AsyncValidatorSlot] = []
        #: Spawned tasks, in scheduling order
        self.tasks: list[asyncio.Task] = []

    def schedule(
        self,
        awaitable: typing.Awaitable,
        slot_store: ErrorStore,
        *,
        on_error: typing.Callable[[ValidationError], None],
        on_result: typing.Callable[[typing.Any], None] | None = None,
    ) -> None:
        """Schedule a validator awaitable, bounded by the shared semaphore.

        :param awaitable: The awaitable returned by the validator.
        :param slot_store: Error store to collect the validator's errors into.
            Stores are merged into the schema's error store in scheduling
            order once all tasks have completed.
        :param on_error: Called with the `ValidationError` if the validator
            fails. Should store the error in ``slot_store``.
        :param on_result: Called with the validator's return value if it
            succeeds.
        """
        semaphore = self.context.semaphore
        slot = _AsyncValidatorSlot(awaitable, slot_store)
        self.slots.append(slot)

        async def task_coro() -> None:
            if semaphore is not None:
                await semaphore.acquire()
            try:
                slot.started = True
                try:
                    result = await awaitable
                except ValidationError as error:
                    on_error(error)
                else:
                    if on_result is not None:
                        on_result(result)
            finally:
                if semaphore is not None:
                    semaphore.release()

        self.tasks.append(asyncio.ensure_future(task_coro()))

    def merge(self) -> None:
        """Merge deferred nested errors and validator errors into the error store."""
        error_store = typing.cast("ErrorStore", self.error_store)
        for field_name, index, value in self.deferreds:
            collected = collect_deferred_errors(value)
            if collected:
                error_store.store_error(collected, field_name, index=index)
        for slot in self.slots:
            if slot.store.errors:
                error_store.errors = merge_errors(error_store.errors, slot.store.errors)

    def close_unstarted(self) -> None:
        """Close awaitables whose task was cancelled before they started."""
        for slot in self.slots:
            if not slot.started and inspect.iscoroutine(slot.awaitable):
                slot.awaitable.close()


class _AsyncValidatorSlot:
    """Bookkeeping for a single scheduled asynchronous validator."""

    __slots__ = ("awaitable", "store", "started")

    def __init__(self, awaitable: typing.Awaitable, store: ErrorStore):
        self.awaitable = awaitable
        self.store = store
        #: Whether the awaitable started running. If a load is cancelled
        #: before the awaitable started, it is closed explicitly.
        self.started = False


class _DeferredNested:
    """The result of a nested schema load whose asynchronous validators and
    post_load processors have been deferred to the asynchronous phase of the
    enclosing `Schema.load_async` call.
    """

    __slots__ = ("schema", "result", "error_store", "many", "partial", "unknown", "data")

    def __init__(
        self,
        *,
        schema: Schema,
        result: typing.Any,
        error_store: ErrorStore,
        many: bool,
        partial: typing.Any,
        unknown: typing.Any,
        data: typing.Any,
    ):
        self.schema = schema
        #: The synchronously deserialized data, before post_load processing
        self.result = result
        #: The nested schema's error store, including deferred validator errors
        self.error_store = error_store
        self.many = many
        self.partial = partial
        self.unknown = unknown
        self.data = data


def contains_deferred(value: typing.Any) -> bool:
    """Return whether ``value`` is or contains a `_DeferredNested`."""
    if isinstance(value, _DeferredNested):
        return True
    if isinstance(value, (list, tuple)):
        return any(contains_deferred(item) for item in value)
    if isinstance(value, dict):
        return any(contains_deferred(item) for item in value.values())
    return False


def collect_deferred_errors(value: typing.Any) -> typing.Any | None:
    """Collect errors from deferred nested loads within ``value``.

    Returns an error structure matching the shape of ``value`` (lists and
    tuples map to dicts of index -> errors), or `None` if there are no errors.
    """
    if isinstance(value, _DeferredNested):
        return value.error_store.errors or None
    if isinstance(value, (list, tuple)):
        errors = {}
        for idx, item in enumerate(value):
            item_errors = collect_deferred_errors(item)
            if item_errors:
                errors[idx] = item_errors
        return errors or None
    if isinstance(value, dict):
        errors = {}
        for key, item in value.items():
            item_errors = collect_deferred_errors(item)
            if item_errors:
                errors[key] = item_errors
        return errors or None
    return None


def unwrap_deferred(value: typing.Any, *, finalize: bool) -> typing.Any:
    """Replace deferred nested loads within ``value`` with their final values.

    If ``finalize`` is true, run the deferred post_load processors. Processing
    is recursive, so innermost post_load processors run first.
    """
    if isinstance(value, _DeferredNested):
        result = unwrap_deferred(value.result, finalize=finalize)
        if finalize:
            return value.schema._finalize_deferred(value, result)
        return result
    if isinstance(value, list):
        return [unwrap_deferred(item, finalize=finalize) for item in value]
    if isinstance(value, tuple):
        return tuple(unwrap_deferred(item, finalize=finalize) for item in value)
    if isinstance(value, dict):
        return type(value)(
            (key, unwrap_deferred(item, finalize=finalize))
            for key, item in value.items()
        )
    return value
