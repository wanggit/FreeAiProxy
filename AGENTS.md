# AGENTS.md

This file provides guidance to the AI agent when working with code in this repository.

FreeAiProxy wraps DeepSeek, Tencent Yuanbao, Baidu Wenxin and Zhipu ChatGLM web UIs (Camoufox/Playwright) behind an OpenAI-compatible API. Per-provider calibration details are in `docs/YUANBAO.md`, `docs/WENXIN.md` and `docs/CHATGLM.md`; DeepSeek specifics are in `docs/DESIGN.md`.

## Setup and checks

- First run: `uv sync`, `uv run camoufox fetch` (~1.2 GB browser download), `cp config.example.toml config.toml`.
- `uv run pytest` is unit-only and offline. Lint/type gates: `uv run ruff check .` and `uv run mypy`.
- Scripts under `scripts/` and `poc/` that drive the browser require a running gateway or a real logged-in browser; they are manual checks, not CI tests.
- Use `uv run python scripts/manage.py start|stop|restart|status|build` for background lifecycle. It writes `data/freeaiproxy.pid` and `data/freeaiproxy.log`; `stop` only kills the managed process tree, not an unmanaged server.
- Start with `uv run freeaiproxy start`; browser visibility follows `debug.headed` / `worker.headless`. Login profiles persist.
- uvicorn only binds the port after the lifespan startup yields, so the lifespan must NOT wait on browser launches: provider browsers warm in a background task and `/healthz` reports `state=starting` until ready. `bm.start()` is idempotent (returns if the browser is already running) so a request-triggered recovery and the boot task never launch the same profile twice.

## Provider model

- Providers: `deepseek` (profile `data/profile`), `yuanbao` (profile `data/profiles/yuanbao`), `wenxin` (profile `data/profiles/wenxin`) and `chatglm` (profile `data/profiles/chatglm`), configured under `[providers.*]`. Any subset may run at once.
- Each provider has its own BrowserManager, driver, FIFO RequestQueue and profile. Serialize all page work per provider through its queue; never access one provider's page concurrently. Cross-provider requests may run in parallel.
- Browser-bound requests call `bm.ensure_available()` inside the provider queue. It relaunches a closed, crashed or unresponsive browser from the persisted profile; keep the check inside the queue so recovery stays serialized.
- Conversations are scoped by provider. Auto-match also scopes by mode; an explicit session may switch modes but not providers.
- Models live in `services/models.py`: `yuanbao-hunyuan` = Hy4 + Expert, `yuanbao-hunyuan-thinking` = Hy3 + Thinking, `yuanbao-hunyuan-instant` = Hy3 + Instant, `yuanbao-hunyuan-search` = Hy3 + Instant + Search, plus `yuanbao-writing`, `yuanbao-image`, `yuanbao-ppt`. Wenxin adds `wenxin-image`, `wenxin-writing` and `wenxin-music`. ChatGLM (智谱清言) adds `chatglm-glm5.3` and `chatglm-image`.
- Expert mode has no Search entry in its Add menu. Search is intentionally exposed on Hy3 + Instant; do not try to force it into Expert.

## Yuanbao invariants

- Selectors/constants belong in `engine/yuanbao_selectors.py`; page JS and interactions belong in `engine/yuanbao_driver.py`. DeepSeek keeps `engine/selectors.py` + `engine/ds_driver.py`.
- Conversation URLs are `/chat/<space>/<chat-id>`; `conversations.chat_id` stores `space/chat-id`. On resume the page reloads in normal chat, so re-enter the requested Search/Writing/Image/PPT mode before sending.
- Selecting Hy4 auto-switches to Expert. Align model + mode before every send; for Hy3 modes select Hy3 first, then the mode.
- The answer is `.hyc-common-markdown` inside the last AI item, excluding ancestors containing `__think` and markdown with `hyc-common-markdown-style-cot`. Thinking traces are intentionally not returned; Expert and PPT process blocks are filtered the same way.
- Completion: stop button (`aria-label="Stop Answering"`) gone, `Send` visible, non-empty answer stable. A short answer can appear after the stop button disappears, so never finalize on button state alone. `.hyc-content-md-done` is only a hint.
- Image generation: find `img` elements containing `text2img`/`hunyuan-prod` in the last AI item, hover each one and click `.dic-card-item__download` in its bottom-right corner, then capture the Playwright download to get the watermark-free original. Save to `data/generated/` and serve via `/v1/files/<name>`; the signed COS URL is only a fallback and must not be exposed.
- Image aspect ratio: the `/v1/images/generations` `size` field maps to the AI Image `Ratio` dropdown (`button[aria-label='Ratio']` → `role="menuitemcheckbox"` items `1:1/4:3/3:4/16:9/9:16`, default `Smart Ratio`); select it after entering image mode and before sending.
- The UI is language-sensitive: the browser locale comes from `LoginSpec.locale` (DeepSeek/Yuanbao `en-US`, Wenxin/ChatGLM `zh-CN`) so the site renders the labels each driver's selectors target. Set it per provider, not globally.
- PPT: wait for the `.pptx` file card, click it to open the workspace, hover the Download icon (`.toolbar-export-format_download...`), choose `PPTX`, and capture the Playwright download. Do not return the web file-card link as the artifact.
- Attachments: use Add -> Upload Image / Local Files with a trusted file-chooser click; wait for image `alt` or the composer's `with-files` state. Keep original filenames.
- All API errors must subclass `GatewayError` (`engine/errors.py`).

