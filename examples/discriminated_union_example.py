"""Example: a message list mixing emails and webhooks, handled by a single
``fields.DiscriminatedUnion`` field that dispatches on the ``kind`` key.

Run with: python examples/discriminated_union_example.py
"""

import dataclasses
import json

from marshmallow import EXCLUDE, Schema, fields, post_load


@dataclasses.dataclass
class EmailMessage:
    address: str
    subject: str


@dataclasses.dataclass
class WebhookMessage:
    url: str
    retries: int = 0


class EmailSchema(Schema):
    address = fields.Email(required=True)
    subject = fields.Str(required=True)

    @post_load
    def make_message(self, data, **kwargs):
        return EmailMessage(**data)


class WebhookSchema(Schema):
    url = fields.Url(required=True)
    retries = fields.Int(load_default=0)

    @post_load
    def make_message(self, data, **kwargs):
        return WebhookMessage(**data)


class MessageBatchSchema(Schema):
    messages = fields.List(
        fields.DiscriminatedUnion(
            {"email": EmailSchema, "webhook": WebhookSchema},
            discriminator="kind",
            dump_types={EmailMessage: "email", WebhookMessage: "webhook"},
            unknown=EXCLUDE,
        )
    )


incoming = {
    "messages": [
        {"kind": "email", "address": "alice@example.com", "subject": "Hi"},
        {"kind": "webhook", "url": "https://example.com/hook", "retries": 3},
        {"kind": "webhook", "url": "https://example.com/other"},
    ]
}

if __name__ == "__main__":
    schema = MessageBatchSchema()
    batch = schema.load(incoming)
    print("Loaded objects:")
    for message in batch["messages"]:
        print(" ", message)

    dumped = schema.dump(batch)
    print("\nDumped JSON:")
    print(json.dumps(dumped, indent=2))

    print("\nInvalid input reports errors per item:")
    bad = {
        "messages": [
            {"kind": "email", "address": "not-an-email"},
            {"kind": "pigeon"},
            {"url": "https://example.com/no-kind"},
        ]
    }
    try:
        schema.load(bad)
    except Exception as error:  # noqa: BLE001
        print(json.dumps(error.messages, indent=2))
