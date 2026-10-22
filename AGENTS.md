# AGENTS.md

This file provides guidance to the AI agent when working with code in this repository.

FreeAiProxy wraps DeepSeek and Tencent Yuanbao web UIs (Camoufox/Playwright) behind an OpenAI-compatible API. Yuanbao calibration details are in `docs/YUANBAO.md`; DeepSeek specifics are in `docs/DESIGN.md`.

## Setup and checks

- First run: `uv sync`, `uv run camoufox fetch` (~1.2 GB browser download), `cp config.example.toml config.toml`.
- `uv run pytest` is unit-only and offline. Lint/type gates: `uv run ruff check .` and `uv run mypy`.
- Scripts under `scripts/` and `poc/` that drive the browser require a running gateway or a real logged-in browser; they are manual checks, not CI tests.
- Use `uv run python scripts/manage.py start|stop|restart|status|build` for background lifecycle. It writes `data/freeaiproxy.pid` and `data/freeaiproxy.log`; `stop` only kills the managed process tree, not an unmanaged server.
- Start with `uv run freeaiproxy start`; browser visibility follows `debug.headed` / `worker.headless`. Login profiles persist.

## Provider model

- Providers: `deepseek` (profile `data/profile`) and `yuanbao` (profile `data/profiles/yuanbao`), configured under `[providers.*]`. Both may run at once.
- Each provider has its own BrowserManager, driver, FIFO RequestQueue and profile. Serialize all page work per provider through its queue; never access one provider's page concurrently. Cross-provider requests may run in parallel.
- Browser-bound requests call `bm.ensure_available()` inside the provider queue. It relaunches a closed, crashed or unresponsive browser from the persisted profile; keep the check inside the queue so recovery stays serialized.
- Conversations are scoped by provider. Auto-match also scopes by mode; an explicit session may switch modes but not providers.
- Models live in `services/models.py`: `yuanbao-hunyuan` = Hy4 + Expert, `yuanbao-hunyuan-thinking` = Hy3 + Thinking, `yuanbao-hunyuan-instant` = Hy3 + Instant, `yuanbao-hunyuan-search` = Hy3 + Instant + Search, plus `yuanbao-writing`, `yuanbao-image`, `yuanbao-ppt`.
- Expert mode has no Search entry in its Add menu. Search is intentionally exposed on Hy3 + Instant; do not try to force it into Expert.

## Yuanbao invariants

- Selectors/constants belong in `engine/yuanbao_selectors.py`; page JS and interactions belong in `engine/yuanbao_driver.py`. DeepSeek keeps `engine/selectors.py` + `engine/ds_driver.py`.
- Conversation URLs are `/chat/<space>/<chat-id>`; `conversations.chat_id` stores `space/chat-id`. On resume the page reloads in normal chat, so re-enter the requested Search/Writing/Image/PPT mode before sending.
- Selecting Hy4 auto-switches to Expert. Align model + mode before every send; for Hy3 modes select Hy3 first, then the mode.
- The answer is `.hyc-common-markdown` inside the last AI item, excluding ancestors containing `__think` and markdown with `hyc-common-markdown-style-cot`. Thinking traces are intentionally not returned; Expert and PPT process blocks are filtered the same way.
- Completion: stop button (`aria-label="Stop Answering"`) gone, `Send` visible, non-empty answer stable. A short answer can appear after the stop button disappears, so never finalize on button state alone. `.hyc-content-md-done` is only a hint.
- Image generation: find `img` elements containing `text2img`/`hunyuan-prod` in the last AI item, hover each one and click `.dic-card-item__download` in its bottom-right corner, then capture the Playwright download to get the watermark-free original. Save to `data/generated/` and serve via `/v1/files/<name>`; the signed COS URL is only a fallback and must not be exposed.
- Image aspect ratio: the `/v1/images/generations` `size` field maps to the AI Image `Ratio` dropdown (`button[aria-label='Ratio']` → `role="menuitemcheckbox"` items `1:1/4:3/3:4/16:9/9:16`, default `Smart Ratio`); select it after entering image mode and before sending.
- The UI is language-sensitive: keep `locale="en-US"` on Camoufox so headed and headless runs both render the English labels the selectors target.
- PPT: wait for the `.pptx` file card, click it to open the workspace, hover the Download icon (`.toolbar-export-format_download...`), choose `PPTX`, and capture the Playwright download. Do not return the web file-card link as the artifact.
- Attachments: use Add -> Upload Image / Local Files with a trusted file-chooser click; wait for image `alt` or the composer's `with-files` state. Keep original filenames.
- All API errors must subclass `GatewayError` (`engine/errors.py`).

## DeepSeek invariants

- The message list is virtualized: anchor the reply on the last user message; never infer it from node counts or text diffs against the previous answer.
- Completion uses the two signals plus a long stability fallback; DeepThink is aligned explicitly per turn.
- Streaming is synthesized from DOM polling; on client disconnect the worker must still drain and persist the reply.

## Local state and gotchas

- `data/` is entirely gitignored. `data/profile` and `data/profiles/yuanbao` **are** the login states: never delete, reset, or commit them. `data/generated/` holds downloaded images and PPTX files.
- Config layering: `FREEAIPROXY__SECTION__KEY` env vars > `config.toml` > defaults.
- Windows Git Bash passes non-ASCII `curl -d` arguments in the system code page (GBK). `api/middleware.py` transcodes non-UTF-8 JSON bodies via GB18030; keep it when touching request parsing.
- Profile import/sync only supports Firefox and requires Firefox to be closed (locked cookie DB); Chromium is unsupported by design.
- Keep browsers headed during development (`debug.headed = true`); headless increases anti-bot risk. In headless mode, a missing login temporarily relaunches the browser headed and returns to headless after sign-in; `bm.headless` tracks the actual mode.
- PPT generation can take several minutes; use the configured worker timeouts and do not retry blindly.
