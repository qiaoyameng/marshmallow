# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "marshmallow",
# ]
# ///
"""Example: a discriminated union of message payloads.

A message list mixes "email" and "webhook" items. `fields.DiscriminatedUnion`
selects exactly one branch schema by the ``kind`` discriminator on load, and
selects the branch by an explicit type -> tag mapping on dump.
"""

import json
from dataclasses import dataclass
from pprint import pprint

from marshmallow import Schema, ValidationError, fields, post_load, validates


@dataclass
class Email:
    address: str
    subject: str = ""


@dataclass
class Webhook:
    url: str
    retries: int = 0


class EmailSchema(Schema):
    address = fields.Email(required=True)
    subject = fields.Str(load_default="")

    @validates("subject")
    def validate_subject(self, value, **kwargs):
        if value and value == value.upper():
            raise ValidationError("Shouting subjects are not allowed.")

    @post_load
    def make_email(self, data, **kwargs):
        return Email(**data)


class WebhookSchema(Schema):
    url = fields.Url(required=True)
    retries = fields.Int(load_default=0)

    @post_load
    def make_webhook(self, data, **kwargs):
        return Webhook(**data)


class MessageSchema(Schema):
    payload = fields.DiscriminatedUnion(
        {"email": EmailSchema, "webhook": WebhookSchema},
        discriminator="kind",
        # Explicit type -> tag mapping for serialization. Objects that match
        # zero or more than one tag are rejected instead of relying on
        # registration order.
        dump_types={Email: "email", Webhook: "webhook"},
    )


def main():
    schema = MessageSchema(many=True)
    raw = json.loads(
        """
        [
            {"payload": {"kind": "email", "address": "team@example.com",
                         "subject": "hello"}},
            {"payload": {"kind": "webhook",
                         "url": "https://example.com/hook", "retries": 3}}
        ]
        """
    )

    print("=== load: only the branch selected by 'kind' runs ===")
    messages = schema.load(raw)
    pprint(messages)

    print("\n=== dump: branch selected by the explicit type mapping ===")
    pprint(schema.dump(messages))

    print("\n=== the input dictionaries are never mutated ===")
    pprint(raw)

    print("\n=== a missing discriminator is its own error ===")
    try:
        MessageSchema().load({"payload": {"address": "team@example.com"}})
    except ValidationError as error:
        pprint(error.messages)

    print("\n=== an unregistered discriminator is its own error ===")
    try:
        MessageSchema().load({"payload": {"kind": "sms"}})
    except ValidationError as error:
        pprint(error.messages)

    print("\n=== branch hooks and error paths are preserved ===")
    try:
        MessageSchema().load(
            {"payload": {"kind": "email", "address": "team@example.com",
                         "subject": "URGENT"}}
        )
    except ValidationError as error:
        pprint(error.messages)

    print("\n=== unmapped dump types are rejected ===")
    try:
        MessageSchema().dump({"payload": object()})
    except ValidationError as error:
        pprint(error.messages)


if __name__ == "__main__":
    main()
