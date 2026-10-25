from __future__ import annotations

"""Doubao (www.doubao.com/chat/) selectors, calibrated 2026-10-23."""

BASE_URL = "https://www.doubao.com/chat/"
LOGIN_URL = "https://www.doubao.com/login/"

# Presence of the composer means signed in; the login button means the opposite.
LOGGED_IN_SELECTORS = (
    '[data-testid="chat_input"] [contenteditable="true"]',
    '[data-testid="chat_input_input"]',
)
LOGIN_BUTTON = '[data-testid="to_login_button"]'
PLACEHOLDER = "发消息..."

# Entering the image skill pops a radix subscription upsell dialog that covers
# the whole page and intercepts every subsequent click (verified 2026-10-25 in
# the gateway log: the skill-bar click times out on "subtree intercepts
# pointer events"). The close control is not calibrated, so dismissal falls
# back to Escape plus an overlay click.
UPSELL_DIALOG = '[role="dialog"][data-state="open"]'
UPSELL_CLOSE = (
    '[role="dialog"][data-state="open"] [aria-label*="关闭"], '
    '[role="dialog"][data-state="open"] [aria-label*="Close"], '
    '[role="dialog"][data-state="open"] [data-slot="dialog-close"]'
)

COMPOSER = '[data-testid="chat_input"]'
EDITOR = '[data-testid="chat_input_input"] [contenteditable="true"]'
SEND_BUTTON = '[data-testid="chat_input_send_button"]'
UPLOAD_BUTTON = '[data-testid="upload_file_button"]'

# Skill bar (labels drift with A/B tests, so the testids are the primary key
# and the text labels are only the fallback).
SKILL_IMAGE = "skill_bar_button_3"
SKILL_VIDEO = "skill_bar_button_17"
SKILL_MUSIC = "skill_bar_button_9"
SKILL_MORE = "skill_bar_button_more"
SKILL_LABELS = {"image": "图像生成", "video": "视频生成", "music": "音乐生成"}
SKILL_EXIT = '[data-testid="skill_input_exit_button"]'
GUIDANCE_BAR = '[data-testid="guidance-skill-bar"]'

# Composer option controls (image/video skills). The model and style controls
# are radix dropdowns tagged with an actionbar control key; the ratio control
# carries no key and is matched on its "比例 <value>" label.
MODEL_CONTROL = '[data-input-engine-actionbar-control-key="model"]'
STYLE_CONTROL = '[data-input-engine-actionbar-control-key="style"]'
RATIO_CONTROL = 'button:has(span:text("比例"))'
RATIO_LABEL = "比例"
# The video composer merges 比例+时长 into one trigger whose label is
# "自动 · 10s" (no 比例 text at all) and it carries no control key.
VIDEO_RATIO_CONTROL = (
    '[data-testid="chat_input"] '
    'button[data-slot="dropdown-menu-trigger"]:has-text("·")'
)
POPUP_MENU = '[role="menu"]'
# The ratio/style panels are radix dropdowns whose panel node carries
# role="menu" but whose entries are plain <button>s — only some panels (the
# model pickers) use role="menuitem". Always scope to [role="menu"] so the
# role-less wrapper that duplicates the same labels is never matched.
POPUP_ITEM = (
    '[role="menu"] [role="menuitem"], '
    '[role="menu"] [role="menuitemcheckbox"], '
    '[role="menu"] button'
)

# Music skill chips inside the composer (lyrics mode / theme / genre / mood /
# voice). The theme field is a plain input, the rest are dropdown triggers.
MUSIC_CHIPS = '[data-testid="chat_input_input"] [data-slot="dropdown-menu-trigger"]'
MUSIC_THEME_INPUT = '[data-testid="chat_input_input"] input[data-music-theme]'
MUSIC_LYRIC_MARK = "[data-music-lyric]"
MUSIC_OPTION_KEY = "musicGenaration"

