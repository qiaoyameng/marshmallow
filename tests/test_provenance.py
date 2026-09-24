"""Tests for Schema.load_with_sources and field provenance tracking."""

import pytest

from marshmallow import (
    EXCLUDE,
    FieldSource,
    Schema,
    ValidationError,
    fields,
    post_load,
    pre_load,
)
from marshmallow.provenance import LoadResult


class BasicSchema(Schema):
    name = fields.Str(required=True)
    age = fields.Int(load_default=0)
    nickname = fields.Str()  # no default, not required


class TestBasicSources:
    def test_returns_load_result_with_data(self):
        result = BasicSchema().load_with_sources({"name": "Ada", "age": 36})
        assert isinstance(result, LoadResult)
        assert result.data == {"name": "Ada", "age": 36}

    def test_supplied_defaulted_omitted(self):
        result = BasicSchema().load_with_sources({"name": "Ada"})
        assert result.source_of("name") == FieldSource.SUPPLIED
        assert result.source_of("age") == FieldSource.DEFAULTED
        assert result.source_of("nickname") == FieldSource.OMITTED

    def test_sources_property_mirrors_data_shape(self):
        result = BasicSchema().load_with_sources({"name": "Ada"})
        assert result.sources == {
            "name": FieldSource.SUPPLIED,
            "age": FieldSource.DEFAULTED,
            "nickname": FieldSource.OMITTED,
        }

    def test_field_sources_compare_equal_to_strings(self):
        result = BasicSchema().load_with_sources({"name": "Ada"})
        assert result.source_of("name") == "supplied"
        assert result.source_of("age") == "defaulted"
        assert result.source_of("nickname") == "omitted"

    def test_unknown_path_raises_key_error(self):
        result = BasicSchema().load_with_sources({"name": "Ada"})
        with pytest.raises(KeyError):
            result.source_of("nonexistent")

    def test_load_return_type_unchanged(self):
        data = BasicSchema().load({"name": "Ada"})
        assert type(data) is dict
        assert data == {"name": "Ada", "age": 0}

    def test_default_value_none(self):
        class NoneDefaultSchema(Schema):
            value = fields.Str(load_default=None, allow_none=True)

        result = NoneDefaultSchema().load_with_sources({})
        assert result.data == {"value": None}
        assert result.source_of("value") == FieldSource.DEFAULTED

        supplied = NoneDefaultSchema().load_with_sources({"value": None})
        assert supplied.data == {"value": None}
        assert supplied.source_of("value") == FieldSource.SUPPLIED

    def test_default_factory_called_once_per_load(self):
        calls = []

        def factory():
            calls.append(1)
            return len(calls)

        class FactorySchema(Schema):
            counter = fields.Int(load_default=factory)

        result = FactorySchema().load_with_sources({})
        assert result.data == {"counter": 1}
        assert result.source_of("counter") == FieldSource.DEFAULTED
        assert calls == [1]

    def test_pre_load_output_determines_source(self):
        class PreLoadSchema(Schema):
            name = fields.Str()
            tag = fields.Str(load_default="untagged")

            @pre_load
            def transform(self, data, **kwargs):
                data = dict(data)
                # "name" appears only after pre_load; "tag" is removed by pre_load
                data["name"] = data.pop("raw_name")
                data.pop("tag", None)
                return data

        result = PreLoadSchema().load_with_sources(
            {"raw_name": "Ada", "tag": "important"}
        )
        assert result.data == {"name": "Ada", "tag": "untagged"}
        assert result.source_of("name") == FieldSource.SUPPLIED
        assert result.source_of("tag") == FieldSource.DEFAULTED

    def test_unknown_include_fields_are_supplied(self):
        class IncludeSchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = "include"

        result = IncludeSchema().load_with_sources({"name": "Ada", "extra": 1})
        assert result.source_of("extra") == FieldSource.SUPPLIED


class TestDataKeyAndAttribute:
    def test_data_key_mismatch(self):
        class DataKeySchema(Schema):
            name = fields.Str(data_key="externalName")
            age = fields.Int(load_default=0, data_key="externalAge")

        result = DataKeySchema().load_with_sources({"externalName": "Ada"})
        assert result.data == {"name": "Ada", "age": 0}
        # Queryable by output attribute...
        assert result.source_of("name") == FieldSource.SUPPLIED
        assert result.source_of("age") == FieldSource.DEFAULTED
        # ...and by external data_key
        assert result.source_of("externalName") == FieldSource.SUPPLIED
        assert result.source_of("externalAge") == FieldSource.DEFAULTED

    def test_attribute_mismatch(self):
        class AttributeSchema(Schema):
            name = fields.Str(attribute="real_name", data_key="externalName")

        result = AttributeSchema().load_with_sources({"externalName": "Ada"})
        assert result.data == {"real_name": "Ada"}
        assert result.source_of("real_name") == FieldSource.SUPPLIED
        assert result.source_of("externalName") == FieldSource.SUPPLIED


