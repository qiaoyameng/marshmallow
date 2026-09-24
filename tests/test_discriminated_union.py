"""Tests for fields.DiscriminatedUnion."""

import dataclasses

import pytest

from marshmallow import (
    EXCLUDE,
    INCLUDE,
    Schema,
    ValidationError,
    fields,
    post_dump,
    post_load,
    pre_load,
    validates,
)


@dataclasses.dataclass
class EmailMessage:
    address: str
    subject: str


@dataclasses.dataclass
class WebhookMessage:
    url: str
    retries: int = 0


class BaseMessageSchema(Schema):
    """Shared base schema to exercise inheritance and hook preservation."""

    note = fields.Str(load_default="")

    @pre_load
    def base_pre_load(self, data, **kwargs):
        data = dict(data)
        data["base_pre_load_ran"] = True
        return data

    @post_load
    def base_post_load(self, data, **kwargs):
        data["base_post_load_ran"] = True
        return data


class EmailSchema(BaseMessageSchema):
    address = fields.Email(required=True)
    subject = fields.Str(required=True)
    base_pre_load_ran = fields.Bool(load_default=False)
    base_post_load_ran = fields.Bool(dump_only=True)
    post_dump_ran = fields.Bool(load_default=False)

    @validates("subject")
    def validate_subject(self, value, **kwargs):
        if value == "shout":
            raise ValidationError("No shouting.")

    @post_dump
    def email_post_dump(self, data, **kwargs):
        data["post_dump_ran"] = True
        return data


class WebhookSchema(BaseMessageSchema):
    url = fields.Url(required=True)
    retries = fields.Int(load_default=0)
    base_pre_load_ran = fields.Bool(load_default=False)
    base_post_load_ran = fields.Bool(dump_only=True)


def make_union_field(**kwargs):
    return fields.DiscriminatedUnion(
        {"email": EmailSchema, "webhook": WebhookSchema},
        discriminator="kind",
        dump_types={EmailMessage: "email", WebhookMessage: "webhook"},
        **kwargs,
    )


class MessageSchema(Schema):
    message = make_union_field()


class TestConstruction:
    def test_schemas_must_be_a_non_empty_mapping(self):
        with pytest.raises(ValueError, match="non-empty"):
            fields.DiscriminatedUnion({})
        with pytest.raises(ValueError, match="non-empty"):
            fields.DiscriminatedUnion([EmailSchema])

    def test_dump_types_labels_must_be_registered(self):
        with pytest.raises(ValueError, match="not registered"):
            fields.DiscriminatedUnion(
                {"email": EmailSchema},
                dump_types={EmailMessage: "email", WebhookMessage: "webhook"},
            )

    @pytest.mark.parametrize(
        "schemas",
        [
            {"email": EmailSchema()},
            {"email": "EmailSchema"},
            {"email": lambda: EmailSchema},
        ],
    )
    def test_branch_resolution_forms(self, schemas):
        field = fields.DiscriminatedUnion(schemas)
        assert isinstance(field.branch_schemas["email"], EmailSchema)

    def test_invalid_branch_type_raises(self):
        field = fields.DiscriminatedUnion({"email": 42})
        with pytest.raises(ValueError, match="must be a `Schema`"):
            field.branch_schemas


