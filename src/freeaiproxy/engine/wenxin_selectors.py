"""Selectors and page constants for the Baidu Wenxin (文心) web UI.

Calibrated against the live logged-in page on 2026-10-23, including the
音乐生成 (music) composer and its 曲风 / 音色 / 歌词 directives.
"""

from __future__ import annotations

import re

BASE_URL = "https://wenxin.baidu.com/"
# A conversation lives at /search/<numeric-id>?enter_type=chat_url.
CHAT_BASE_URL = "https://wenxin.baidu.com/search/"
CHAT_PATH_RE = re.compile(r"/search/(\d+)")

# Composer. Verified 2026-10-23: the home (对话) composer is
# <textarea class="ci-textarea">, but every agent mode (图片生成/帮我写作/
# PPT生成) replaces it with a contenteditable <div class="ci-editor">. The send
# control is <span class="ci-submit-button"> in both cases.
INPUT_BOX = "textarea.ci-textarea"
EDITOR = "div.ci-editor"
COMPOSER = "textarea.ci-textarea, div.ci-editor"
SEND_BUTTON = ".ci-submit-button"

# Conversation flow items. Verified 2026-10-23: each turn renders a question
# container and an answer container; the assistant text lives in .cosd-markdown
# (the follow-up suggestion chips live outside it, so they are excluded).
QUESTION_ITEM = '[class*="conversation-flow-question-container"]'
AI_ITEM = '[class*="conversation-flow-answer-container"]'
ANSWER_MARKDOWN = ".cosd-markdown"
# Present only while the markdown is still streaming; its absence is the
# completion signal.
LOADING = ".cosd-markdown-loading"

# Agent mode capsules shown above the composer. Clicking one switches the
# composer into that capability.
MODE_CAPSULE = ".capsule-text"
MODE_IMAGE = "图片生成"
MODE_WRITING = "帮我写作"

# 音乐生成 is not on the visible capsule row: it only appears in the DOM after
# the .more-dropdown-trigger ("更多") is opened with a real mouse click
# (synthetic clicks do not expand it; verified 2026-10-23).
MORE_TRIGGER = ".more-dropdown-trigger"
MODE_MUSIC = "音乐生成"
# The music composer exposes three directive triggers (.ci-tooltip) whose text
# becomes the chosen value after selection.
MUSIC_TOOL_TIP = ".ci-tooltip"
MUSIC_OPTION = ".music-directive-button"
MUSIC_STYLE_TRIGGER = "选曲风"
MUSIC_VOICE_TRIGGER = "选音色"
MUSIC_LYRICS_TRIGGER = "AI写歌词"
MUSIC_STYLES = (
    "默认", "摇滚", "电子", "R&B", "嘻哈", "金属", "蓝调",
    "流行", "民谣", "爵士", "说唱", "拉丁", "朋克",
)
MUSIC_VOICES = ("智能匹配", "深情男声", "甜美女声")
MUSIC_LYRICS_AUTO = "AI写歌词"
MUSIC_LYRICS_CUSTOM = "自定义歌词"
MUSIC_PLAYER = ".ai-music-player"

# Login detection. Verified 2026-10-23 against the Baidu passport modal that
# overlays the home page when signed out.
LOGIN_SURFACES = (
    '[class*="pass-module"]',
    '[class*="pass-text-input"]',
    '[class*="phoenix-login"]',
)
LOGIN_HINTS = (
    "请登录",
    "扫码登录",
    "请使用百度APP扫码登录",
    "账号登录",
    "短信登录",
)
LOGGED_IN_SELECTORS = (INPUT_BOX, EDITOR)

RATE_LIMIT_HINTS = ("过于频繁", "操作频繁", "请稍后再试")
