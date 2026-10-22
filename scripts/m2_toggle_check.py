"""Check the DeepThink toggle follows the requested model on every turn."""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "src")

from freeaiproxy.config import load_settings  # noqa: E402
from freeaiproxy.engine.browser_manager import BrowserManager  # noqa: E402
from freeaiproxy.engine.ds_driver import DeepSeekDriver  # noqa: E402


async def main() -> None:
    bm = BrowserManager(load_settings())
    await bm._launch()
    driver = DeepSeekDriver(bm, load_settings())
    page = bm.page
    assert page is not None
    try:
        await driver.goto_new_chat()
        print("initial think state:", await driver.read_think_state())

        await driver.ensure_think_mode(True)
        print("after ensure(True):", await driver.read_think_state())

        await driver.ensure_think_mode(False)
        print("after ensure(False):", await driver.read_think_state())

        await driver.ensure_think_mode(True)
        print("after ensure(True) again:", await driver.read_think_state())

        # toggle survives a navigation to an existing chat?
        await driver.goto_new_chat()
        print("after navigation:", await driver.read_think_state())

        # and on a real chat page
        await driver.send_user_message("测试一下思考开关是否生效，只回复OK。", think=True)
        print("after send(think=True):", await driver.read_think_state())
        pre = await driver.snapshot()
        reply = await driver.wait_completion(pre)
        print("reply:", repr(reply[:60]))
        print("think state after reply:", await driver.read_think_state())
    finally:
        await bm.stop()


asyncio.run(main())
