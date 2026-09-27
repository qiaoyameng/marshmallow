"""Tests for `Schema.load_async` and asynchronous validators."""

import asyncio

import pytest

from marshmallow import (
    Schema,
    ValidationError,
    fields,
    post_load,
    validates,
    validates_schema,
)


class FakeAsyncService:
    """A controllable stand-in for an asynchronous validation service.

    Records every call and tracks the number of concurrently running checks
    so tests can assert on the concurrency limit.
    """

    def __init__(self, *, valid=True, delay=0.01):
        self.valid = valid
        self.delay = delay
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.exited = 0

    async def check(self, value):
        self.calls.append(value)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if not self.valid:
                raise ValidationError(f"{value} was rejected")
            return value
        finally:
            self.active -= 1
            self.exited += 1


def run(coro):
    return asyncio.run(coro)


class AsyncUserSchema(Schema):
    name = fields.Str()
    age = fields.Int()

    @validates("name")
    async def check_name(self, value, **kwargs):
        await asyncio.sleep(0)
        if value == "taken":
            raise ValidationError("Name is already taken.")

    @validates_schema
    async def check_age(self, data, **kwargs):
        await asyncio.sleep(0)
        if data.get("age") == 13:
            raise ValidationError("Unlucky age.", "age")


class TestLoadAsync:
    def test_load_async_success(self):
        assert run(AsyncUserSchema().load_async({"name": "alice", "age": 30})) == {
            "name": "alice",
            "age": 30,
        }

    def test_load_async_field_validator_error(self):
        with pytest.raises(ValidationError) as excinfo:
            run(AsyncUserSchema().load_async({"name": "taken", "age": 30}))
        assert excinfo.value.messages == {"name": ["Name is already taken."]}

    def test_load_async_schema_validator_error(self):
        with pytest.raises(ValidationError) as excinfo:
            run(AsyncUserSchema().load_async({"name": "alice", "age": 13}))
        assert excinfo.value.messages == {"age": ["Unlucky age."]}

    def test_load_async_many(self):
        schema = AsyncUserSchema()
        with pytest.raises(ValidationError) as excinfo:
            run(
                schema.load_async(
                    [
                        {"name": "alice", "age": 30},
                        {"name": "taken", "age": 13},
                    ],
                    many=True,
                )
            )
        assert excinfo.value.messages == {
            1: {"name": ["Name is already taken."], "age": ["Unlucky age."]}
        }

    def test_load_async_mixed_sync_and_async_validators(self):
        class MixedSchema(Schema):
            name = fields.Str()

            @validates("name")
            def check_name_sync(self, value, **kwargs):
                if value == "sync-bad":
                    raise ValidationError("Sync error.")

            @validates("name")
            async def check_name_async(self, value, **kwargs):
                await asyncio.sleep(0)
                if value == "async-bad":
                    raise ValidationError("Async error.")

        schema = MixedSchema()
        assert run(schema.load_async({"name": "ok"})) == {"name": "ok"}
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async({"name": "sync-bad"}))
        assert excinfo.value.messages == {"name": ["Sync error."]}
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async({"name": "async-bad"}))
        assert excinfo.value.messages == {"name": ["Async error."]}

    def test_load_async_error_format_matches_sync(self):
        class SyncUserSchema(Schema):
            name = fields.Str()

            @validates("name")
            def check_name(self, value, **kwargs):
                if value == "taken":
                    raise ValidationError("Name is already taken.")

        class AsyncNameSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await asyncio.sleep(0)
                if value == "taken":
                    raise ValidationError("Name is already taken.")

        with pytest.raises(ValidationError) as sync_excinfo:
            SyncUserSchema().load({"name": "taken"})
        with pytest.raises(ValidationError) as async_excinfo:
            run(AsyncNameSchema().load_async({"name": "taken"}))
        assert sync_excinfo.value.messages == async_excinfo.value.messages

    def test_load_async_runs_post_load_after_validation(self):
        events = []

        class PostLoadSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await asyncio.sleep(0)
                events.append("validate")
                if value == "bad":
                    raise ValidationError("Invalid.")

            @post_load
            def make_object(self, item, **kwargs):
                events.append("post_load")
                return item

        assert run(PostLoadSchema().load_async({"name": "ok"})) == {"name": "ok"}
        assert events == ["validate", "post_load"]

        events.clear()
        with pytest.raises(ValidationError):
            run(PostLoadSchema().load_async({"name": "bad"}))
        assert events == ["validate"]

    def test_load_async_skips_validator_when_deserialization_fails(self):
        service = FakeAsyncService()

        class ServiceSchema(Schema):
            name = fields.Str()
            age = fields.Int()

            @validates("age")
            async def check_age(self, value, **kwargs):
                await service.check(value)

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.check(value)

        with pytest.raises(ValidationError) as excinfo:
            run(ServiceSchema().load_async({"name": "alice", "age": "not-an-int"}))
        assert excinfo.value.messages == {"age": ["Not a valid integer."]}
        # The async validator for "age" was not called, the one for "name" was
        assert service.calls == ["alice"]

    def test_load_async_invalid_max_concurrent_validations(self):
        with pytest.raises(ValueError, match="max_concurrent_validations"):
            run(AsyncUserSchema().load_async({"name": "a"}, max_concurrent_validations=0))


