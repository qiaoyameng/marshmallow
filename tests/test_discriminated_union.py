"""Tests for `fields.DiscriminatedUnion`."""

import pytest

from marshmallow import (
    EXCLUDE,
    INCLUDE,
    RAISE,
    Schema,
    ValidationError,
    fields,
    post_load,
    pre_load,
    validates,
)


class Email:
    def __init__(self, address=None, subject=None):
        self.address = address
        self.subject = subject


class Webhook:
    def __init__(self, url, retries=0):
        self.url = url
        self.retries = retries


class BaseMessageSchema(Schema):
    """Base schema to verify branches may inherit from other schemas."""

    id = fields.Int()


class EmailSchema(BaseMessageSchema):
    address = fields.Email(required=True)
    subject = fields.Str(load_default="")

    @post_load
    def make_email(self, data, **kwargs):
        return Email(**data)


class WebhookSchema(BaseMessageSchema):
    url = fields.Url(required=True)
    retries = fields.Int(load_default=0)

    @post_load
    def make_webhook(self, data, **kwargs):
        return Webhook(**data)


def make_union_field(**kwargs):
    kwargs.setdefault("dump_types", {Email: "email", Webhook: "webhook"})
    return fields.DiscriminatedUnion(
        {"email": EmailSchema, "webhook": WebhookSchema},
        discriminator="kind",
        **kwargs,
    )


class MessageSchema(Schema):
    payload = make_union_field()


@pytest.fixture()
def schema():
    return MessageSchema()


