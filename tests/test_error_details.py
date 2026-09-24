"""Regression tests for the machine-readable error details on
:class:`marshmallow.ValidationError`.
"""

import pytest

from marshmallow import (
    DEFAULT_ERROR_CODE,
    ErrorDetail,
    Schema,
    ValidationError,
    fields,
    validate,
    validates,
    validates_schema,
)


def detail_by_path(err, path):
    matches = [detail for detail in err.details if detail.path == tuple(path)]
    assert matches, f"no error detail at path {path!r}"
    return matches


class TestValidationErrorDetails:
    def test_string_error_gets_default_code(self):
        err = ValidationError("bad value")
        assert err.messages == ["bad value"]
        assert err.details == [
            ErrorDetail(
                code=DEFAULT_ERROR_CODE,
                message="bad value",
                params={},
                path=(),
            )
        ]

    def test_explicit_code_and_params(self):
        err = ValidationError(
            "Must be at least {min}.", code="range", params={"input": 1, "min": 5}
        )
        (detail,) = err.details
        assert detail.code == "range"
        assert detail.params == {"input": 1, "min": 5}
        assert detail.message == "Must be at least {min}."

    def test_dict_error_gets_default_code_and_paths(self):
        err = ValidationError({"user": {"email": ["email is invalid"]}})
        (detail,) = err.details
        assert detail.code == DEFAULT_ERROR_CODE
        assert detail.path == ("user", "email")

    def test_multiple_messages_do_not_overwrite(self):
        err = ValidationError(["first", "second"])
        assert [detail.message for detail in err.details] == ["first", "second"]
        err2 = ValidationError({"field": ["one", "two"]})
        assert [detail.path for detail in err2.details] == [("field",), ("field",)]

    def test_messages_and_str_unchanged(self):
        err = ValidationError("oops")
        assert str(err) == "oops"
        assert err.messages == ["oops"]

    def test_field_name_is_added_to_path(self):
        err = ValidationError("oops", field_name="email")
        assert err.details[0].path == ("email",)

    def test_schema_level_error_has_empty_path(self):
        err = ValidationError("schema oops", field_name="_schema")
        assert err.details[0].path == ()


class TestBuiltinFieldCodes:
    def test_required_code(self):
        class S(Schema):
            name = fields.Str(required=True)

        with pytest.raises(ValidationError) as exc:
            S().load({})
        (detail,) = exc.value.details
        assert detail.code == "required"
        assert detail.path == ("name",)
        assert detail.params == {}
        assert exc.value.messages == {"name": ["Missing data for required field."]}

    def test_null_code(self):
        class S(Schema):
            name = fields.Str()

        with pytest.raises(ValidationError) as exc:
            S().load({"name": None})
        assert exc.value.details[0].code == "null"

    def test_invalid_type_code_keeps_input(self):
        class S(Schema):
            count = fields.Int()

        with pytest.raises(ValidationError) as exc:
            S().load({"count": "abc"})
        (detail,) = exc.value.details
        assert detail.code == "invalid"
        assert detail.params == {"input": "abc"}
        assert detail.path == ("count",)

    def test_custom_error_message_keeps_stable_code(self):
        class S(Schema):
            count = fields.Int(error_messages={"invalid": "numbers only"})

        with pytest.raises(ValidationError) as exc:
            S().load({"count": "abc"})
        (detail,) = exc.value.details
        assert detail.code == "invalid"
        assert detail.message == "numbers only"
        assert detail.params == {"input": "abc"}

    def test_unknown_field_code(self):
        class S(Schema):
            name = fields.Str()

        with pytest.raises(ValidationError) as exc:
            S().load({"name": "a", "extra": 1})
        (detail,) = detail_by_path(exc.value, ["extra"])
        assert detail.code == "unknown"
        assert detail.params == {"input": "extra"}
        assert exc.value.messages["extra"] == ["Unknown field."]

    def test_top_level_type_code(self):
        class S(Schema):
            name = fields.Str()

        with pytest.raises(ValidationError) as exc:
            S(many=True).load({"not": "a list"})
        (detail,) = exc.value.details
        assert detail.code == "type"
        assert detail.path == ()


