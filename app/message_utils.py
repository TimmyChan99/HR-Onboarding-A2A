from __future__ import annotations

import json
from typing import Any

from a2a.types import Message, Part
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value


class MessagePayloadError(ValueError):
    pass


def command_from_message(message: Message) -> dict[str, Any]:
    message_dict = MessageToDict(message, preserving_proto_field_name=True)
    for part in message_dict.get("parts", []):
        data = part.get("data")
        if isinstance(data, dict):
            return data

        text = part.get("text")
        if isinstance(text, str) and text.strip():
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

    raise MessagePayloadError(
        "The A2A message must contain an application/json data part or JSON text part"
    )


def data_part(payload: dict[str, Any]) -> Part:
    return Part(
        data=ParseDict(payload, Value()),
        media_type="application/json",
    )