# Message list.
MESSAGE_LIST = '[data-testid="message-list"]'
MESSAGE_BLOCK = '[data-testid="message-block-container"]'
USER_MESSAGE = '[data-testid="send_message"]'
ASSISTANT_MESSAGE = '[data-testid="receive_message"][data-message-role="assistant"]'
MESSAGE_CONTENT = '[data-testid="message_content"]'
ANSWER_TEXT = '[data-testid="message_text_content"]'
STREAMING_ATTR = "data-streaming"
THINKING = '[data-testid="message_thinking"]'
SUGGEST_LIST = '[data-testid="suggest_message_list"]'
IMAGE_CONTENT = '[data-testid="message_image_content"]'
MUSIC_CARD = '[data-testid="music-generation-card-content"]'
MUSIC_CARD_FINISHED = "data-finished"
PLAY_BUTTON = '[class*="play-button-wrapper"]'

# Video result card: a cover image inside the assistant bubble plus an empty
# player shell; the MP4 only shows up once the play icon is clicked.
VIDEO_CARD = '[class*="block-video"]'
VIDEO_PLAY_ICON = '[data-video-conner-tag="true"]'
VIDEO_PLAYER = '[class*="video-player"]'
VIDEO_COVER = '[class*="block-video"] img'

# The music player never exposes an <audio> element: playing the card calls
# /alice/media/bigmusic/get_video and streams an MP4 from videoweb.doubao.com.
MUSIC_MEDIA_API = "/alice/media/bigmusic/get_video"
MUSIC_MEDIA_HOSTS = ("v9-videoweb.doubao.com", "v3-videoweb.doubao.com")

# Generated images: the visible src keeps a ~tplv watermark transform, the
# "cgen_dwm3" variant is the full-resolution original.
IMAGE_HOST = "flow-imagex-sign.byteimg.com"

PLACEHOLDER_HINTS = ("发消息", "Message", "Describe")

# Chat reply completion hints: while the model is still working the answer
# area shows one of these placeholders.
BUSY_HINTS = ("正在生成", "生成中", "创作中", "正在作曲", "正在演唱", "排队中", "请稍候")

# Login-panel copy: the literal word 登录 also shows up in the signed-in page
# (account menu), so only these phrases count as a logged-out surface.
LOGIN_HINTS = ("扫码登录", "手机号登录", "密码登录")

# ---------------------------------------------------------------- vocabularies

IMAGE_MODELS = (
    "Seedream 5.0 Flash",
    "Seedream 5.0 Pro 升级",
    "Seedream 4.5",
    "Seedream 4.0",
)
IMAGE_RATIOS = ("9:16", "2:3", "3:4", "1:1", "4:3", "3:2", "16:9")
IMAGE_RATIO_AUTO = "自动"
IMAGE_STYLES = (
    "人像摄影",
    "电影写真",
    "中国风",
    "动漫",
    "3D渲染",
    "赛博朋克",
    "CG 动画",
    "水墨画",
    "油画",
    "古典",
    "水彩画",
    "卡通",
    "平面插画",
    "风景",
    "港风动漫",
    "像素风格",
    "荧光绘画",
    "彩铅画",
    "手办",
    "儿童绘画",
    "抽象",
    "锐笔插画",
    "二次元",
    "油墨印刷",
    "版画",
    "莫奈",
    "毕加索",
    "伦勃朗",
    "马蒂斯",
    "巴洛克",
)

VIDEO_MODELS = (
    "Seedance 2.5 升级",
    "Seedance 2.0 升级",
    "Seedance 2.0 Fast",
    "Seedance 2.0 Mini",
)
VIDEO_RATIOS = ("3:4", "4:3", "9:16", "16:9", "1:1", "21:9")
VIDEO_RATIO_AUTO = "自动"
VIDEO_DURATION_MIN = 4
VIDEO_DURATION_MAX = 15
VIDEO_DURATION_DEFAULT = 10
# The video agent opens with a parameter confirmation turn; answering this
# word starts the render.
VIDEO_CONFIRM_TEXT = "确认"

MUSIC_LYRIC_MODES = ("AI 帮我写歌词", "自定义歌词")
MUSIC_LYRIC_DEFAULT = "AI 帮我写歌词"
MUSIC_GENRES = (
    "流行",
    "嘻哈",
    "国风",
    "DJ",
    "摇滚",
    "民谣",
    "R&B",
    "雷鬼",
    "朋克",
    "电音",
    "爵士",
)
MUSIC_MOODS = (
    "快乐",
    "放松",
    "活力",
    "兴奋",
    "忧郁",
    "鼓舞",
    "伤感",
    "怀旧",
    "浪漫",
)
MUSIC_VOICES = ("男声", "女声")
