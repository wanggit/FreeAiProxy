from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Literal

from freeaiproxy.db import Database
from freeaiproxy.engine.errors import (
    HistoryConflictError,
    InvalidRequestError,
    SessionInvalidError,
)

ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

SendMode = Literal["send", "repeat"]


@dataclass
class Msg:
    role: str
    content: str
    attachments: list = field(default_factory=list)


@dataclass
class Resolution:
    conv_id: str
    conv: dict
    mode: SendMode
    pending_user: str | None = None
    pending_attachments: list = field(default_factory=list)
    cached_reply: str | None = None
    adopt_pairs: list[tuple[str, str]] = field(default_factory=list)


def gen_conv_id() -> str:
    return "conv_" + secrets.token_hex(6)


def prefix_len(history: list[dict], incoming: list[Msg]) -> int:
    n = 0
    limit = min(len(history), len(incoming))
    while n < limit:
        h = history[n]
        m = incoming[n]
        if h["role"] != m.role or (h["content"] or "") != m.content:
            break
        n += 1
    return n


def align_user_messages(
    history: list[dict], incoming: list[Msg]
) -> tuple[int, list[Msg]]:
    """Align the client's messages against the stored history by user turns.

    The gateway owns the real conversation (it lives in the DeepSeek web app),
    so the client's rendering of past assistant turns is not authoritative: it
    may be trimmed, re-formatted or simply missing. Alignment therefore
    compares only the user turns. Returns (aligned user count, new user
    messages that are not in the stored history yet).
    """
    hist_users = [m for m in history if m["role"] == ROLE_USER]
    inc_users = [m for m in incoming if m.role == ROLE_USER]
    k = 0
    while (
        k < len(hist_users)
        and k < len(inc_users)
        and (hist_users[k]["content"] or "") == inc_users[k].content
    ):
        k += 1
    return k, inc_users[k:]


def _reply_for_user_turn(history: list[dict], aligned: int) -> str | None:
    """Assistant reply that belongs to the aligned-th (1-indexed) user turn."""
    seen = 0
    for i, row in enumerate(history):
        if row["role"] != ROLE_USER:
            continue
        seen += 1
        if seen == aligned:
            for j in range(i + 1, len(history)):
                if history[j]["role"] == ROLE_ASSISTANT:
                    return history[j]["content"]
            return None
    return None


def _last_assistant(history: list[dict]) -> str | None:
    for row in reversed(history):
        if row["role"] == ROLE_ASSISTANT:
            return row["content"]
    return None


def _validate_interesting(interesting: list[Msg]) -> None:
    if not interesting:
        raise InvalidRequestError("messages must contain at least one user message")
    if interesting[0].role != ROLE_USER:
        raise InvalidRequestError("the conversation must start with a user message")


def leading_system_text(messages: list[Msg]) -> str:
    parts: list[str] = []
    for m in messages:
        if m.role != ROLE_SYSTEM:
            break
        if m.content:
            parts.append(m.content)
    return "\n\n".join(parts)


async def _resolve_explicit(
    db: Database, session_id: str, incoming: list[Msg], provider: str
) -> Resolution:
    conv = await db.get_conversation(session_id)
    if conv is None:
        raise SessionInvalidError(f"session '{session_id}' does not exist")
    if conv["status"] != "active":
        raise SessionInvalidError("conversation is no longer active")
    if conv["provider"] != provider:
        raise SessionInvalidError(
            f"session '{session_id}' belongs to provider '{conv['provider']}', "
            f"not '{provider}'"
        )
    history = await db.get_history(session_id)
    aligned, new_users = align_user_messages(history, incoming)
    if history and aligned == 0:
        raise HistoryConflictError(
            "incoming history diverges from the stored conversation history; "
            "start a new conversation or verify the history you send"
        )
    if len(new_users) == 0:
        cached = _reply_for_user_turn(history, aligned)
        if cached is None:
            cached = _last_assistant(history)
        if cached is None:
            raise HistoryConflictError("conversation has no recorded reply to repeat")
        return Resolution(conv_id=session_id, conv=conv, mode="repeat", cached_reply=cached)
    if len(new_users) == 1:
        return Resolution(
            conv_id=session_id,
            conv=conv,
            mode="send",
            pending_user=new_users[0].content,
            pending_attachments=list(new_users[0].attachments),
        )
    raise HistoryConflictError(
        "at most one new user message may be appended per request; use a new conversation instead"
    )


async def _new_conversation(
    db: Database, incoming: list[Msg], sys_text: str, provider: str, mode: str
) -> Resolution:
    if incoming[-1].role != ROLE_USER:
        raise InvalidRequestError("the last message must come from the user")
    first_user = incoming[0].content
    pending = (sys_text + "\n\n" + first_user) if sys_text else first_user
    conv_id = gen_conv_id()
    await db.create_conversation(conv_id, provider=provider, mode=mode)
    conv = {
        "conv_id": conv_id,
        "chat_id": None,
        "status": "active",
        "provider": provider,
        "mode": mode,
    }
    return Resolution(
        conv_id=conv_id,
        conv=conv,
        mode="send",
        pending_user=pending,
        pending_attachments=list(incoming[0].attachments),
    )


async def resolve(
    db: Database,
    *,
    session_id: str | None,
    messages: list[Msg],
    sys_text: str = "",
    provider: str = "deepseek",
    mode: str = "chat",
) -> Resolution:
    interesting = list(messages)
    _validate_interesting(interesting)
    if session_id:
        return await _resolve_explicit(
            db, session_id, incoming=interesting, provider=provider
        )

    convs = await db.list_active_conversations(provider=provider, mode=mode)
    best: tuple[int, int, dict, str | None, Msg | None] | None = None
    for conv in convs:
        history = await db.get_history(conv["conv_id"])
        if not history:
            continue
        aligned, new_users = align_user_messages(history, interesting)
        if aligned == 0:
            continue
        if len(new_users) == 0:
            cached = _reply_for_user_turn(history, aligned) or _last_assistant(history)
            if cached is None:
                continue
            kind = 0
            candidate: tuple[int, int, dict, str | None, Msg | None] = (
                aligned,
                kind,
                conv,
                cached,
                None,
            )
        elif len(new_users) == 1:
            kind = 1
            candidate = (aligned, kind, conv, None, new_users[0])
        else:
            continue
        if best is None or candidate[0] > best[0] or candidate[0] == best[0] and candidate[1] < best[1]:
            best = candidate
    if best is not None:
        _, kind, conv, cached, new_msg = best
        if kind == 0:
            return Resolution(
                conv_id=conv["conv_id"], conv=conv, mode="repeat", cached_reply=cached
            )
        assert new_msg is not None
        return Resolution(
            conv_id=conv["conv_id"],
            conv=conv,
            mode="send",
            pending_user=new_msg.content,
            pending_attachments=list(new_msg.attachments),
        )
    return await _new_conversation(db, interesting, sys_text, provider, mode)
