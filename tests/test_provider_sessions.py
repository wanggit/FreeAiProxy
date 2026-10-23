from __future__ import annotations

import sqlite3

import pytest

from freeaiproxy.db import Database
from freeaiproxy.engine.errors import SessionInvalidError
from freeaiproxy.services.session_service import ROLE_USER, Msg, gen_conv_id, resolve


async def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "t.sqlite")
    await db.connect()
    return db


async def test_explicit_session_rejects_other_provider(tmp_path):
    db = await make_db(tmp_path)
    conv_id = gen_conv_id()
    await db.create_conversation(conv_id, provider="deepseek", mode="chat")
    await db.append_history(conv_id, ROLE_USER, "hi")
    with pytest.raises(SessionInvalidError):
        await resolve(
            db,
            session_id=conv_id,
            messages=[Msg(ROLE_USER, "hi")],
            provider="yuanbao",
        )


async def test_auto_match_does_not_cross_providers(tmp_path):
    db = await make_db(tmp_path)
    deepseek = gen_conv_id()
    await db.create_conversation(deepseek, provider="deepseek", mode="chat")
    await db.append_history(deepseek, ROLE_USER, "shared prompt")

    res = await resolve(
        db,
        session_id=None,
        messages=[Msg(ROLE_USER, "shared prompt")],
        provider="yuanbao",
    )
    assert res.mode == "send"
    assert res.conv_id != deepseek
    assert res.conv["provider"] == "yuanbao"


async def test_wenxin_session_is_scoped(tmp_path):
    db = await make_db(tmp_path)
    conv_id = gen_conv_id()
    await db.create_conversation(conv_id, provider="wenxin", mode="image")
    await db.append_history(conv_id, ROLE_USER, "画一只猫")
    await db.append_history(conv_id, "assistant", "已生成")
    with pytest.raises(SessionInvalidError):
        await resolve(
            db,
            session_id=conv_id,
            messages=[Msg(ROLE_USER, "画一只猫")],
            provider="yuanbao",
        )
    res = await resolve(
        db,
        session_id=None,
        messages=[Msg(ROLE_USER, "画一只猫")],
        provider="wenxin",
        mode="image",
    )
    assert res.conv_id == conv_id
    assert res.conv["provider"] == "wenxin"


async def test_yuanbao_mode_is_stored(tmp_path):
    db = await make_db(tmp_path)
    conv_id = gen_conv_id()
    await db.create_conversation(conv_id, provider="yuanbao", mode="thinking")
    conv = await db.get_conversation(conv_id)
    assert conv is not None
    assert conv["provider"] == "yuanbao"
    assert conv["mode"] == "thinking"


async def test_auto_match_does_not_cross_modes(tmp_path):
    db = await make_db(tmp_path)
    thinking = gen_conv_id()
    await db.create_conversation(thinking, provider="yuanbao", mode="thinking")
    await db.append_history(thinking, ROLE_USER, "same prompt")

    res = await resolve(
        db,
        session_id=None,
        messages=[Msg(ROLE_USER, "same prompt")],
        provider="yuanbao",
        mode="expert",
    )
    assert res.mode == "send"
    assert res.conv_id != thinking
    assert res.conv["mode"] == "expert"


async def test_chatglm_session_is_scoped(tmp_path):
    db = await make_db(tmp_path)
    conv_id = gen_conv_id()
    await db.create_conversation(conv_id, provider="chatglm", mode="chat")
    await db.append_history(conv_id, ROLE_USER, "介绍下光合作用")
    await db.append_history(conv_id, "assistant", "光合作用是……")
    with pytest.raises(SessionInvalidError):
        await resolve(
            db,
            session_id=conv_id,
            messages=[Msg(ROLE_USER, "介绍下光合作用")],
            provider="wenxin",
        )
    res = await resolve(
        db,
        session_id=None,
        messages=[Msg(ROLE_USER, "介绍下光合作用")],
        provider="chatglm",
        mode="chat",
    )
    assert res.conv_id == conv_id
    assert res.conv["provider"] == "chatglm"


async def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE conversations (
                conv_id      TEXT PRIMARY KEY,
                chat_id      TEXT,
                status       TEXT NOT NULL DEFAULT 'active',
                created_at   REAL NOT NULL,
                last_used_at REAL NOT NULL
            );
            CREATE TABLE history_msgs (
                conv_id TEXT NOT NULL,
                seq     INTEGER NOT NULL,
                role    TEXT NOT NULL,
                content TEXT NOT NULL,
                PRIMARY KEY (conv_id, seq)
            );
            INSERT INTO conversations
                (conv_id, chat_id, status, created_at, last_used_at)
            VALUES ('conv_old', 'chat-old', 'active', 1.0, 1.0);
            """
        )
    db = Database(path)
    await db.connect()
    try:
        conv = await db.get_conversation("conv_old")
        assert conv is not None
        assert conv["provider"] == "deepseek"
        assert conv["mode"] == "chat"
    finally:
        await db.close()