class TestLoad:
    def test_selects_matching_branch(self, schema):
        result = schema.load({"payload": {"kind": "email", "address": "a@b.com"}})
        assert isinstance(result["payload"], Email)
        assert result["payload"].address == "a@b.com"

        result = schema.load(
            {"payload": {"kind": "webhook", "url": "https://example.com/hook"}}
        )
        assert isinstance(result["payload"], Webhook)
        assert result["payload"].url == "https://example.com/hook"

    def test_only_selected_branch_runs(self):
        calls = []

        class TrackedEmailSchema(EmailSchema):
            @pre_load
            def track(self, data, **kwargs):
                calls.append("email")
                return data

        class TrackedWebhookSchema(WebhookSchema):
            @pre_load
            def track(self, data, **kwargs):
                calls.append("webhook")
                return data

        class TrackedSchema(Schema):
            payload = fields.DiscriminatedUnion(
                {"email": TrackedEmailSchema, "webhook": TrackedWebhookSchema},
                discriminator="kind",
                dump_types={Email: "email", Webhook: "webhook"},
            )

        TrackedSchema().load({"payload": {"kind": "email", "address": "a@b.com"}})
        assert calls == ["email"]

    def test_other_branch_constraints_not_enforced(self, schema):
        # "url" is required by the webhook branch only; the email branch
        # must not complain about it.
        result = schema.load({"payload": {"kind": "email", "address": "a@b.com"}})
        assert isinstance(result["payload"], Email)

    def test_missing_discriminator(self, schema):
        with pytest.raises(ValidationError) as excinfo:
            schema.load({"payload": {"address": "a@b.com"}})
        assert excinfo.value.messages == {
            "payload": ["Missing discriminator field 'kind'."]
        }

    def test_unknown_discriminator(self, schema):
        with pytest.raises(ValidationError) as excinfo:
            schema.load({"payload": {"kind": "sms", "address": "a@b.com"}})
        assert excinfo.value.messages == {
            "payload": ["No schema registered for discriminator value 'sms'."]
        }

    def test_unhashable_discriminator(self, schema):
        with pytest.raises(ValidationError) as excinfo:
            schema.load({"payload": {"kind": ["email"]}})
        assert "payload" in excinfo.value.messages

    def test_invalid_input_type(self, schema):
        with pytest.raises(ValidationError) as excinfo:
            schema.load({"payload": "not-a-mapping"})
        assert excinfo.value.messages == {
            "payload": ["Invalid input type. Expected a mapping."]
        }

    def test_branch_validation_errors_keep_path(self, schema):
        with pytest.raises(ValidationError) as excinfo:
            schema.load({"payload": {"kind": "email", "address": "not-an-email"}})
        assert excinfo.value.messages == {
            "payload": {"address": ["Not a valid email address."]}
        }

    def test_input_dict_not_mutated(self, schema):
        data = {"payload": {"kind": "email", "address": "a@b.com"}}
        schema.load(data)
        assert data == {"payload": {"kind": "email", "address": "a@b.com"}}

    def test_branch_hooks_run(self):
        class HookedSchema(Schema):
            value = fields.Str()

            @pre_load
            def pre(self, data, **kwargs):
                data = dict(data)
                data["value"] = data["value"].strip()
                return data

            @validates("value")
            def validate_value(self, value, **kwargs):
                if value == "bad":
                    raise ValidationError("Bad value.")

            @post_load
            def post(self, data, **kwargs):
                data["value"] = data["value"].upper()
                return data

        class HookedUnionSchema(Schema):
            payload = fields.DiscriminatedUnion(
                {"hooked": HookedSchema}, discriminator="kind"
            )

        result = HookedUnionSchema().load(
            {"payload": {"kind": "hooked", "value": "  ok  "}}
        )
        assert result["payload"]["value"] == "OK"

        with pytest.raises(ValidationError) as excinfo:
            HookedUnionSchema().load({"payload": {"kind": "hooked", "value": "bad"}})
        assert excinfo.value.messages == {"payload": {"value": ["Bad value."]}}

    def test_partial_true_propagates(self, schema):
        # "address" is required, but partial=True relaxes it in the branch.
        result = schema.load(
            {"payload": {"kind": "email", "subject": "hi"}}, partial=True
        )
        assert isinstance(result["payload"], Email)
        assert result["payload"].subject == "hi"

    def test_partial_dotted_path_propagates(self, schema):
        result = schema.load(
            {"payload": {"kind": "email", "subject": "hi"}},
            partial=("payload.address",),
        )
        assert isinstance(result["payload"], Email)

    def test_unknown_raise_by_default(self, schema):
        with pytest.raises(ValidationError) as excinfo:
            schema.load(
                {"payload": {"kind": "email", "address": "a@b.com", "extra": 1}}
            )
        assert excinfo.value.messages == {"payload": {"extra": ["Unknown field."]}}

    def test_unknown_exclude_propagates(self):
        class ExcludeSchema(Schema):
            payload = make_union_field(unknown=EXCLUDE)

        result = ExcludeSchema().load(
            {"payload": {"kind": "email", "address": "a@b.com", "extra": 1}}
        )
        assert isinstance(result["payload"], Email)

    def test_unknown_include_propagates(self):
        class PlainSchema(Schema):
            url = fields.Url(required=True)

        class IncludeSchema(Schema):
            payload = fields.DiscriminatedUnion(
                {"plain": PlainSchema}, discriminator="kind", unknown=INCLUDE
            )

        result = IncludeSchema().load(
            {
                "payload": {
                    "kind": "plain",
                    "url": "https://example.com",
                    "extra": 1,
                }
            }
        )
        assert result["payload"]["extra"] == 1

    def test_discriminator_declared_by_branch(self):
        class TaggedEmailSchema(BaseMessageSchema):
            kind = fields.Constant("email")
            address = fields.Email(required=True)

            @post_load
            def make_email(self, data, **kwargs):
                data.pop("kind")
                return Email(**data)

        class TaggedSchema(Schema):
            payload = fields.DiscriminatedUnion(
                {"email": TaggedEmailSchema},
                discriminator="kind",
                dump_types={Email: "email"},
            )

        data = {"payload": {"kind": "email", "address": "a@b.com"}}
        result = TaggedSchema().load(data)
        assert isinstance(result["payload"], Email)
        # The discriminator is declared by the branch, so it round-trips
        # through the branch schema instead of being stripped/injected.
        assert TaggedSchema().dump(result)["payload"]["kind"] == "email"


