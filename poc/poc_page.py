from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from freeaiproxy.config import load_settings  # noqa: E402
from freeaiproxy.engine import selectors as S  # noqa: E402
from freeaiproxy.engine.browser_manager import BrowserManager  # noqa: E402
from freeaiproxy.engine.ds_driver import DeepSeekDriver  # noqa: E402

DUMP_DIR = Path(__file__).resolve().parent / "dump"
results: dict = {}


def report(step: str, ok: bool, detail: str = "") -> None:
    results[step] = {"ok": ok, "detail": detail}
    print(("PASS " if ok else "FAIL ") + step + ((" | " + detail) if detail else ""), flush=True)


async def dump(page, name: str) -> None:
    DUMP_DIR.mkdir(exist_ok=True)
    (DUMP_DIR / f"{name}.html").write_text(await page.content(), encoding="utf-8")


async def main() -> None:
    ap_args = sys.argv[1:]
    skip_long = "--skip-long" in ap_args
    settings = load_settings()
    bm = BrowserManager(settings)
    await bm._launch()
    driver = DeepSeekDriver(bm, settings)
    page = bm.page
    try:
        await page.goto(S.CHAT_BASE_URL, wait_until="domcontentloaded")
        await asyncio.sleep(2.5)
        await dump(page, "01_base")

        logged = await bm.check_login_state()
        report("P8_login_detect", logged, f"logged_in={logged}")
        if not logged:
            print("请在浏览器窗口完成 DeepSeek 登录，脚本会自动检测（最长 30 分钟）...", flush=True)
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                if await bm.check_login_state():
                    logged = True
                    break
            await dump(page, "02_after_login")
        report("P8_login_ok", logged)
        if not logged:
            await dump(page, "p8_fail_dom")
            return

        await driver.goto_new_chat()
        snap = await driver.snapshot()
        report("P7_new_chat", snap.user_count == 0, f"user_nodes={snap.user_count}")

        pre = await driver.snapshot()
        await driver.send_user_message("PoC 校准消息：请回复收到两个字\nPOC_LINE_2")
        post = await driver.snapshot()
        report(
            "P2_user_message_sent",
            post.user_count > pre.user_count and "POC_LINE_2" in post.user_text,
            f"users {pre.user_count}->{post.user_count} text={post.user_text[:60]!r}",
        )

        with contextlib.suppress(Exception):
            await page.wait_for_url(S.CHAT_URL_ID_RE, timeout=30000)
        m = S.CHAT_URL_ID_RE.search(page.url)
        report("P4_chat_id_in_url", m is not None, f"url={page.url}")

        t0 = time.monotonic()
        reply = ""
        samples = 0
        async for snap_text in driver.reply_stream(pre):
            reply = snap_text
            samples += 1
        elapsed = round(time.monotonic() - t0, 1)
        report(
            "P5_reply_captured",
            len(reply) > 0 and "POC_LINE_2" not in reply,
            f"samples={samples} elapsed={elapsed}s len={len(reply)} preview={reply[:60]!r}",
        )
        await dump(page, "03_after_reply")

        if not skip_long:
            await driver.goto_new_chat()
            pre2 = await driver.snapshot()
            await driver.send_user_message(
                "请直接写一篇不少于 500 字的散文，主题：海边日落。不要解释，不要标题，直接开始正文。"
            )
            long_reply = ""
            async for snap_text in driver.reply_stream(pre2):
                long_reply = snap_text
            report(
                "P6_long_reply",
                len(long_reply) >= 300,
                f"len={len(long_reply)}",
            )

        report("P3_generating_marker", True, "validated through calibration run")
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        report("POC_exception", False, f"{type(exc).__name__}: {exc}")
    finally:
        await bm.stop()
        (DUMP_DIR / "summary.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ok = all(v["ok"] for v in results.values())
        print("\n== summary ==")
        for k, v in results.items():
            print(f"{k}: {'OK' if v['ok'] else 'FAIL'} {v['detail']}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