class TestValidatorCodes:
    def test_range_code_and_params(self):
        class S(Schema):
            n = fields.Int(validate=validate.Range(min=1, max=10))

        with pytest.raises(ValidationError) as exc:
            S().load({"n": 0})
        detail = exc.value.details[0]
        assert detail.code == "range"
        assert detail.params["input"] == 0
        assert detail.params["min"] == 1
        assert detail.params["max"] == 10

    @pytest.mark.parametrize(
        ("validator", "value", "code"),
        [
            (validate.Length(min=3), "ab", "length"),
            (validate.Equal(5), 4, "equal"),
            (validate.Regexp(r"^\d+$"), "abc", "regexp"),
            (validate.OneOf([1, 2]), 3, "one_of"),
            (validate.NoneOf([1, 2]), 1, "none_of"),
            (validate.ContainsOnly([1, 2]), [1, 3], "contains_only"),
            (validate.ContainsNoneOf([1, 2]), [1], "contains_none_of"),
            (validate.Email(), "bad", "invalid_email"),
            (validate.URL(), "bad", "invalid_url"),
            (validate.Predicate("isdigit"), "abc", "predicate"),
        ],
    )
    def test_builtin_validator_codes(self, validator, value, code):
        class S(Schema):
            v = fields.Field()  # value passed through unchanged

            @validates("v")
            def check(self, item, **kwargs):
                validator(item)

        with pytest.raises(ValidationError) as exc:
            S().load({"v": value})
        assert exc.value.details[0].code == code
        assert exc.value.details[0].params["input"] == value

    def test_and_collects_every_error(self):
        class S(Schema):
            n = fields.Int(
                validate=validate.And(validate.Range(min=10), validate.Range(max=0))
            )

        with pytest.raises(ValidationError) as exc:
            S().load({"n": 5})
        assert exc.value.messages == {
            "n": [
                "Must be greater than or equal to 10.",
                "Must be less than or equal to 0.",
            ]
        }
        details = exc.value.details
        assert [detail.code for detail in details] == ["range", "range"]
        assert [detail.params["min"] for detail in details] == [10, None]
        assert [detail.params["max"] for detail in details] == [None, 0]
        assert {detail.path for detail in details} == {("n",)}

    def test_string_validator_gets_default_code(self):
        def reject(value):
            raise ValidationError("custom failure")

        class S(Schema):
            v = fields.Str(validate=reject)

        with pytest.raises(ValidationError) as exc:
            S().load({"v": "x"})
        detail = exc.value.details[0]
        assert detail.code == DEFAULT_ERROR_CODE
        assert detail.message == "custom failure"
        assert detail.path == ("v",)

    def test_dict_validator_gets_default_code(self):
        def reject(value):
            raise ValidationError({"sub": ["nested failure"]})

        class S(Schema):
            v = fields.Str(validate=reject)

        with pytest.raises(ValidationError) as exc:
            S().load({"v": "x"})
        (detail,) = exc.value.details
        assert detail.code == DEFAULT_ERROR_CODE
        assert detail.path == ("v", "sub")


class TestSchemaLevelValidation:
    def test_schema_validator_empty_path(self):
        class S(Schema):
            a = fields.Int()
            b = fields.Int()

            @validates_schema
            def check(self, data, **kwargs):
                raise ValidationError("a must equal b")

        with pytest.raises(ValidationError) as exc:
            S().load({"a": 1, "b": 2})
        (detail,) = exc.value.details
        assert detail.code == DEFAULT_ERROR_CODE
        assert detail.path == ()
        assert exc.value.messages == {"_schema": ["a must equal b"]}

    def test_field_validator_uses_data_key(self):
        class S(Schema):
            display_name = fields.Str(data_key="displayName")

            @validates("display_name")
            def check(self, value, **kwargs):
                raise ValidationError("taken")

        with pytest.raises(ValidationError) as exc:
            S().load({"displayName": "a"})
        assert exc.value.details[0].path == ("displayName",)
        assert exc.value.messages == {"displayName": ["taken"]}

    def test_schema_validator_on_many_keeps_index(self):
        class S(Schema):
            n = fields.Int()

            @validates_schema
            def check(self, data, **kwargs):
                if data["n"] < 0:
                    raise ValidationError("negative")

        with pytest.raises(ValidationError) as exc:
            S().load([{"n": 1}, {"n": -1}], many=True)
        detail = exc.value.details[0]
        assert detail.path == (1,)
        assert exc.value.messages == {1: {"_schema": ["negative"]}}


