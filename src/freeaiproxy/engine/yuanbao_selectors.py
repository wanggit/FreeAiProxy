"""Selectors and page constants for the Tencent Yuanbao web UI.

Calibrated against the live logged-in page on 2026-10-22. Every interaction
with Yuanbao must go through this module.
"""

from __future__ import annotations

import re

BASE_URL = "https://yuanbao.tencent.com/"
CHAT_BASE_URL = "https://yuanbao.tencent.com/chat/"

# Conversation URLs look like /chat/<space>/<chat-id>; both parts are needed
# to navigate back, so the driver stores "space/chat-id" as the chat ref.
CHAT_PATH_RE = re.compile(r"/chat/([0-9A-Za-z]+/[0-9A-Za-z]+)")

INPUT_BOX = ".ql-editor"
SEND_BUTTON = "[aria-label='Send']"
STOP_BUTTON = "[aria-label='Stop Answering']"
ADD_BUTTON = "button[aria-label='Add']"
SWITCH_MODEL_BUTTON = "button[aria-label='Switch model']"
SELECT_MODEL_ITEM = "button[aria-label='Select model']"

HUMAN_ITEM = ".agent-chat__list__item--human"
AI_ITEM = ".agent-chat__list__item--ai"
MARKDOWN = ".hyc-common-markdown"
MARKDOWN_DONE = ".hyc-content-md-done"

# Thinking traces live in separate containers and must never be returned as
# the assistant answer.
THINK_CONTAINERS = (
    '[class*="deepsearch-cot__think"]',
    '[class*="deep-search-agent__think"]',
)
THINK_MARKDOWN_CLASS = "hyc-common-markdown-style-cot"

SEARCH_PILL = ".app-search-bar"
FILE_CARD = ".file-card_fileCard__gfdDP"
PPT_DOWNLOAD_BUTTON = (
    ".toolbar-export-format_download__KVu6U[aria-label='Download']"
)

LOGIN_SURFACES = (
    '[class*="hyc-login"]',
    '[class*="hyc-phone-login"]',
    ".agent-dialogue__tool__login",
)
LOGIN_HINTS = (
    "Not logged in",
    "Please scan the QR code",
    "Verification Code",
    "未登录",
    "扫码登录",
    "验证码登录",
)
LOGGED_IN_SELECTORS = (INPUT_BOX,)

RATE_LIMIT_HINTS = ("频率过高", "操作频繁", "稍后再试")

# Hovering a generated image reveals this control in the card's bottom-right
# corner; clicking it downloads the watermark-free original.
IMAGE_DOWNLOAD_BUTTON = ".dic-card-item__download"

# AI Image exposes a "Ratio" dropdown (role=menuitemcheckbox items) that sets
# the aspect ratio of the next generation. "Smart Ratio" is the web default.
IMAGE_RATIO_BUTTON = "button[aria-label='Ratio']"
IMAGE_RATIO_ITEM = '[role="menuitemcheckbox"]'
IMAGE_RATIOS = ("1:1", "4:3", "3:4", "16:9", "9:16")
IMAGE_RATIO_SMART = "Smart Ratio"

PLACEHOLDER_WRITING = "Enter your topic"
PLACEHOLDER_IMAGE = "Describe the image"
PLACEHOLDER_SEARCH = "real-time web insights"
PLACEHOLDER_PPT = "Enter the topic, style"