class TestDump:
    def test_selects_branch_by_type(self, schema):
        result = schema.dump({"payload": Email("a@b.com")})
        assert result["payload"]["kind"] == "email"
        assert result["payload"]["address"] == "a@b.com"

    def test_dump_injects_discriminator(self, schema):
        result = schema.dump({"payload": Webhook("https://example.com")})
        assert result["payload"]["kind"] == "webhook"
        assert result["payload"]["url"] == "https://example.com"

    def test_unknown_type_rejected(self, schema):
        class Unknown:
            pass

        with pytest.raises(ValidationError) as excinfo:
            schema.dump({"payload": Unknown()})
        assert "No `dump_types` entry matches" in str(excinfo.value)

    def test_ambiguous_type_rejected(self):
        class Base:
            pass

        class Child(Base):
            pass

        class AmbiguousSchema(Schema):
            payload = fields.DiscriminatedUnion(
                {"base": WebhookSchema, "child": WebhookSchema},
                discriminator="kind",
                dump_types={Base: "base", Child: "child"},
            )

        # A Child instance matches both entries; registration order must
        # not decide, so this is an error.
        with pytest.raises(ValidationError) as excinfo:
            AmbiguousSchema().dump({"payload": Child()})
        assert "Multiple `dump_types` entries" in str(excinfo.value)
        # An exact Base instance matches only one entry.
        result = AmbiguousSchema().dump({"payload": Base()})
        assert result["payload"]["kind"] == "base"

    def test_dump_none(self, schema):
        assert schema.dump({"payload": None}) == {"payload": None}


class TestComposition:
    def test_in_list(self):
        class ListSchema(Schema):
            payloads = fields.List(make_union_field())

        result = ListSchema().load(
            {
                "payloads": [
                    {"kind": "email", "address": "a@b.com"},
                    {"kind": "webhook", "url": "https://example.com"},
                ]
            }
        )
        assert isinstance(result["payloads"][0], Email)
        assert isinstance(result["payloads"][1], Webhook)

    def test_in_list_error_paths(self):
        class ListSchema(Schema):
            payloads = fields.List(make_union_field())

        with pytest.raises(ValidationError) as excinfo:
            ListSchema().load(
                {
                    "payloads": [
                        {"kind": "email", "address": "a@b.com"},
                        {"kind": "webhook", "url": "nope"},
                    ]
                }
            )
        assert excinfo.value.messages == {
            "payloads": {1: {"url": ["Not a valid URL."]}}
        }

    def test_in_nested(self):
        class InnerSchema(Schema):
            payload = make_union_field()

        class OuterSchema(Schema):
            inner = fields.Nested(InnerSchema)

        result = OuterSchema().load(
            {"inner": {"payload": {"kind": "email", "address": "a@b.com"}}}
        )
        assert isinstance(result["inner"]["payload"], Email)

    def test_many_true(self):
        result = MessageSchema(many=True).load(
            [
                {"payload": {"kind": "email", "address": "a@b.com"}},
                {"payload": {"kind": "webhook", "url": "https://example.com"}},
            ]
        )
        assert isinstance(result[0]["payload"], Email)
        assert isinstance(result[1]["payload"], Webhook)

    def test_many_true_error_paths(self):
        with pytest.raises(ValidationError) as excinfo:
            MessageSchema(many=True).load(
                [
                    {"payload": {"kind": "email", "address": "a@b.com"}},
                    {"payload": {"kind": "email"}},
                ]
            )
        assert excinfo.value.messages == {
            1: {"payload": {"address": ["Missing data for required field."]}}
        }