class TestDeserialization:
    def test_selects_branch_by_discriminator(self):
        result = MessageSchema().load(
            {"message": {"kind": "email", "address": "a@b.com", "subject": "hi"}}
        )
        assert result["message"]["address"] == "a@b.com"
        assert result["message"]["subject"] == "hi"

    def test_only_selected_branch_runs(self):
        # "subject" is required by EmailSchema but unknown to WebhookSchema;
        # loading a webhook must not trigger email validation.
        result = MessageSchema().load(
            {"message": {"kind": "webhook", "url": "https://example.com"}}
        )
        assert result["message"]["url"] == "https://example.com"
        assert result["message"]["retries"] == 0

    def test_missing_discriminator_error(self):
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema().load({"message": {"address": "a@b.com"}})
        assert excinfo.value.messages == {
            "message": ["Missing discriminator field 'kind'."]
        }

    def test_unknown_discriminator_error(self):
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema().load({"message": {"kind": "pigeon"}})
        assert excinfo.value.messages == {
            "message": ["Unknown discriminator value 'pigeon' for field 'kind'."]
        }

    def test_unknown_discriminator_not_falling_back_to_other_branches(self):
        # The payload would be valid for EmailSchema, but the unregistered
        # kind must be rejected instead of trying every branch.
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema().load(
                {"message": {"kind": "EMAIL", "address": "a@b.com", "subject": "x"}}
            )
        assert "Unknown discriminator value" in excinfo.value.messages["message"][0]

    def test_unhashable_discriminator_value(self):
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema().load({"message": {"kind": ["email"]}})
        assert "Unknown discriminator value" in excinfo.value.messages["message"][0]

    def test_non_mapping_input(self):
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema().load({"message": "not a dict"})
        assert excinfo.value.messages == {"message": ["Invalid type."]}

    def test_input_dict_is_not_mutated(self):
        payload = {"kind": "email", "address": "a@b.com", "subject": "hi"}
        snapshot = dict(payload)
        MessageSchema().load({"message": payload})
        assert payload == snapshot

    def test_discriminator_key_not_passed_to_branch(self):
        # Branches use the default unknown=RAISE; the discriminator key must
        # be stripped before delegating or it would be an unknown field.
        result = MessageSchema().load(
            {"message": {"kind": "webhook", "url": "https://example.com"}}
        )
        assert "kind" not in result["message"]

    def test_branch_hooks_preserved(self):
        result = MessageSchema().load(
            {"message": {"kind": "email", "address": "a@b.com", "subject": "hi"}}
        )
        assert result["message"]["base_pre_load_ran"] is True
        assert result["message"]["base_post_load_ran"] is True

    def test_branch_validator_error_path(self):
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema().load(
                {"message": {"kind": "email", "address": "a@b.com", "subject": "shout"}}
            )
        assert excinfo.value.messages == {"message": {"subject": ["No shouting."]}}

    def test_branch_field_error_path(self):
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema().load(
                {"message": {"kind": "email", "address": "nope", "subject": "hi"}}
            )
        assert excinfo.value.messages == {
            "message": {"address": ["Not a valid email address."]}
        }

    def test_inherited_schema_as_branch(self):
        class SpecialEmailSchema(EmailSchema):
            priority = fields.Int(load_default=1)

        class MySchema(Schema):
            message = fields.DiscriminatedUnion({"special": SpecialEmailSchema})

        result = MySchema().load(
            {"message": {"kind": "special", "address": "a@b.com", "subject": "hi"}}
        )
        # Fields and hooks from the whole inheritance chain apply.
        assert result["message"]["priority"] == 1
        assert result["message"]["note"] == ""
        assert result["message"]["base_post_load_ran"] is True

    def test_partial_true_propagates(self):
        result = MessageSchema().load({"message": {"kind": "email"}}, partial=True)
        # partial skips missing fields entirely, so load_default is not applied
        assert result["message"] == {
            "base_pre_load_ran": True,
            "base_post_load_ran": True,
        }

    def test_partial_collection_propagates(self):
        result = MessageSchema().load(
            {"message": {"kind": "email", "address": "a@b.com"}},
            partial=["message.subject"],
        )
        assert result["message"]["address"] == "a@b.com"
        assert "subject" not in result["message"]

    def test_unknown_propagates(self):
        class ExcludeSchema(Schema):
            message = make_union_field(unknown=EXCLUDE)

        result = ExcludeSchema().load(
            {
                "message": {
                    "kind": "webhook",
                    "url": "https://example.com",
                    "extra": 1,
                }
            }
        )
        assert "extra" not in result["message"]

        class IncludeSchema(Schema):
            message = make_union_field(unknown=INCLUDE)

        result = IncludeSchema().load(
            {
                "message": {
                    "kind": "webhook",
                    "url": "https://example.com",
                    "extra": 1,
                }
            }
        )
        assert result["message"]["extra"] == 1

        # Default: the branch schema's own unknown policy (RAISE) applies.
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema().load(
                {"message": {"kind": "webhook", "url": "https://x.com", "extra": 1}}
            )
        assert excinfo.value.messages == {"message": {"extra": ["Unknown field."]}}

    def test_data_key(self):
        class DataKeySchema(Schema):
            message = make_union_field(data_key="payload")

        result = DataKeySchema().load(
            {"payload": {"kind": "webhook", "url": "https://example.com"}}
        )
        assert result["message"]["url"] == "https://example.com"

    def test_none_handling(self):
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema().load({"message": None})
        assert excinfo.value.messages == {"message": ["Field may not be null."]}

        class AllowNoneSchema(Schema):
            message = make_union_field(allow_none=True)

        assert AllowNoneSchema().load({"message": None}) == {"message": None}

    def test_required(self):
        class RequiredSchema(Schema):
            message = make_union_field(required=True)

        with pytest.raises(ValidationError) as excinfo:
            RequiredSchema().load({})
        assert excinfo.value.messages == {
            "message": ["Missing data for required field."]
        }

    def test_custom_error_messages(self):
        class CustomSchema(Schema):
            message = make_union_field(
                error_messages={"missing_discriminator": "No kind given."}
            )

        with pytest.raises(ValidationError) as excinfo:
            CustomSchema().load({"message": {}})
        assert excinfo.value.messages == {"message": ["No kind given."]}

    def test_field_level_validator_runs_on_result(self):
        class ValidatedSchema(Schema):
            message = make_union_field(
                validate=lambda value: (_ for _ in ()).throw(
                    ValidationError("always invalid")
                )
            )

        with pytest.raises(ValidationError) as excinfo:
            ValidatedSchema().load(
                {"message": {"kind": "webhook", "url": "https://example.com"}}
            )
        assert excinfo.value.messages == {"message": ["always invalid"]}