class ChildSchema(Schema):
    name = fields.Str(required=True)
    score = fields.Int(load_default=10)


class ParentSchema(Schema):
    title = fields.Str()
    child = fields.Nested(ChildSchema)


class TestNestedSources:
    def test_nested_field_sources(self):
        result = ParentSchema().load_with_sources(
            {"title": "t", "child": {"name": "kid"}}
        )
        assert result.source_of("title") == FieldSource.SUPPLIED
        assert result.source_of("child") == FieldSource.SUPPLIED
        assert result.source_of("child.name") == FieldSource.SUPPLIED
        assert result.source_of("child.score") == FieldSource.DEFAULTED

    def test_nested_omitted(self):
        result = ParentSchema().load_with_sources({"title": "t"})
        assert result.source_of("child") == FieldSource.OMITTED
        assert result.sources["child"] == FieldSource.OMITTED

    def test_nested_data_key(self):
        class NestedKeySchema(Schema):
            child = fields.Nested(ChildSchema, data_key="childData")

        result = NestedKeySchema().load_with_sources({"childData": {"name": "kid"}})
        assert result.source_of("child") == FieldSource.SUPPLIED
        assert result.source_of("childData") == FieldSource.SUPPLIED
        assert result.source_of("child.name") == FieldSource.SUPPLIED
        assert result.source_of("childData.score") == FieldSource.DEFAULTED

    def test_nested_partial_missing_field_is_omitted_not_defaulted(self):
        # "score" has a load_default, but partial skips it entirely
        result = ParentSchema().load_with_sources(
            {"child": {"name": "kid"}}, partial=("child.score",)
        )
        assert result.data == {"child": {"name": "kid"}}
        assert result.source_of("child.score") == FieldSource.OMITTED

    def test_nested_partial_true(self):
        result = ParentSchema().load_with_sources({"child": {}}, partial=True)
        assert result.source_of("child.name") == FieldSource.OMITTED
        assert result.source_of("child.score") == FieldSource.OMITTED

    def test_top_level_partial_missing_field_is_omitted_not_defaulted(self):
        result = BasicSchema().load_with_sources({"name": "Ada"}, partial=("age",))
        assert result.data == {"name": "Ada"}
        assert result.source_of("age") == FieldSource.OMITTED


class TestCollectionSources:
    def test_list_of_nested(self):
        class ListSchema(Schema):
            children = fields.List(fields.Nested(ChildSchema))

        result = ListSchema().load_with_sources(
            {"children": [{"name": "a"}, {"name": "b", "score": 3}]}
        )
        assert result.source_of("children") == FieldSource.SUPPLIED
        assert result.source_of("children.0.name") == FieldSource.SUPPLIED
        assert result.source_of("children.0.score") == FieldSource.DEFAULTED
        assert result.source_of("children.1.score") == FieldSource.SUPPLIED
        assert result.source_of(("children", 1, "name")) == FieldSource.SUPPLIED

    def test_list_of_nested_single_item_keeps_list_structure(self):
        class ListSchema(Schema):
            children = fields.List(fields.Nested(ChildSchema))

        result = ListSchema().load_with_sources({"children": [{"name": "a"}]})
        assert result.source_of("children.0.name") == FieldSource.SUPPLIED
        assert result.source_of("children.0.score") == FieldSource.DEFAULTED
        assert result.sources["children"] == [
            {"name": FieldSource.SUPPLIED, "score": FieldSource.DEFAULTED}
        ]

    def test_nested_many(self):
        class ManySchema(Schema):
            children = fields.Nested(ChildSchema, many=True)

        result = ManySchema().load_with_sources(
            {"children": [{"name": "a"}, {}]}, partial=("children.name",)
        )
        assert result.source_of("children.0.name") == FieldSource.SUPPLIED
        assert result.source_of("children.1.name") == FieldSource.OMITTED
        assert result.source_of("children.1.score") == FieldSource.DEFAULTED

    def test_many_true(self):
        result = BasicSchema().load_with_sources(
            [{"name": "a"}, {"name": "b", "age": 1, "nickname": "bee"}], many=True
        )
        assert result.data == [
            {"name": "a", "age": 0},
            {"name": "b", "age": 1, "nickname": "bee"},
        ]
        assert result.source_of("0.name") == FieldSource.SUPPLIED
        assert result.source_of("0.age") == FieldSource.DEFAULTED
        assert result.source_of("0.nickname") == FieldSource.OMITTED
        assert result.source_of((1, "nickname")) == FieldSource.SUPPLIED
        assert result.sources[0]["age"] == FieldSource.DEFAULTED

    def test_many_true_with_partial(self):
        result = BasicSchema().load_with_sources(
            [{"name": "a"}], many=True, partial=True
        )
        assert result.source_of("0.age") == FieldSource.OMITTED


