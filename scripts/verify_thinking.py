"""Verify a reasoner request really produced a thinking trace in the web app.

Reads the chat_id the gateway stored for a session, reopens that DeepSeek
conversation in a browser and looks for the reasoning node.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "src")

from freeaiproxy.config import load_settings  # noqa: E402
from freeaiproxy.engine import selectors as S  # noqa: E402
from freeaiproxy.engine.browser_manager import BrowserManager  # noqa: E402

DB = Path("data/freeaiproxy.db")


def chat_id_for(conv_id: str) -> str | None:
    conn = sqlite3.connect(DB)
    try:
        row = conn.execute(
            "SELECT chat_id FROM conversations WHERE conv_id = ?", (conv_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


async def main() -> None:
    conv_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not conv_id:
        rows = sqlite3.connect(DB).execute(
            "SELECT conv_id, chat_id FROM conversations WHERE chat_id IS NOT NULL ORDER BY last_used_at DESC"
        ).fetchall()
        if not rows:
            print("no conversation with a chat id yet")
            return
        conv_id, chat_id = rows[0]
    else:
        chat_id = chat_id_for(conv_id)
    if not chat_id:
        print("no chat id for", conv_id)
        return
    print(f"session {conv_id} -> chat {chat_id}")

    bm = BrowserManager(load_settings())
    await bm._launch()
    page = bm.page
    assert page is not None
    try:
        await page.goto(f"{S.CHAT_BASE_URL}a/chat/s/{chat_id}", wait_until="domcontentloaded")
        await asyncio.sleep(4)
        think = await page.evaluate(
            """() => Array.from(document.querySelectorAll('.ds-think-content'))
                .map(el => ({len: (el.innerText || '').length,
                             text: (el.innerText || '').slice(0, 120)}))"""
        )
        answers = await page.evaluate(
            """() => Array.from(document.querySelectorAll('.ds-assistant-message-main-content'))
                .map(el => (el.innerText || '').slice(0, 80))"""
        )
        print("think nodes:", think)
        print("answers:", answers)
        if think:
            print("RESULT: THINKING TRACE PRESENT")
        else:
            print("RESULT: NO THINKING TRACE")
    finally:
        await bm.stop()


asyncio.run(main())