class TestComposition:
    def test_in_list(self):
        class BatchSchema(Schema):
            messages = fields.List(make_union_field())

        result = BatchSchema().load(
            {
                "messages": [
                    {"kind": "email", "address": "a@b.com", "subject": "hi"},
                    {"kind": "webhook", "url": "https://example.com"},
                ]
            }
        )
        assert result["messages"][0]["address"] == "a@b.com"
        assert result["messages"][1]["retries"] == 0

    def test_in_list_error_paths(self):
        class BatchSchema(Schema):
            messages = fields.List(make_union_field())

        with pytest.raises(ValidationError) as excinfo:
            BatchSchema().load(
                {
                    "messages": [
                        {"kind": "email", "address": "a@b.com", "subject": "hi"},
                        {"kind": "pigeon"},
                        {"address": "a@b.com"},
                    ]
                }
            )
        messages = excinfo.value.messages["messages"]
        assert "Unknown discriminator value" in messages[1][0]
        assert "Missing discriminator field" in messages[2][0]

    def test_in_nested(self):
        class OuterSchema(Schema):
            wrapper = fields.Nested(MessageSchema)

        result = OuterSchema().load(
            {"wrapper": {"message": {"kind": "webhook", "url": "https://x.com"}}}
        )
        assert result["wrapper"]["message"]["url"] == "https://x.com"

        with pytest.raises(ValidationError) as excinfo:
            OuterSchema().load({"wrapper": {"message": {"kind": "pigeon"}}})
        assert "Unknown discriminator value" in (
            excinfo.value.messages["wrapper"]["message"][0]
        )

    def test_many_true(self):
        schema = MessageSchema(many=True)
        result = schema.load(
            [
                {"message": {"kind": "email", "address": "a@b.com", "subject": "hi"}},
                {"message": {"kind": "webhook", "url": "https://example.com"}},
            ]
        )
        assert result[0]["message"]["subject"] == "hi"
        assert result[1]["message"]["url"] == "https://example.com"

        with pytest.raises(ValidationError) as excinfo:
            schema.load([{"message": {"kind": "pigeon"}}])
        assert "Unknown discriminator value" in (
            excinfo.value.messages[0]["message"][0]
        )

    def test_same_branch_reused_in_multiple_union_fields(self):
        class TwoMessagesSchema(Schema):
            first = fields.DiscriminatedUnion(
                {"email": EmailSchema, "webhook": WebhookSchema}
            )
            second = fields.DiscriminatedUnion(
                {"email": EmailSchema, "webhook": WebhookSchema}
            )

        result = TwoMessagesSchema().load(
            {
                "first": {"kind": "email", "address": "a@b.com", "subject": "hi"},
                "second": {"kind": "webhook", "url": "https://example.com"},
            }
        )
        assert result["first"]["address"] == "a@b.com"
        assert result["second"]["url"] == "https://example.com"

    def test_same_branch_instance_reused(self):
        shared = EmailSchema()

        class SharedSchema(Schema):
            first = fields.DiscriminatedUnion({"email": shared})
            second = fields.DiscriminatedUnion({"email": shared})

        result = SharedSchema().load(
            {
                "first": {"kind": "email", "address": "a@b.com", "subject": "x"},
                "second": {"kind": "email", "address": "c@d.com", "subject": "y"},
            }
        )
        assert result["first"]["subject"] == "x"
        assert result["second"]["subject"] == "y"