class TestPostLoad:
    def test_post_load_custom_object_keeps_sources(self):
        class User:
            def __init__(self, name, age):
                self.name = name
                self.age = age

        class UserSchema(Schema):
            name = fields.Str(required=True)
            age = fields.Int(load_default=0)

            @post_load
            def make_user(self, data, **kwargs):
                return User(**data)

        result = UserSchema().load_with_sources({"name": "Ada"})
        assert isinstance(result.data, User)
        assert result.data.name == "Ada"
        assert result.data.age == 0
        assert result.source_of("name") == FieldSource.SUPPLIED
        assert result.source_of("age") == FieldSource.DEFAULTED

    def test_post_load_many_custom_objects(self):
        class Point:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        class PointSchema(Schema):
            x = fields.Int(required=True)
            y = fields.Int(load_default=0)

            @post_load
            def make_point(self, data, **kwargs):
                return Point(**data)

        result = PointSchema().load_with_sources([{"x": 1}, {"x": 2, "y": 3}], many=True)
        assert [p.y for p in result.data] == [0, 3]
        assert result.source_of("0.y") == FieldSource.DEFAULTED
        assert result.source_of("1.y") == FieldSource.SUPPLIED


class TestFailureSources:
    def test_error_preserves_valid_sources_and_valid_data(self):
        class StrictSchema(Schema):
            name = fields.Str(required=True)
            age = fields.Int(load_default=0)

        with pytest.raises(ValidationError) as excinfo:
            StrictSchema().load_with_sources({"name": "Ada", "age": "not-an-int"})
        exc = excinfo.value
        # valid_data is preserved as before
        assert exc.valid_data == {"name": "Ada"}
        # sources of the valid portion are queryable on the error
        assert exc.sources.source_of("name") == FieldSource.SUPPLIED
        assert exc.sources.source_of("age") == FieldSource.SUPPLIED
        assert exc.sources.data == {"name": "Ada"}

    def test_error_in_nested_list_preserves_sources(self):
        class ListSchema(Schema):
            children = fields.List(fields.Nested(ChildSchema))

        with pytest.raises(ValidationError) as excinfo:
            ListSchema().load_with_sources(
                {"children": [{"name": "a"}, {"score": "bad"}]}
            )
        exc = excinfo.value
        assert exc.sources.source_of("children.0.name") == FieldSource.SUPPLIED
        assert exc.sources.source_of("children.0.score") == FieldSource.DEFAULTED
        assert exc.sources.source_of("children.1.name") == FieldSource.OMITTED

    def test_untracked_load_error_has_no_sources(self):
        with pytest.raises(ValidationError) as excinfo:
            BasicSchema().load({})
        assert not hasattr(excinfo.value, "sources")

    def test_schema_level_validation_error_keeps_sources(self):
        class CrossSchema(Schema):
            a = fields.Int()
            b = fields.Int(load_default=5)

            @post_load
            def check(self, data, **kwargs):
                if data.get("a") == data.get("b"):
                    raise ValidationError("a must differ from b")
                return data

        with pytest.raises(ValidationError) as excinfo:
            CrossSchema().load_with_sources({"a": 5})
        exc = excinfo.value
        assert exc.sources.source_of("a") == FieldSource.SUPPLIED
        assert exc.sources.source_of("b") == FieldSource.DEFAULTED


class TestUnknownExclude:
    def test_unknown_exclude_not_tracked(self):
        class ExcludeSchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = EXCLUDE

        result = ExcludeSchema().load_with_sources({"name": "Ada", "extra": 1})
        assert result.data == {"name": "Ada"}
        with pytest.raises(KeyError):
            result.source_of("extra")