class TestDataKeyAndNone:
    def test_data_key_on_union_field(self):
        class DataKeySchema(Schema):
            payload = make_union_field(data_key="body")

        result = DataKeySchema().load(
            {"body": {"kind": "email", "address": "a@b.com"}}
        )
        assert isinstance(result["payload"], Email)
        dumped = DataKeySchema().dump(result)
        assert dumped["body"]["kind"] == "email"

    def test_data_key_on_branch_fields(self):
        class KeyedWebhookSchema(Schema):
            url = fields.Url(required=True, data_key="targetUrl")

            @post_load
            def make_webhook(self, data, **kwargs):
                return Webhook(**data)

        class KeyedSchema(Schema):
            payload = fields.DiscriminatedUnion(
                {"webhook": KeyedWebhookSchema},
                discriminator="kind",
                dump_types={Webhook: "webhook"},
            )

        result = KeyedSchema().load(
            {"payload": {"kind": "webhook", "targetUrl": "https://example.com"}}
        )
        assert result["payload"].url == "https://example.com"
        assert KeyedSchema().dump(result)["payload"]["targetUrl"] == (
            "https://example.com"
        )

    def test_allow_none(self):
        class NoneSchema(Schema):
            payload = make_union_field(allow_none=True)

        assert NoneSchema().load({"payload": None}) == {"payload": None}
        assert NoneSchema().dump({"payload": None}) == {"payload": None}

    def test_none_rejected_by_default(self, schema):
        with pytest.raises(ValidationError) as excinfo:
            schema.load({"payload": None})
        assert excinfo.value.messages == {"payload": ["Field may not be null."]}


class TestCustomizationAndReuse:
    def test_custom_error_messages(self):
        class CustomSchema(Schema):
            payload = make_union_field(
                error_messages={
                    "missing_discriminator": "kind is required.",
                    "unknown_discriminator": "bad kind: {value!r}.",
                }
            )

        with pytest.raises(ValidationError) as excinfo:
            CustomSchema().load({"payload": {}})
        assert excinfo.value.messages == {"payload": ["kind is required."]}
        with pytest.raises(ValidationError) as excinfo:
            CustomSchema().load({"payload": {"kind": "sms"}})
        assert excinfo.value.messages == {"payload": ["bad kind: 'sms'."]}

    def test_same_branch_reused_in_multiple_union_fields(self):
        class MultiSchema(Schema):
            first = make_union_field()
            second = make_union_field()

        result = MultiSchema().load(
            {
                "first": {"kind": "email", "address": "a@b.com"},
                "second": {"kind": "email", "address": "c@d.com"},
            }
        )
        assert isinstance(result["first"], Email)
        assert isinstance(result["second"], Email)
        assert result["first"].address == "a@b.com"
        assert result["second"].address == "c@d.com"

    def test_same_branch_shared_between_union_fields(self):
        shared = {"email": EmailSchema, "webhook": WebhookSchema}

        class SharedSchema(Schema):
            incoming = fields.DiscriminatedUnion(
                shared, discriminator="kind", dump_types={Email: "email"}
            )
            outgoing = fields.DiscriminatedUnion(
                shared, discriminator="kind", dump_types={Email: "email"}
            )

        result = SharedSchema().load(
            {
                "incoming": {"kind": "email", "address": "a@b.com"},
                "outgoing": {"kind": "webhook", "url": "https://example.com"},
            }
        )
        assert isinstance(result["incoming"], Email)
        assert isinstance(result["outgoing"], Webhook)

    def test_schema_registration_forms(self):
        class FormsSchema(Schema):
            by_class = fields.DiscriminatedUnion(
                {"email": EmailSchema}, discriminator="kind"
            )
            by_instance = fields.DiscriminatedUnion(
                {"email": EmailSchema()}, discriminator="kind"
            )
            by_string = fields.DiscriminatedUnion(
                {"email": "EmailSchema"}, discriminator="kind"
            )
            by_callable = fields.DiscriminatedUnion(
                {"email": lambda: EmailSchema}, discriminator="kind"
            )

        data = {"kind": "email", "address": "a@b.com"}
        result = FormsSchema().load(
            {
                "by_class": dict(data),
                "by_instance": dict(data),
                "by_string": dict(data),
                "by_callable": dict(data),
            }
        )
        for key in ("by_class", "by_instance", "by_string", "by_callable"):
            assert isinstance(result[key], Email), key
