from __future__ import annotations

from freeaiproxy.db import Database
from freeaiproxy.services.session_service import ROLE_ASSISTANT, ROLE_USER, Msg, resolve


async def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "t.sqlite")
    await db.connect()
    return db


async def seed(db: Database, history: list[Msg], bind: bool = True) -> str:
    from freeaiproxy.services.session_service import gen_conv_id

    conv_id = gen_conv_id()
    await db.create_conversation(conv_id)
    if bind:
        await db.bind_chat_id(conv_id, "fake-chat-id")
    for m in history:
        await db.append_history(conv_id, m.role, m.content)
    return conv_id


async def test_auto_match_prefers_most_aligned_conversation(tmp_path):
    db = await make_db(tmp_path)
    await seed(db, [Msg(ROLE_USER, "q1"), Msg(ROLE_ASSISTANT, "r1")])
    conv2 = await seed(
        db,
        [
            Msg(ROLE_USER, "q1"),
            Msg(ROLE_ASSISTANT, "r1"),
            Msg(ROLE_USER, "q2"),
            Msg(ROLE_ASSISTANT, "r2"),
        ],
    )
    incoming = [
        Msg(ROLE_USER, "q1"),
        Msg(ROLE_ASSISTANT, "r1"),
        Msg(ROLE_USER, "q2"),
        Msg(ROLE_ASSISTANT, "r2"),
        Msg(ROLE_USER, "q3"),
    ]
    res = await resolve(db, session_id=None, messages=incoming)
    assert res.mode == "send"
    assert res.conv_id == conv2
    assert res.pending_user == "q3"


async def test_auto_match_repeat_returns_cached_reply(tmp_path):
    db = await make_db(tmp_path)
    await seed(db, [Msg(ROLE_USER, "q1"), Msg(ROLE_ASSISTANT, "r1")])
    conv2 = await seed(
        db,
        [
            Msg(ROLE_USER, "q1"),
            Msg(ROLE_ASSISTANT, "r1"),
            Msg(ROLE_USER, "q2"),
            Msg(ROLE_ASSISTANT, "r2"),
        ],
    )
    incoming = [
        Msg(ROLE_USER, "q1"),
        Msg(ROLE_ASSISTANT, "r1"),
        Msg(ROLE_USER, "q2"),
        Msg(ROLE_ASSISTANT, "r2"),
    ]
    res = await resolve(db, session_id=None, messages=incoming)
    assert res.mode == "repeat"
    assert res.conv_id == conv2
    assert res.cached_reply == "r2"


async def test_auto_match_new_conversation_without_user_overlap(tmp_path):
    db = await make_db(tmp_path)
    await seed(db, [Msg(ROLE_USER, "q1"), Msg(ROLE_ASSISTANT, "r1")])
    res = await resolve(db, session_id=None, messages=[Msg(ROLE_USER, "无关的新对话")])
    assert res.mode == "send"
    assert res.conv["chat_id"] is None
    stored = await db.get_history(res.conv_id)
    assert stored == []


async def test_auto_match_ignores_client_assistant_text_drift(tmp_path):
    db = await make_db(tmp_path)
    conv1 = await seed(db, [Msg(ROLE_USER, "q1"), Msg(ROLE_ASSISTANT, "r1")])
    res = await resolve(
        db,
        session_id=None,
        messages=[
            Msg(ROLE_USER, "q1"),
            Msg(ROLE_ASSISTANT, "客户端改写过的内容"),
            Msg(ROLE_USER, "q2"),
        ],
    )
    assert res.mode == "send"
    assert res.conv_id == conv1
    assert res.pending_user == "q2"


async def test_auto_match_binds_to_conversation_whose_history_is_a_prefix(tmp_path):
    db = await make_db(tmp_path)
    conv_long = await seed(
        db,
        [
            Msg(ROLE_USER, "q1"),
            Msg(ROLE_ASSISTANT, "r1"),
            Msg(ROLE_USER, "q2"),
            Msg(ROLE_ASSISTANT, "r2"),
        ],
    )
    res = await resolve(
        db,
        session_id=None,
        messages=[Msg(ROLE_USER, "q1"), Msg(ROLE_ASSISTANT, "r1"), Msg(ROLE_USER, "q3")],
    )
    assert res.mode == "send"
    assert res.conv_id == conv_long
    assert res.pending_user == "q3"
