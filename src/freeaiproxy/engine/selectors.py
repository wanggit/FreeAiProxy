# Selector registry for the DeepSeek web UI.
# Per DESIGN.md appendix A: every selector carries its intent and the date it
# was last verified against the live page. All page interaction code must
# reference selectors only through this module.
#
# Calibration evidence: poc/dump/calibration.json (2026-10-21, DeepSeek web).

from __future__ import annotations

import re

CHAT_BASE_URL = "https://chat.deepseek.com/"

# New-chat entry page. Verified 2026-10-21 via PoC P7: after loading it the
# message list is empty and the composer is ready.
NEW_CHAT_URL = "https://chat.deepseek.com/a/chat/"

# Extracts DeepSeek's chat id from the page URL. Verified 2026-10-21 via PoC
# P4: https://chat.deepseek.com/a/chat/s/e970cafb-849c-4c38-973e-45ddab7859ce
CHAT_URL_ID_RE = re.compile(r"/a/chat/s/([0-9a-zA-Z_-]+)")

# Chat input box. Verified 2026-10-21 via PoC P1: the composer is a plain
# <textarea> with placeholder "Message DeepSeek".
INPUT_BOX_CANDIDATES = [
    "textarea[placeholder='Message DeepSeek']",
    "textarea",
]

# Assistant answer container. Verified 2026-10-21 via calibration: the reply
# renders as one node carrying both classes; the node count does NOT grow
# during streaming, its text does.
ASSISTANT_MSG_CANDIDATES = [
    ".ds-assistant-message-main-content",
    ".ds-markdown.ds-assistant-message-main-content",
]

# User message container. Verified 2026-10-21 via calibration: user bubbles
# are .ds-message nodes that contain a .ds-collapsible-text child.
USER_MSG_CANDIDATES = [
    ".ds-message:has(.ds-collapsible-text)",
    ".ds-collapsible-text",
]

# Composer buttons. Verified 2026-10-21 via calibration: while the model is
# generating, the primary circle button's class list ends with _52c986b; when
# idle it additionally carries bd74640a (and ds-button--disabled when the
# input is empty). So "generating" == has _52c986b and lacks bd74640a.
GENERATING_BUTTON_CLASS = "_52c986b"
IDLE_BUTTON_CLASS = "bd74640a"
PRIMARY_CIRCLE_BUTTON = ".ds-button.ds-button--primary.ds-button--circle"

# Text that only appears on the DeepSeek login panel. Presence means the
# session expired and the browser must be re-authenticated by hand.
LOGIN_TEXT_HINTS = [
    "获取验证码",
    "手机号登录",
    "邮箱登录",
    "忘记密码",
    "登录 / 注册",
    "Sign in",
    "Log in",
]

# Logged-in indicators. Verified 2026-10-21: a logged-in session renders the
# composer, so the input box doubles as the primary logged-in signal.
LOGGED_IN_CANDIDATES = [
    "textarea[placeholder='Message DeepSeek']",
    "[class*='avatar']",
]

# DeepThink (reasoning) toggle. Verified 2026-10-21 via
# poc/dump/reasoner_calibration.json: the composer carries two
# .ds-toggle-button elements — the reasoning one and a search one. They are
# told apart by the label rendered next to them, so the lookup is done in JS.
DEEPTHINK_TOGGLE = ".ds-toggle-button"
DEEPTHINK_LABEL = "DeepThink"

# Reasoning ("thinking") content. Verified 2026-10-21: in reasoning mode the
# chain of thought renders in its own node, separate from the final answer,
# so the answer can be extracted without the reasoning trace.
THINK_CONTENT = ".ds-think-content"

# Attachment input. Verified 2026-10-21 via poc/dump/upload_*.html: the
# composer carries a hidden <input type="file" multiple> that sits right
# after the attachment button, so files can be attached with set_input_files
# instead of driving the native file chooser.
FILE_INPUT = "input[type='file']"

# Attachment preview. An image renders as <img alt="filename">, a document
# renders as a file card that shows the file name as text. The file name
# therefore shows up in the DOM for both kinds once the upload finished.
ATTACHMENT_PREVIEW = "img[alt]"
