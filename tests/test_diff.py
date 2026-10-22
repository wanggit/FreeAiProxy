from __future__ import annotations

import pytest

from freeaiproxy.db import Database
from freeaiproxy.engine.errors import (
    HistoryConflictError,
    InvalidRequestError,
    SessionInvalidError,
)
from freeaiproxy.services.session_service import (
    ROLE_ASSISTANT,
    ROLE_USER,
    Msg,
    resolve,
)


async def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "t.sqlite")
    await db.connect()
    return db


async def create_bound_conv(db, messages: list[Msg]) -> str:
    from freeaiproxy.services.session_service import gen_conv_id

    conv_id = gen_conv_id()
    await db.create_conversation(conv_id)
    await db.bind_chat_id(conv_id, "fake-chat-id")
    for m in messages:
        await db.append_history(conv_id, m.role, m.content)
    return conv_id


async def test_new_conversation(tmp_path):
    db = await make_db(tmp_path)
    res = await resolve(db, session_id=None, messages=[Msg(ROLE_USER, "hi")])
    assert res.mode == "send"
    assert res.pending_user == "hi"
    assert res.conv["chat_id"] is None
    conv = await db.get_conversation(res.conv_id)
    assert conv is not None and conv["status"] == "active"


async def test_system_merged_on_creation(tmp_path):
    db = await make_db(tmp_path)
    res = await resolve(
        db, session_id=None, messages=[Msg(ROLE_USER, "hi")], sys_text="你可以扮演一个诗人"
    )
    assert res.mode == "send"
    assert res.pending_user.startswith("你可以扮演一个诗人")
    assert "hi" in res.pending_user


async def test_explicit_continue_send(tmp_path):
    db = await make_db(tmp_path)
    conv_id = await create_bound_conv(
        db, [Msg(ROLE_USER, "hi"), Msg(ROLE_ASSISTANT, "来自助手")]
    )
    res = await resolve(
        db,
        session_id=conv_id,
        messages=[
            Msg(ROLE_USER, "hi"),
            Msg(ROLE_ASSISTANT, "来自助手"),
            Msg(ROLE_USER, "第二条"),
        ],
    )
    assert res.mode == "send"
    assert res.pending_user == "第二条"


async def test_explicit_tolerates_client_assistant_text_drift(tmp_path):
    db = await make_db(tmp_path)
    conv_id = await create_bound_conv(
        db, [Msg(ROLE_USER, "hi"), Msg(ROLE_ASSISTANT, "来自助手")]
    )
    res = await resolve(
        db,
        session_id=conv_id,
        messages=[
            Msg(ROLE_USER, "hi"),
            Msg(ROLE_ASSISTANT, "客户端自己改写过的回复"),
            Msg(ROLE_USER, "第二条"),
        ],
    )
    assert res.mode == "send"
    assert res.pending_user == "第二条"


async def test_explicit_repeat_returns_cached(tmp_path):
    db = await make_db(tmp_path)
    conv_id = await create_bound_conv(
        db, [Msg(ROLE_USER, "hi"), Msg(ROLE_ASSISTANT, "旧回复")]
    )
    res = await resolve(
        db,
        session_id=conv_id,
        messages=[Msg(ROLE_USER, "hi"), Msg(ROLE_ASSISTANT, "旧回复")],
    )
    assert res.mode == "repeat"
    assert res.cached_reply == "旧回复"
    assert res.pending_user is None


async def test_explicit_repeat_when_client_omits_assistant_reply(tmp_path):
    db = await make_db(tmp_path)
    conv_id = await create_bound_conv(
        db, [Msg(ROLE_USER, "hi"), Msg(ROLE_ASSISTANT, "旧回复")]
    )
    res = await resolve(db, session_id=conv_id, messages=[Msg(ROLE_USER, "hi")])
    assert res.mode == "repeat"
    assert res.cached_reply == "旧回复"


async def test_explicit_repeat_returns_reply_of_the_matching_turn(tmp_path):
    db = await make_db(tmp_path)
    conv_id = await create_bound_conv(
        db,
        [
            Msg(ROLE_USER, "u1"),
            Msg(ROLE_ASSISTANT, "a1"),
            Msg(ROLE_USER, "u2"),
            Msg(ROLE_ASSISTANT, "a2"),
        ],
    )
    res = await resolve(
        db,
        session_id=conv_id,
        messages=[Msg(ROLE_USER, "u1"), Msg(ROLE_ASSISTANT, "a1")],
    )
    assert res.mode == "repeat"
    assert res.cached_reply == "a1"


async def test_explicit_conflict_when_first_user_message_differs(tmp_path):
    db = await make_db(tmp_path)
    conv_id = await create_bound_conv(
        db, [Msg(ROLE_USER, "hi"), Msg(ROLE_ASSISTANT, "来自助手")]
    )
    with pytest.raises(HistoryConflictError):
        await resolve(
            db,
            session_id=conv_id,
            messages=[Msg(ROLE_USER, "完全不同的问题"), Msg(ROLE_USER, "第二条")],
        )


async def test_explicit_conflict_two_new_user_messages(tmp_path):
    db = await make_db(tmp_path)
    conv_id = await create_bound_conv(db, [Msg(ROLE_USER, "u1")])
    with pytest.raises(HistoryConflictError):
        await resolve(
            db,
            session_id=conv_id,
            messages=[Msg(ROLE_USER, "u1"), Msg(ROLE_USER, "u2"), Msg(ROLE_USER, "u3")],
        )


async def test_unknown_session(tmp_path):
    db = await make_db(tmp_path)
    with pytest.raises(SessionInvalidError):
        await resolve(db, session_id="conv_nope", messages=[Msg(ROLE_USER, "hi")])


async def test_dead_session(tmp_path):
    db = await make_db(tmp_path)
    res = await resolve(db, session_id=None, messages=[Msg(ROLE_USER, "hi")])
    await db.mark_dead(res.conv_id)
    with pytest.raises(SessionInvalidError):
        await resolve(db, session_id=res.conv_id, messages=[Msg(ROLE_USER, "hi")])


async def test_last_message_must_be_user(tmp_path):
    db = await make_db(tmp_path)
    with pytest.raises(InvalidRequestError):
        await resolve(
            db, session_id=None, messages=[Msg(ROLE_USER, "hi"), Msg(ROLE_ASSISTANT, "x")]
        )


async def test_first_must_be_user(tmp_path):
    db = await make_db(tmp_path)
    with pytest.raises(InvalidRequestError):
        await resolve(db, session_id=None, messages=[Msg(ROLE_ASSISTANT, "hi")])


async def test_empty_messages(tmp_path):
    db = await make_db(tmp_path)
    with pytest.raises(InvalidRequestError):
        await resolve(db, session_id=None, messages=[])