class TestSyncLoadUsageError:
    def test_sync_load_with_async_field_validator_raises(self):
        with pytest.raises(RuntimeError, match="load_async"):
            AsyncUserSchema().load({"name": "alice", "age": 30})

    def test_sync_load_with_async_schema_validator_raises(self):
        class AsyncSchemaValidatorSchema(Schema):
            name = fields.Str()

            @validates_schema
            async def check_schema(self, data, **kwargs):
                await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="load_async"):
            AsyncSchemaValidatorSchema().load({"name": "alice"})

    def test_sync_validate_with_async_validator_raises(self):
        with pytest.raises(RuntimeError, match="load_async"):
            AsyncUserSchema().validate({"name": "alice", "age": 30})

    def test_sync_load_still_works_without_async_validators(self):
        class SyncSchema(Schema):
            name = fields.Str()

            @validates("name")
            def check_name(self, value, **kwargs):
                if value == "bad":
                    raise ValidationError("Invalid.")

        assert SyncSchema().load({"name": "ok"}) == {"name": "ok"}
        with pytest.raises(ValidationError):
            SyncSchema().load({"name": "bad"})


class TestConcurrencyLimit:
    def test_concurrency_limit_with_many(self):
        service = FakeAsyncService(delay=0.02)

        class ItemSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.check(value)

        data = [{"name": f"item{i}"} for i in range(6)]
        result = run(
            ItemSchema().load_async(data, many=True, max_concurrent_validations=2)
        )
        assert result == data
        assert len(service.calls) == 6
        assert service.max_active == 2

    def test_concurrency_limit_shared_with_nested_schemas(self):
        service = FakeAsyncService(delay=0.02)

        class ChildSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.check(value)

        class ParentSchema(Schema):
            child = fields.Nested(ChildSchema)
            children = fields.List(fields.Nested(ChildSchema))

            @validates("children")
            async def check_children(self, value, **kwargs):
                await service.check("children")

        data = [
            {
                "child": {"name": "a"},
                "children": [{"name": "b"}, {"name": "c"}],
            }
            for _ in range(2)
        ]
        run(ParentSchema().load_async(data, many=True, max_concurrent_validations=3))
        # 2 items * (1 nested + 2 listed + 1 collection validator) checks
        assert len(service.calls) == 8
        assert service.max_active == 3

    def test_no_limit_by_default(self):
        service = FakeAsyncService(delay=0.02)

        class ItemSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.check(value)

        data = [{"name": f"item{i}"} for i in range(5)]
        run(ItemSchema().load_async(data, many=True))
        assert service.max_active == 5


