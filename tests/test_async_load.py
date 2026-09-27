"""Tests for Schema.load_async."""

import asyncio

import pytest

from marshmallow import (
    EXCLUDE,
    Schema,
    ValidationError,
    fields,
    post_load,
    pre_load,
    validates,
    validates_schema,
)
from marshmallow.exceptions import MarshmallowError


class FakeNameService:
    """A controllable fake asynchronous service.

    Simulates a remote "is this name taken" check. Tracks concurrency so
    tests can assert on the maximum number of in-flight calls, and records
    every call so tests can assert which calls happened.
    """

    def __init__(self, taken=(), delay=0.01):
        self.taken = set(taken)
        self.delay = delay
        self.calls = []
        self.active = 0
        self.peak = 0

    async def is_taken(self, name):
        self.calls.append(name)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            return name in self.taken
        finally:
            self.active -= 1


def run(coro):
    return asyncio.run(coro)


def make_schema(service, **schema_kwargs):
    class UserSchema(Schema):
        name = fields.Str(required=True)
        email = fields.Str(required=True)

        @validates("name")
        async def validate_name(self, value, **kwargs):
            if await service.is_taken(value):
                raise ValidationError("Name is already taken.")

        @validates("email")
        def validate_email(self, value, **kwargs):
            if "@" not in value:
                raise ValidationError("Invalid email.")

    return UserSchema(**schema_kwargs)


class TestBasicAsyncLoad:
    def test_async_field_validator_success(self):
        service = FakeNameService(taken={"taken"})
        schema = make_schema(service)
        result = run(schema.load_async({"name": "free", "email": "a@b.c"}))
        assert result == {"name": "free", "email": "a@b.c"}
        assert service.calls == ["free"]

    def test_async_field_validator_failure(self):
        service = FakeNameService(taken={"taken"})
        schema = make_schema(service)
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async({"name": "taken", "email": "a@b.c"}))
        assert excinfo.value.messages == {"name": ["Name is already taken."]}

    def test_sync_validators_still_work(self):
        service = FakeNameService()
        schema = make_schema(service)
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async({"name": "free", "email": "invalid"}))
        assert excinfo.value.messages == {"email": ["Invalid email."]}

    def test_sync_and_async_errors_merge(self):
        service = FakeNameService(taken={"taken"})
        schema = make_schema(service)
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async({"name": "taken", "email": "invalid"}))
        assert excinfo.value.messages == {
            "email": ["Invalid email."],
            "name": ["Name is already taken."],
        }

    def test_async_schema_validator(self):
        class S(Schema):
            password = fields.Str()
            confirm = fields.Str()

            @validates_schema
            async def check_match(self, data, **kwargs):
                await asyncio.sleep(0)
                if data.get("password") != data.get("confirm"):
                    raise ValidationError("Passwords do not match.")

        schema = S()
        assert run(schema.load_async({"password": "a", "confirm": "a"})) == {
            "password": "a",
            "confirm": "a",
        }
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async({"password": "a", "confirm": "b"}))
        assert excinfo.value.messages == {"_schema": ["Passwords do not match."]}

    def test_async_schema_validator_with_field_name(self):
        class S(Schema):
            name = fields.Str()

            @validates_schema
            async def check(self, data, **kwargs):
                await asyncio.sleep(0)
                raise ValidationError("Nope.", field_name="name")

        with pytest.raises(ValidationError) as excinfo:
            run(S().load_async({"name": "x"}))
        assert excinfo.value.messages == {"name": ["Nope."]}

    def test_awaitable_returned_by_sync_def_validator(self):
        service = FakeNameService()

        class S(Schema):
            name = fields.Str()

            @validates("name")
            def check_name(self, value, **kwargs):
                # A regular function returning an awaitable
                return service.is_taken(value)

        assert run(S().load_async({"name": "x"})) == {"name": "x"}
        assert service.calls == ["x"]

    def test_post_load_runs_after_async_validation(self):
        events = []

        class S(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await asyncio.sleep(0)
                events.append("validate")

            @post_load
            def make(self, data, **kwargs):
                events.append("post_load")
                return data

        run(S().load_async({"name": "x"}))
        assert events == ["validate", "post_load"]

    def test_post_load_not_run_on_async_validation_failure(self):
        events = []

        class S(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await asyncio.sleep(0)
                raise ValidationError("Nope.")

            @post_load
            def make(self, data, **kwargs):
                events.append("post_load")
                return data

        with pytest.raises(ValidationError):
            run(S().load_async({"name": "x"}))
        assert events == []

    def test_many(self):
        service = FakeNameService(taken={"b"})
        schema = make_schema(service, many=True)
        data = [
            {"name": "a", "email": "a@b.c"},
            {"name": "b", "email": "b@b.c"},
            {"name": "c", "email": "invalid"},
        ]
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async(data))
        assert excinfo.value.messages == {
            1: {"name": ["Name is already taken."]},
            2: {"email": ["Invalid email."]},
        }
        assert service.calls == ["a", "b", "c"]

    def test_deserialization_failure_skips_dependent_async_validator(self):
        service = FakeNameService()

        class S(Schema):
            age = fields.Int()

            @validates("age")
            async def check_age(self, value, **kwargs):
                await service.is_taken(str(value))

        with pytest.raises(ValidationError) as excinfo:
            run(S().load_async({"age": "notanint"}))
        assert excinfo.value.messages == {"age": ["Not a valid integer."]}
        assert service.calls == []

    def test_valid_data_on_error(self):
        service = FakeNameService(taken={"taken"})
        schema = make_schema(service)
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async({"name": "taken", "email": "a@b.c"}))
        assert excinfo.value.valid_data == {"email": "a@b.c"}


