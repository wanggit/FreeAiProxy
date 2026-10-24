"""Selectors and page constants for Zhipu ChatGLM (智谱清言) web UI.

Calibrated against the live logged-in page on 2026-10-23 (probes
poc/chatglm_probe*.py). The main chat lives on /main/alltoolsdetail; the
AI画图 agent is a fixed "gdetail" page. Both conversations are addressed via
the `cid` query parameter, not the path.
"""

from __future__ import annotations

import re

BASE_URL = "https://chatglm.cn/"
CHAT_URL = "https://chatglm.cn/main/alltoolsdetail?lang=zh"
# AI画图 agent page (agent id is stable).
DRAW_URL = "https://chatglm.cn/main/gdetail/65a232c082ff90a2ad2f15e2?lang=zh"
CID_RE = re.compile(r"[?&]cid=([0-9a-f]{12,32})")
# Prefix stored in conversations.chat_id to mark a draw (image) conversation.
DRAW_REF_PREFIX = "draw:"

# Composer. Both the chat and the draw page use the same #search-input-box
# textarea; Enter submits. Native mouse clicks are swallowed by the page, but
# Playwright fill()/keyboard events work (verified 2026-10-23).
INPUT_BOX = "#search-input-box textarea"
# The send control gains the `searching` class while a reply is streaming.
ENTER_BUTTON = ".enter"
SEARCHING_CLASS = "searching"

# Conversation flow. Each turn is a .conversation.question plus an .answer.
# The answer body is .answer-content-wrap WITHOUT the
# text-advance-thinking-content class (that one holds the thinking trace,
# which is intentionally not returned). Rendered markdown sits in
# .markdown-body inside each wrap.
QUESTION_ITEM = ".conversation.question"
AI_ITEM = ".answer"
ANSWER_WRAP = ".answer-content-wrap"
THINKING_WRAP_CLASS = "text-advance-thinking-content"

# Model popover: clicking .think-mode-trigger opens .think-mode-list with
# .think-mode-item entries (.item-name = model). Thinking strength is a
# submenu of the same popover. GLM-5.3 always thinks before answering.
MODEL_TRIGGER = ".think-mode-trigger"
MODEL_ITEM_NAME = ".item-name"
MODEL_GLM53 = "GLM-5.3"

# Draw composer toolbar chips.
PROMPT_ITEM = ".prompt-item"
WM_TOGGLE_TEXT = "去水印"
WM_ON_TEXT = "去水印（开）"
WM_ON_OPTION = "开启"
MODE_GLM_IMAGE = "GLM Image"
# Popover shared by the toolbar chips (mode select / watermark switch).
DRAW_POPOVER = ".draw_options_popover"

# Reference-image upload panel inside the AI画图 composer: a hidden
# input[type=file] (accept=image/*) whose thumbnails render as <img> inside
# .ai-draw-upload (verified 2026-10-24).
DRAW_UPLOAD_INPUT = ".ai-draw-upload input[type=file]"

# Generated image thumbnails: <img class="limitSize"> inside
# .img-answer-item. Srcs are sfile.chatglm.cn CDN urls; with 去水印 on at
# generation time the raw jpg (query stripped) is watermark-free
# (verified 2026-10-23).
RESULT_IMG = ".img-answer-item img.limitSize"
RESULT_IMG_HOST = "sfile.chatglm.cn"

# Login detection. Verified 2026-10-23: a login modal over the home page with
# WeChat QR + phone/SMS inputs when signed out.
LOGIN_SURFACES = (
    'input[placeholder="请输入手机号"]',
    'input[placeholder="请输入短信验证码"]',
    ".submit-btn.apply-btn",
)
LOGIN_HINTS = (
    "微信扫码登录",
    "手机号登录",
    "未注册手机号将自动注册",
    "登录送积分好礼",
)
LOGGED_IN_SELECTORS = (INPUT_BOX,)

RATE_LIMIT_HINTS = ("积分不足", "过于频繁", "请稍后再试", "使用次数已达上限")
