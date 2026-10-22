from __future__ import annotations

import pytest

from freeaiproxy.api.v1_chat import (
    _ChatMessage,
    _completion_body,
    _content_to_text,
    build_internal_messages,
    validate_model,
)
from freeaiproxy.engine.errors import InvalidRequestError


def test_validate_model_ok():
    assert validate_model("deepseek-chat") == "deepseek-chat"
    assert validate_model("deepseek-reasoner") == "deepseek-reasoner"


def test_validate_model_unknown():
    with pytest.raises(InvalidRequestError):
        validate_model("gpt-4o")


def test_content_to_text_plain():
    assert _content_to_text("hello") == "hello"


def test_content_to_text_parts():
    parts = [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]
    assert _content_to_text(parts) == "A\nB"


def test_content_to_text_ignores_image_parts():
    parts = [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    assert _content_to_text(parts) == "look"


def test_content_to_text_rejects_unknown_part():
    with pytest.raises(InvalidRequestError):
        _content_to_text([{"type": "video", "url": "x"}])


def test_build_internal_messages_drops_system_and_collects_it():
    msgs = [
        _ChatMessage(role="system", content="你是一个助手"),
        _ChatMessage(role="user", content="hi"),
    ]
    internal, sys_text = build_internal_messages(msgs)
    assert sys_text == "你是一个助手"
    assert [(m.role, m.content) for m in internal] == [("user", "hi")]


def test_build_internal_messages_developer_role_treated_as_system():
    msgs = [
        _ChatMessage(role="developer", content="规则"),
        _ChatMessage(role="user", content="hi"),
    ]
    internal, sys_text = build_internal_messages(msgs)
    assert sys_text == "规则"
    assert len(internal) == 1


def test_build_internal_messages_rejects_tool_role():
    msgs = [
        _ChatMessage(role="user", content="hi"),
        _ChatMessage(role="tool", content="x"),
    ]
    with pytest.raises(InvalidRequestError):
        build_internal_messages(msgs)


def test_build_internal_messages_rejects_empty_user():
    msgs = [_ChatMessage(role="user", content="   ")]
    with pytest.raises(InvalidRequestError):
        build_internal_messages(msgs)


def test_build_internal_messages_requires_user():
    msgs = [_ChatMessage(role="system", content="only system")]
    with pytest.raises(InvalidRequestError):
        build_internal_messages(msgs)


def test_completion_body_shape():
    body = _completion_body("chatcmpl-1", "deepseek-chat", 1700000000, "hi", "conv_1")
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "hi"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["session_id"] == "conv_1"
