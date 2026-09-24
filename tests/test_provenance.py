import pytest

from marshmallow import (
    Schema,
    ValidationError,
    fields,
    post_load,
    pre_load,
)
from marshmallow.provenance import DEFAULTED, OMITTED, SUPPLIED, LoadResult


class ProfileSchema(Schema):
    bio = fields.Str(load_default=None)
    age = fields.Int(load_default=0)
    nickname = fields.Str()


class UserSchema(Schema):
    name = fields.Str(required=True)
    email = fields.Str(load_default=None)
    display_name = fields.Str(data_key="displayName", attribute="display")
    profile = fields.Nested(ProfileSchema)
    friends = fields.List(fields.Nested(ProfileSchema))


class TestBasicSources:
    def test_supplied_defaulted_omitted(self):
        result = UserSchema().load_with_provenance({"name": "Ada"})
        assert isinstance(result, LoadResult)
        assert result.data == {"name": "Ada", "email": None}
        assert result.source("name") == SUPPLIED
        assert result.source("email") == DEFAULTED
        assert result.source("display") == OMITTED

    def test_explicit_null_is_supplied(self):
        result = UserSchema().load_with_provenance({"name": "Ada", "email": None})
        assert result.data["email"] is None
        assert result.source("email") == SUPPLIED

    def test_none_load_default_is_defaulted(self):
        # A missing field with load_default=None is defaulted, not supplied
        result = UserSchema().load_with_provenance({"name": "Ada"})
        assert result.source("email") == DEFAULTED

    def test_unknown_path_returns_default(self):
        result = UserSchema().load_with_provenance({"name": "Ada"})
        assert result.source("nonexistent") is None
        assert result.source("nonexistent", default="?") == "?"

    def test_sources_mapping_uses_dotted_paths(self):
        result = UserSchema().load_with_provenance(
            {"name": "Ada", "profile": {"bio": "hi"}}
        )
        assert result.sources["profile.bio"] == SUPPLIED
        assert result.sources["profile.age"] == DEFAULTED

    def test_source_determined_after_pre_load(self):
        class PreSchema(Schema):
            a = fields.Str()
            b = fields.Str(load_default="x")

            @pre_load
            def munge(self, data, **kwargs):
                data = dict(data)
                data["b"] = data.pop("a")
                return data

        result = PreSchema().load_with_provenance({"a": "1"})
        # "b" was present after pre_load ran, so it counts as supplied
        assert result.source("b") == SUPPLIED
        assert result.source("a") == OMITTED

    def test_load_return_type_unchanged(self):
        data = UserSchema().load({"name": "Ada"})
        assert not isinstance(data, LoadResult)
        assert data == {"name": "Ada", "email": None}


class TestDataKeyAndAttribute:
    def test_data_key_mismatch_queries_by_attribute(self):
        result = UserSchema().load_with_provenance(
            {"name": "Ada", "displayName": "Ada Lovelace"}
        )
        assert result.data["display"] == "Ada Lovelace"
        # Queried by the output attribute name, not the external data_key
        assert result.source("display") == SUPPLIED
        assert result.source("displayName") is None

    def test_data_key_field_defaulted(self):
        result = UserSchema().load_with_provenance({"name": "Ada"})
        assert result.source("display") == OMITTED


class TestNestedAndCollections:
    def test_nested_sources(self):
        result = UserSchema().load_with_provenance(
            {"name": "Ada", "profile": {"bio": "hi", "age": 36}}
        )
        assert result.source("profile") == SUPPLIED
        assert result.source("profile.bio") == SUPPLIED
        assert result.source("profile.age") == SUPPLIED
        assert result.source("profile.nickname") == OMITTED

    def test_nested_omitted(self):
        result = UserSchema().load_with_provenance({"name": "Ada"})
        assert result.source("profile") == OMITTED
        assert result.source("profile.bio") is None

    def test_list_of_nested_sources(self):
        result = UserSchema().load_with_provenance(
            {"name": "Ada", "friends": [{"bio": "x"}, {"age": 1}]}
        )
        assert result.source("friends") == SUPPLIED
        assert result.source("friends", 0, "bio") == SUPPLIED
        assert result.source("friends", 0, "age") == DEFAULTED
        assert result.source("friends.1.bio") == DEFAULTED
        assert result.source("friends.1.age") == SUPPLIED
        assert result.source("friends.1.nickname") == OMITTED

    def test_many_true_sources(self):
        result = UserSchema().load_with_provenance(
            [{"name": "Ada"}, {"name": "Bob", "email": "b@example.com"}], many=True
        )
        assert result.source(0, "name") == SUPPLIED
        assert result.source("0.email") == DEFAULTED
        assert result.source("1.email") == SUPPLIED
        assert result.source("1.profile") == OMITTED

    def test_nested_many_sources(self):
        class Group(Schema):
            members = fields.Nested(ProfileSchema, many=True)

        result = Group().load_with_provenance({"members": [{"bio": "x"}, {}]})
        assert result.source("members.0.bio") == SUPPLIED
        assert result.source("members.1.bio") == DEFAULTED