class TestSyncLoadRejectsAsyncValidators:
    def test_load_with_async_field_validator_raises_usage_error(self):
        service = FakeNameService()
        schema = make_schema(service)
        with pytest.raises(MarshmallowError, match="load_async"):
            schema.load({"name": "x", "email": "a@b.c"})
        # The coroutine was not run
        assert service.calls == []

    def test_load_with_async_schema_validator_raises_usage_error(self):
        class S(Schema):
            name = fields.Str()

            @validates_schema
            async def check(self, data, **kwargs):
                pass

        with pytest.raises(MarshmallowError, match="load_async"):
            S().load({"name": "x"})

    def test_validate_with_async_validator_raises_usage_error(self):
        service = FakeNameService()
        schema = make_schema(service)
        with pytest.raises(MarshmallowError, match="load_async"):
            schema.validate({"name": "x", "email": "a@b.c"})

    def test_load_does_not_start_an_event_loop(self):
        service = FakeNameService()
        schema = make_schema(service)
        with pytest.raises(MarshmallowError):
            schema.load({"name": "x", "email": "a@b.c"})
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()


class TestConcurrencyLimit:
    def test_max_concurrency_limits_parallel_validators(self):
        service = FakeNameService(delay=0.02)

        class S(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.is_taken(value)

        schema = S(many=True)
        data = [{"name": f"name{i}"} for i in range(6)]
        run(schema.load_async(data, max_concurrency=2))
        assert len(service.calls) == 6
        assert service.peak == 2

    def test_no_limit_runs_all_concurrently(self):
        service = FakeNameService(delay=0.02)

        class S(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.is_taken(value)

        schema = S(many=True)
        data = [{"name": f"name{i}"} for i in range(4)]
        run(schema.load_async(data))
        assert service.peak == 4

    def test_nested_and_many_share_concurrency_limit(self):
        service = FakeNameService(delay=0.02)

        class Inner(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.is_taken(value)

        class Outer(Schema):
            inner = fields.Nested(Inner)
            inners = fields.List(fields.Nested(Inner))
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.is_taken(value)

        schema = Outer()
        data = {
            "inner": {"name": "a"},
            "inners": [{"name": "b"}, {"name": "c"}],
            "name": "d",
        }
        run(schema.load_async(data, max_concurrency=2))
        assert sorted(service.calls) == ["a", "b", "c", "d"]
        assert service.peak == 2

    def test_invalid_max_concurrency(self):
        schema = make_schema(FakeNameService())
        with pytest.raises(ValueError, match="max_concurrency"):
            run(schema.load_async({"name": "x", "email": "a@b.c"}, max_concurrency=0))


class TestErrorOrdering:
    def test_error_order_is_deterministic_regardless_of_completion_speed(self):
        # The validator for "a" is slower, so it finishes last, but errors
        # must be collected in declaration order.
        class S(Schema):
            a = fields.Str()
            b = fields.Str()

            @validates("a")
            async def check_a(self, value, **kwargs):
                await asyncio.sleep(0.03)
                raise ValidationError("error a")

            @validates("b")
            async def check_b(self, value, **kwargs):
                await asyncio.sleep(0)
                raise ValidationError("error b")

        with pytest.raises(ValidationError) as excinfo:
            run(S().load_async({"a": "x", "b": "y"}))
        messages = excinfo.value.messages
        assert list(messages.keys()) == ["a", "b"]
        assert messages == {"a": ["error a"], "b": ["error b"]}

    def test_many_error_indices_in_input_order(self):
        class S(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                # Later items finish first
                await asyncio.sleep(0.03 if value == "first" else 0)
                raise ValidationError(f"error {value}")

        schema = S(many=True)
        data = [{"name": "first"}, {"name": "second"}, {"name": "third"}]
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async(data))
        messages = excinfo.value.messages
        assert list(messages.keys()) == [0, 1, 2]


class TestNested:
    def test_nested_async_validator_errors(self):
        service = FakeNameService(taken={"taken"})

        class Inner(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                if await service.is_taken(value):
                    raise ValidationError("Name is already taken.")

        class Outer(Schema):
            inner = fields.Nested(Inner, required=True)

        schema = Outer()
        result = run(schema.load_async({"inner": {"name": "free"}}))
        assert result == {"inner": {"name": "free"}}

        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async({"inner": {"name": "taken"}}))
        assert excinfo.value.messages == {"inner": {"name": ["Name is already taken."]}}

    def test_nested_many_async_validator_errors(self):
        service = FakeNameService(taken={"b"})

        class Inner(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                if await service.is_taken(value):
                    raise ValidationError("Name is already taken.")

        class Outer(Schema):
            inners = fields.List(fields.Nested(Inner))

        with pytest.raises(ValidationError) as excinfo:
            run(Outer().load_async({"inners": [{"name": "a"}, {"name": "b"}]}))
        assert excinfo.value.messages == {
            "inners": {1: {"name": ["Name is already taken."]}}
        }

    def test_nested_post_load_order(self):
        events = []

        class Inner(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await asyncio.sleep(0)

            @post_load
            def make(self, data, **kwargs):
                events.append("inner_post_load")
                return ("inner", data["name"])

        class Outer(Schema):
            inner = fields.Nested(Inner, required=True)

            @post_load
            def make(self, data, **kwargs):
                events.append("outer_post_load")
                return data

        result = run(Outer().load_async({"inner": {"name": "x"}}))
        assert events == ["inner_post_load", "outer_post_load"]
        assert result == {"inner": ("inner", "x")}

    def test_nested_sync_load_with_async_validator_raises_usage_error(self):
        service = FakeNameService()

        class Inner(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.is_taken(value)

        class Outer(Schema):
            inner = fields.Nested(Inner, required=True)

        with pytest.raises(MarshmallowError, match="load_async"):
            Outer().load({"inner": {"name": "x"}})
        assert service.calls == []

    def test_nested_deserialization_failure_skips_async_validators(self):
        service = FakeNameService()

        class Inner(Schema):
            age = fields.Int()

            @validates("age")
            async def check_age(self, value, **kwargs):
                await service.is_taken(str(value))

        class Outer(Schema):
            inner = fields.Nested(Inner, required=True)

        with pytest.raises(ValidationError) as excinfo:
            run(Outer().load_async({"inner": {"age": "notanint"}}))
        assert excinfo.value.messages == {"inner": {"age": ["Not a valid integer."]}}
        assert service.calls == []

    def test_unknown_exclude_still_works(self):
        service = FakeNameService()

        class S(Schema):
            name = fields.Str()

            class Meta:
                unknown = EXCLUDE

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.is_taken(value)

        result = run(S().load_async({"name": "x", "extra": 1}))
        assert result == {"name": "x"}


class TestCancellation:
    def test_cancellation_waits_for_spawned_tasks(self):
        started = []
        exited = []

        class S(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                started.append(value)
                try:
                    await asyncio.sleep(30)
                finally:
                    exited.append(value)

        async def main():
            task = asyncio.ensure_future(
                S().load_async({"name": "x"}, many=False)
            )
            # Let the load reach the async validation phase
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # The spawned validator task exited before cancellation
            # was reported back to the caller
            assert started == ["x"]
            assert exited == ["x"]

        run(main())

    def test_cancellation_with_many(self):
        started = []
        exited = []

        class S(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                started.append(value)
                try:
                    await asyncio.sleep(30)
                finally:
                    exited.append(value)

        async def main():
            schema = S(many=True)
            task = asyncio.ensure_future(
                schema.load_async([{"name": "a"}, {"name": "b"}], max_concurrency=2)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert sorted(started) == ["a", "b"]
            assert sorted(exited) == ["a", "b"]

        run(main())


class TestMiscInteractions:
    def test_nested_many_field(self):
        service = FakeNameService(taken={"b"})

        class Inner(Schema):
            name = fields.Str()

            @validates("name")
            async def check_name(self, value, **kwargs):
                if await service.is_taken(value):
                    raise ValidationError("Name is already taken.")

            @post_load
            def make(self, data, **kwargs):
                return ("made", data["name"])

        class Outer(Schema):
            inners = fields.Nested(Inner, many=True)

        result = run(Outer().load_async({"inners": [{"name": "a"}]}))
        assert result == {"inners": [("made", "a")]}

        with pytest.raises(ValidationError) as excinfo:
            run(Outer().load_async({"inners": [{"name": "a"}, {"name": "b"}]}))
        assert excinfo.value.messages == {
            "inners": {1: {"name": ["Name is already taken."]}}
        }

    def test_pass_collection_schema_validator(self):
        class S(Schema):
            n = fields.Int()

            @validates_schema(pass_collection=True)
            async def check_all(self, data, **kwargs):
                await asyncio.sleep(0)
                if len(data) > 2:
                    raise ValidationError("Too many items.")

        schema = S(many=True)
        assert run(schema.load_async([{"n": 1}, {"n": 2}])) == [{"n": 1}, {"n": 2}]
        with pytest.raises(ValidationError) as excinfo:
            run(schema.load_async([{"n": 1}, {"n": 2}, {"n": 3}]))
        assert excinfo.value.messages == {"_schema": ["Too many items."]}

    def test_pre_load_runs_before_async_validation(self):
        events = []

        class S(Schema):
            name = fields.Str()

            @pre_load
            def pre(self, data, **kwargs):
                events.append("pre_load")
                return data

            @validates("name")
            async def check_name(self, value, **kwargs):
                await asyncio.sleep(0)
                events.append("validate")

        run(S().load_async({"name": "x"}))
        assert events == ["pre_load", "validate"]

    def test_partial(self):
        service = FakeNameService()

        class S(Schema):
            name = fields.Str(required=True)
            other = fields.Str(required=True)

            @validates("name")
            async def check_name(self, value, **kwargs):
                await service.is_taken(value)

        result = run(S().load_async({"name": "x"}, partial=True))
        assert result == {"name": "x"}
        assert service.calls == ["x"]