class TestSerialization:
    def test_dump_selects_branch_by_type_mapping(self):
        result = MessageSchema().dump(
            {"message": EmailMessage(address="a@b.com", subject="hi")}
        )
        assert result["message"] == {
            "address": "a@b.com",
            "subject": "hi",
            "post_dump_ran": True,
            "kind": "email",
        }

    def test_dump_injects_discriminator(self):
        result = MessageSchema().dump({"message": WebhookMessage(url="https://x.com")})
        assert result["message"]["kind"] == "webhook"
        assert result["message"]["retries"] == 0

    def test_dump_unregistered_type_rejected(self):
        class Other:
            pass

        with pytest.raises(ValidationError, match="No branch registered"):
            MessageSchema().dump({"message": Other()})

    def test_dump_subclass_not_matched_by_registration_order(self):
        class SpecialEmail(EmailMessage):
            pass

        # Exact-type lookup: a subclass must not silently dump as "email"
        # just because EmailMessage was registered first.
        with pytest.raises(ValidationError, match="No branch registered"):
            MessageSchema().dump(
                {"message": SpecialEmail(address="a@b.com", subject="hi")}
            )

    def test_dump_without_dump_types_raises(self):
        class NoDumpTypesSchema(Schema):
            message = fields.DiscriminatedUnion({"email": EmailSchema})

        with pytest.raises(ValueError, match="dump_types"):
            NoDumpTypesSchema().dump({"message": EmailMessage("a@b.com", "hi")})

    def test_dump_none(self):
        class AllowNoneSchema(Schema):
            message = make_union_field(allow_none=True)

        assert AllowNoneSchema().dump({"message": None}) == {"message": None}

    def test_dump_in_list_and_many(self):
        class BatchSchema(Schema):
            messages = fields.List(make_union_field())

        batch = {
            "messages": [
                EmailMessage(address="a@b.com", subject="hi"),
                WebhookMessage(url="https://x.com"),
            ]
        }
        result = BatchSchema().dump(batch)
        assert [m["kind"] for m in result["messages"]] == ["email", "webhook"]

        schema = MessageSchema(many=True)
        result = schema.dump([{"message": WebhookMessage(url="https://x.com")}])
        assert result[0]["message"]["kind"] == "webhook"

    def test_dump_with_data_key(self):
        class DataKeySchema(Schema):
            message = make_union_field(data_key="payload")

        result = DataKeySchema().dump({"message": WebhookMessage(url="https://x.com")})
        assert result["payload"]["kind"] == "webhook"

    def test_round_trip(self):
        schema = MessageSchema()
        dumped = schema.dump({"message": EmailMessage(address="a@b.com", subject="hi")})
        loaded = schema.load(dumped)
        assert loaded["message"]["address"] == "a@b.com"
        assert loaded["message"]["subject"] == "hi"