## Wenxin invariants

- Selectors/constants live in `engine/wenxin_selectors.py`; page JS and interactions in `engine/wenxin_driver.py`. Browser locale is `zh-CN`.
- Conversation URLs are `/search/<numeric-id>`; `conversations.chat_id` stores the numeric id. Login is Baidu passport (a modal over the home page); `LOGGED_IN_SELECTORS` must accept both composers because the home can be left in an agent mode.
- The home (对话) composer is `textarea.ci-textarea`, but every agent mode (图片生成/帮我写作/PPT生成) replaces it with a contenteditable `div.ci-editor`; send via `.ci-submit-button`. Enter also submits but is less reliable, so click the button.
- The answer is the last `[class*="conversation-flow-answer-container"]`; its text is `.cosd-markdown` (follow-up suggestion chips live outside it). Completion = a new answer container appeared, `.cosd-markdown-loading` is gone and the text is stable.
- Image: generated `<img>` srcs are on `gips*.baidu.com/it/u=...`; the src carries a `wm=`/`wmo=` watermark. Stripping those params (`_watermark_free`) yields the clean original — download that, do not expose the watermarked URL. Wenxin's composer has no aspect-ratio control, so `select_image_ratio` is a no-op.
- Music (音乐生成) lives behind the 更多 dropdown, which ignores synthetic clicks: open `.more-dropdown-trigger` with a real mouse click, then click the 音乐生成 capsule. The three directives (选曲风/选音色/AI写歌词) are `.ci-tooltip` triggers whose text becomes the chosen value; options are `.music-directive-button` items. Generation is async in the *same* answer container ("AI音乐创作中" placeholder first, `aCount` never increases): completion = a non-blob `<audio>` src inside `.ai-music-player`, loading icon gone, URL stable. The signed BOS link is named `.mp3` but is actually FLAC — `_save_music` sniffs the magic and stores `.flac`.

## ChatGLM (智谱清言) invariants

- Selectors/constants live in `engine/chatglm_selectors.py`; page JS and interactions in `engine/chatglm_driver.py`. Browser locale is `zh-CN`. ChatGLM calibration details are in `docs/CHATGLM.md`.
- The site swallows Playwright's native mouse clicks: type with `fill()` + Enter, and trigger popover controls via `evaluate(el.click())`.
- Conversations are addressed by the `cid` query param (24-hex), not the path. `conversations.chat_id` stores the cid; draw conversations are prefixed `draw:` so `goto_chat` picks the gdetail agent page.
- GLM-5.3 is selected via the `.think-mode-trigger` popover and always emits a thinking trace; the reply is `.answer-content-wrap` WITHOUT `text-advance-thinking-content`. Completion = `.enter` lost the `searching` class + text stable.
- Image: on the AI画图 agent page force mode radio `GLM Image` and the 去水印 toggle on before sending (watermark removal is a generation-time switch; then the `sfile.chatglm.cn` url itself, query stripped, is the clean original). The mode chip's innerText is CSS-truncated ("GLM"), so verify via the popover's `.el-radio.is-checked` instead.

## DeepSeek invariants

- The message list is virtualized: anchor the reply on the last user message; never infer it from node counts or text diffs against the previous answer.
- Completion uses the two signals plus a long stability fallback; DeepThink is aligned explicitly per turn.
- Streaming is synthesized from DOM polling; on client disconnect the worker must still drain and persist the reply.

## Local state and gotchas

- `data/` is entirely gitignored. `data/profile` and `data/profiles/*` (yuanbao, wenxin, chatglm) **are** the login states: never delete, reset, or commit them. `data/generated/` holds downloaded images, PPTX and audio (`.flac`) files.
- Config layering: `FREEAIPROXY__SECTION__KEY` env vars > `config.toml` > defaults.
- Windows Git Bash passes non-ASCII `curl -d` arguments in the system code page (GBK). `api/middleware.py` transcodes non-UTF-8 JSON bodies via GB18030; keep it when touching request parsing.
- Profile import/sync only supports Firefox and requires Firefox to be closed (locked cookie DB); Chromium is unsupported by design.
- Keep browsers headed during development (`debug.headed = true`); headless increases anti-bot risk. In headless mode, a missing login temporarily relaunches the browser headed and returns to headless after sign-in; `bm.headless` tracks the actual mode.
- PPT generation can take several minutes; use the configured worker timeouts and do not retry blindly.