class TestPartial:
    def test_partial_skip_is_omitted_not_defaulted(self):
        # email has load_default=None, but partial skips it entirely
        result = UserSchema().load_with_provenance({"name": "Ada"}, partial=("email",))
        assert "email" not in result.data
        assert result.source("email") == OMITTED

    def test_nested_partial(self):
        result = UserSchema().load_with_provenance(
            {"name": "Ada", "profile": {"bio": "hi"}},
            partial=("profile.age", "profile.bio"),
        )
        assert result.data["profile"] == {"bio": "hi"}
        assert result.source("profile.bio") == SUPPLIED
        # profile.age is missing and partial-skipped despite its load_default
        assert result.source("profile.age") == OMITTED
        assert result.source("profile.nickname") == OMITTED

    def test_nested_partial_skips_field_with_default(self):
        result = UserSchema().load_with_provenance(
            {"name": "Ada", "profile": {}}, partial=("profile.bio",)
        )
        # profile.bio has load_default=None but is partial-skipped, so it is
        # omitted, not defaulted
        assert result.data["profile"] == {"age": 0}
        assert result.source("profile.bio") == OMITTED
        assert result.source("profile.age") == DEFAULTED

    def test_partial_true_marks_all_missing_omitted(self):
        result = UserSchema().load_with_provenance({"name": "Ada"}, partial=True)
        assert result.source("email") == OMITTED
        assert result.source("profile") == OMITTED


class TestDefaults:
    def test_default_factory_called_once(self):
        calls = []

        class FactorySchema(Schema):
            items = fields.List(fields.Int(), load_default=lambda: calls.append(1) or [])

        result = FactorySchema().load_with_provenance({})
        assert result.data == {"items": []}
        assert result.source("items") == DEFAULTED
        assert calls == [1]

    def test_defaulted_value_in_data(self):
        result = UserSchema().load_with_provenance(
            {"name": "Ada", "profile": {}}
        )
        assert result.data["profile"] == {"bio": None, "age": 0}
        assert result.source("profile.bio") == DEFAULTED
        assert result.source("profile.age") == DEFAULTED


class TestPostLoad:
    def test_post_load_object_keeps_sources(self):
        class Point:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        class PointSchema(Schema):
            x = fields.Int()
            y = fields.Int(load_default=0)

            @post_load
            def make_point(self, data, **kwargs):
                return Point(**data)

        result = PointSchema().load_with_provenance({"x": 1})
        assert isinstance(result.data, Point)
        assert (result.data.x, result.data.y) == (1, 0)
        assert result.source("x") == SUPPLIED
        assert result.source("y") == DEFAULTED

    def test_nested_post_load_object(self):
        class Profile:
            def __init__(self, bio, age):
                self.bio = bio
                self.age = age

        class ObjProfileSchema(ProfileSchema):
            @post_load
            def make_profile(self, data, **kwargs):
                return Profile(**data)

        class ObjUserSchema(Schema):
            profile = fields.Nested(ObjProfileSchema)

        result = ObjUserSchema().load_with_provenance({"profile": {"bio": "hi"}})
        assert isinstance(result.data["profile"], Profile)
        assert result.source("profile.bio") == SUPPLIED
        assert result.source("profile.age") == DEFAULTED


class TestValidationFailure:
    def test_failed_load_raises_and_keeps_sources(self):
        with pytest.raises(ValidationError) as excinfo:
            UserSchema().load_with_provenance(
                {"name": "Ada", "profile": {"age": "not-an-int"}}
            )
        exc = excinfo.value
        assert exc.messages == {"profile": {"age": ["Not a valid integer."]}}
        # valid_data is preserved as with plain load()
        assert exc.valid_data == {
            "name": "Ada",
            "email": None,
            "profile": {"bio": None},
        }
        # Sources of the valid parts survive on the exception
        assert exc.load_result is not None
        assert exc.load_result.data is exc.valid_data
        assert exc.load_result.source("name") == SUPPLIED
        assert exc.load_result.source("email") == DEFAULTED
        assert exc.load_result.source("profile.bio") == DEFAULTED

    def test_failed_load_does_not_look_like_success(self):
        schema = UserSchema()
        with pytest.raises(ValidationError):
            schema.load_with_provenance({})
        # A successful load of the same input shape returns a LoadResult
        result = schema.load_with_provenance({"name": "Ada"})
        assert isinstance(result, LoadResult)

    def test_many_partial_failure_keeps_valid_item_sources(self):
        schema = UserSchema()
        with pytest.raises(ValidationError) as excinfo:
            schema.load_with_provenance(
                [{"name": "Ada"}, {"name": "Bob", "profile": {"age": "bad"}}],
                many=True,
            )
        exc = excinfo.value
        assert exc.load_result is not None
        assert exc.load_result.source("0.name") == SUPPLIED
        assert exc.load_result.source("1.name") == SUPPLIED
        assert exc.load_result.source("1.profile.age") == SUPPLIED
