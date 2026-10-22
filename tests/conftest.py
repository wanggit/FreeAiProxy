from __future__ import annotations

import contextlib

import pytest

from freeaiproxy.db import Database


@pytest.fixture(autouse=True)
async def _close_databases():
    created: list[Database] = []
    original = Database.connect

    async def patched(self: Database) -> None:
        created.append(self)
        await original(self)

    Database.connect = patched
    try:
        yield
    finally:
        Database.connect = original
        for db in created:
            with contextlib.suppress(Exception):
                await db.close()