class TestNestedAndCollections:
    def test_list_indices_are_preserved(self):
        class S(Schema):
            nums = fields.List(fields.Int())

        with pytest.raises(ValidationError) as exc:
            S().load({"nums": ["x", 1, "y"]})
        messages = exc.value.messages
        assert messages == {"nums": {0: ["Not a valid integer."], 2: ["Not a valid integer."]}}
        assert sorted(detail.path for detail in exc.value.details) == [
            ("nums", 0),
            ("nums", 2),
        ]

    def test_tuple_indices_are_preserved(self):
        class S(Schema):
            pair = fields.Tuple((fields.Int(), fields.Int()))

        with pytest.raises(ValidationError) as exc:
            S().load({"pair": ("x", "y")})
        assert sorted(detail.path for detail in exc.value.details) == [
            ("pair", 0),
            ("pair", 1),
        ]

    def test_nested_many_with_alias(self):
        class Item(Schema):
            quantity = fields.Int(data_key="quantityAlias", required=True)

        class Order(Schema):
            items = fields.List(fields.Nested(Item))

        with pytest.raises(ValidationError) as exc:
            Order().load({"items": [{}, {"quantityAlias": "x"}]})
        messages = exc.value.messages
        assert messages == {
            "items": {
                0: {
                    "quantityAlias": ["Missing data for required field."]
                },
                1: {"quantityAlias": ["Not a valid integer."]},
            }
        }
        paths = sorted(detail.path for detail in exc.value.details)
        assert paths == [
            ("items", 0, "quantityAlias"),
            ("items", 1, "quantityAlias"),
        ]
        codes = {detail.path: detail.code for detail in exc.value.details}
        assert codes[("items", 0, "quantityAlias")] == "required"
        assert codes[("items", 1, "quantityAlias")] == "invalid"

    def test_dict_mapping_key_and_value_paths(self):
        class S(Schema):
            mapping = fields.Dict(keys=fields.Int(), values=fields.Int())

        with pytest.raises(ValidationError) as exc:
            S().load({"mapping": {"bad": "also bad"}})
        paths = sorted(detail.path for detail in exc.value.details)
        assert paths == [("mapping", "bad", "key"), ("mapping", "bad", "value")]


class TestIntegrationShowcase:
    """A request with nested objects, aliases and multiple list element errors."""

    def test_showcase(self):
        class Item(Schema):
            quantity = fields.Int(
                data_key="quantityAlias",
                required=True,
                validate=validate.Range(min=1),
            )

        class Order(Schema):
            email_address = fields.Email(data_key="emailAddress", required=True)
            items = fields.List(fields.Nested(Item), required=True)

            @validates_schema
            def no_empty_order(self, data, **kwargs):
                if not data.get("items"):
                    raise ValidationError("Order must contain at least one item.")

        payload = {
            "emailAddress": "not-an-email",
            "items": [
                {"quantityAlias": 0},
                {"quantityAlias": "oops"},
                {"quantityAlias": 4},
            ],
            "unexpected": True,
        }

        with pytest.raises(ValidationError) as exc:
            Order().load(payload)
        err = exc.value

        # Legacy surface stays byte-for-byte compatible
        assert err.messages == {
            "emailAddress": ["Not a valid email address."],
            "items": {
                0: {"quantityAlias": ["Must be greater than or equal to 1."]},
                1: {"quantityAlias": ["Not a valid integer."]},
            },
            "unexpected": ["Unknown field."],
        }
        assert str(err).startswith("{")

        details_by_path = {detail.path: detail for detail in err.details}
        email = details_by_path[("emailAddress",)]
        assert email.code == "invalid_email"
        assert email.params == {"input": "not-an-email"}

        too_small = details_by_path[("items", 0, "quantityAlias")]
        assert too_small.code == "range"
        assert too_small.params == {
            "input": 0,
            "min": 1,
            "max": None,
            "min_inclusive": True,
            "max_inclusive": True,
        }

        bad_type = details_by_path[("items", 1, "quantityAlias")]
        assert bad_type.code == "invalid"
        assert bad_type.params == {"input": "oops"}

        unknown = details_by_path[("unexpected",)]
        assert unknown.code == "unknown"

        # Valid element must not produce a detail entry
        assert ("items", 2, "quantityAlias") not in details_by_path
        assert len(err.details) == 4