class TestErrorOrdering:
    def test_error_order_does_not_depend_on_completion_speed(self):
        class OrderedSchema(Schema):
            a = fields.Str()
            b = fields.Str()

            @validates("a")
            async def check_a(self, value, **kwargs):
                # Finishes last but is declared first
                await asyncio.sleep(0.05)
                raise ValidationError("error a")

            @validates("b")
            async def check_b(self, value, **kwargs):
                # Finishes first but is declared last
                await asyncio.sleep(0)
                raise ValidationError("error b")

        for _ in range(5):
            with pytest.raises(ValidationError) as excinfo:
                run(OrderedSchema().load_async({"a": "x", "b": "y"}))
            assert list(excinfo.value.messages.keys()) == ["a", "b"]
            assert excinfo.value.messages == {"a": ["error a"], "b": ["error b"]}

    def test_error_order_with_many_follows_input_order(self):
        class ItemSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                # Earlier items take longer to fail
                delays = {"first": 0.05, "second": 0.02, "third": 0}
                await asyncio.sleep(delays[value])
                raise ValidationError(f"error {value}")

        data = [{"name": "first"}, {"name": "second"}, {"name": "third"}]
        with pytest.raises(ValidationError) as excinfo:
            run(ItemSchema().load_async(data, many=True))
        assert list(excinfo.value.messages.keys()) == [0, 1, 2]
        assert excinfo.value.messages == {
            0: {"name": ["error first"]},
            1: {"name": ["error second"]},
            2: {"name": ["error third"]},
        }

    def test_sync_and_async_errors_are_merged(self):
        class MergedSchema(Schema):
            a = fields.Int()
            b = fields.Str()

            @validates("b")
            async def check_b(self, value, **kwargs):
                await asyncio.sleep(0)
                raise ValidationError("error b")

        with pytest.raises(ValidationError) as excinfo:
            run(MergedSchema().load_async({"a": "not-an-int", "b": "x"}))
        assert excinfo.value.messages == {
            "a": ["Not a valid integer."],
            "b": ["error b"],
        }


class TestNestedAsyncValidators:
    def test_nested_errors_are_nested_under_parent_key(self):
        class ChildSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await asyncio.sleep(0)
                raise ValidationError("bad name")

        class ParentSchema(Schema):
            child = fields.Nested(ChildSchema)

        with pytest.raises(ValidationError) as excinfo:
            run(ParentSchema().load_async({"child": {"name": "x"}}))
        assert excinfo.value.messages == {"child": {"name": ["bad name"]}}

    def test_nested_list_errors_are_indexed(self):
        class ChildSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await asyncio.sleep(0)
                if value == "bad":
                    raise ValidationError("bad name")

        class ParentSchema(Schema):
            children = fields.List(fields.Nested(ChildSchema))

        with pytest.raises(ValidationError) as excinfo:
            run(ParentSchema().load_async({"children": [{"name": "ok"}, {"name": "bad"}]}))
        assert excinfo.value.messages == {"children": {1: {"name": ["bad name"]}}}

    def test_nested_many_errors_are_indexed(self):
        class ChildSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await asyncio.sleep(0)
                raise ValidationError("bad name")

        class ParentSchema(Schema):
            children = fields.Nested(ChildSchema, many=True)

        with pytest.raises(ValidationError) as excinfo:
            run(ParentSchema().load_async({"children": [{"name": "x"}]}))
        assert excinfo.value.messages == {"children": {0: {"name": ["bad name"]}}}

    def test_nested_schema_validators(self):
        class ChildSchema(Schema):
            name = fields.Str()

            @validates_schema
            async def check_schema(self, data, **kwargs):
                await asyncio.sleep(0)
                raise ValidationError("bad child")

        class ParentSchema(Schema):
            child = fields.Nested(ChildSchema)

        with pytest.raises(ValidationError) as excinfo:
            run(ParentSchema().load_async({"child": {"name": "x"}}))
        assert excinfo.value.messages == {"child": {"_schema": ["bad child"]}}


class TestCancellation:
    def test_cancellation_waits_for_spawned_validators(self):
        service = FakeAsyncService(delay=60)
        started = asyncio.Event()

        class SlowSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                started.set()
                await service.check(value)

        async def main():
            task = asyncio.create_task(SlowSchema().load_async({"name": "x"}))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # The spawned validator exited before the cancellation propagated
            assert service.exited == 1

        run(main())

    def test_cancellation_with_multiple_pending_validators(self):
        service = FakeAsyncService(delay=60)
        started_count = 0
        all_started = asyncio.Event()

        class SlowSchema(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                nonlocal started_count
                started_count += 1
                if started_count == 3:
                    all_started.set()
                await service.check(value)

        async def main():
            task = asyncio.create_task(
                SlowSchema().load_async([{"name": "a"}] * 3, many=True)
            )
            await all_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert service.exited == 3

        run(main())
