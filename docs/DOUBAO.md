# 豆包（doubao.com/chat/）适配说明

豆包作为第五个 provider 接入，profile 为 `data/profiles/doubao`，浏览器 locale 为
`zh-CN`。首次使用需在**有头**浏览器里登录一次
（`freeaiproxy relogin --provider doubao`），登录态持久化后无头模式亦可正常调用。

选择器于 2026-10-23 对着真实登录页面校准（探测脚本见 `poc/doubao_probe*.py`）。

## 1. 模型

| 模型 | 能力 | 状态 |
|---|---|---|
| `doubao-chat` | 普通对话 | 已接入 |
| `doubao-image` | Seedream 图像生成（模型/比例/风格） | 已接入（`/v1/images` 或 chat） |
| `doubao-video` | Seedance 视频生成（模型/比例/时长） | 已接入（`/v1/videos`） |
| `doubao-music` | 音乐生成（曲风/情绪/音色/歌词模式） | 已接入（`/v1/music` 或 chat） |

## 2. 登录与会话

- 登录判定用 `[data-testid="to_login_button"]`；登录文案提示只认
  “扫码登录 / 手机号登录 / 密码登录”——**“登录”二字在已登录页面也出现 2 次**
  （账号菜单），不能作为 hint。
- 已登录判定 = composer 出现（`[data-testid="chat_input"] [contenteditable="true"]`）。
- 会话 URL 为 `https://www.doubao.com/chat/<conv_id>`；`conversations.chat_id` 存
  `<conv_id>`，`re` 从 URL 反解。
- 站点正常响应原生鼠标点击，无需 ChatGLM 那种 `evaluate(el.click())` 兜底。

## 3. Composer 与技能

- 输入框：容器 `[data-testid="chat_input"]`，编辑器
  `[data-testid="chat_input_input"] [contenteditable="true"]`，placeholder “发消息...”。
- 发送：`[data-testid="chat_input_send_button"]`。
- 技能栏（testid 是主键，文本标签仅兜底）：
  - 图像生成 `skill_bar_button_3`
  - 视频生成 `skill_bar_button_17`
  - 音乐生成 `skill_bar_button_9`
  - PPT 生成 `skill_bar_button_5000`（**未接入**）
  - 更多 `skill_bar_button_more`（更多菜单里的项用 `get_by_text(label, exact=True)` 点）
- 退出技能：`[data-testid="skill_input_exit_button"]`。
- 进入技能会替换 composer；每次 `prepare()` 显式进技能并对齐选项。

## 4. 收发与完成判定

- 消息：用户 `[data-testid="send_message"]`，助手
  `[data-testid="receive_message"][data-message-role="assistant"]`。
- 正文 `[data-testid="message_text_content"]`，`data-streaming="true"` 表示还在流式输出。
- 排除 `[data-testid="message_thinking"]`（思考链，不返回）与
  `[data-testid="suggest_message_list"]`（追问建议）。
- 完成判定：新助手消息出现 + 正文非空 + 不再 streaming + 文本连续两次轮询稳定。
- 限制提示（“今日…已”“生成数量已达上限”等）命中即抛 `UpstreamError`。

## 5. 图像（doubao-image）

- 模型控件 `[data-input-engine-actionbar-control-key="model"]`；menuitem 文本带尾注
  （如 “Seedream 5.0 Flash 专业快速”），**用前缀匹配**。
- 比例控件无 key，`button:has(span:text("比例"))`，菜单项：
  `自动/9:16/2:3/3:4/1:1/4:3/3:2/16:9`（默认 自动）。
- 风格控件 `[data-input-engine-actionbar-control-key="style"]`，30 种风格见
  `doubao_selectors.IMAGE_STYLES`。
- 菜单结构：**只能取 `[role="menu"]` 里的** `[role="menuitem"]` /
  `[role="menuitemcheckbox"]`——页面上还有一个无 role 的包裹容器含同样文案的项，
  取到外层会点空。
- 结果：`[data-testid="message_image_content"]`，`img` src 同时有 `cthumb`（缩略）与
  `cgen`（大图），按 id 去重优先 `cgen`；`cgen` 可 httpx 直下 2048×2048。
- **限制：图片右下角带 “豆包AI生成” 水印**，网页端没有免水印下载入口。

## 6. 视频（doubao-video）

视频是**两轮代理流程**：

1. 发送 prompt 后，agent 先回一条参数确认（“视频生成参数确认…确认后我再开始生成视频。”）。
   驱动检测到 `_CONFIRM_HINTS`（确认后/请确认/参数确认/确认参数）后自己回 `确认`
   （`S.VIDEO_CONFIRM_TEXT`），渲染才开始。
2. 渲染完成后正文是 “你的视频生成好了。”，**但气泡里没有 `<video>`**：只有封面
   `img`（`aka.doubaocdn.com/s/...`）+ 空的播放器壳
   （`.block-video-*` / `.video-player-*`）。驱动点封面中央的播放图标
   `[data-video-conner-tag="true"]`，播放器随即拉流（`v*-vdl.doubao.com/...video/tos/...`，
   206 `video/mp4`），驱动从 response 里截获这条 URL。

- 模型控件 key 是 `video-model`（**不是 `model`**）：Seedance 2.5 升级 / 2.0 升级 /
  2.0 Fast / 2.0 Mini。
- 比例+时长共用一个面板：`button:has(span:text("比例"))`，menuitem
  `自动/3:4/4:3/9:16/16:9/1:1/21:9` + slider
  `span[role="slider"][data-slot="slider-thumb"]`（min 0 / max 11 / val 6），
  映射 4s..15s（index = 秒数 - 4，默认 10s）。校准（probe18/19）：
  thumb 是 18px 装饰 span，Playwright 永远不认为它可点击——用 JS
  `t.focus()` + 方向键步进，读 `aria-valuenow` 用 JS（节点会重渲染）。
  选完比例面板**保持打开**（选项实时生效），不要再点触发器（会把面板关掉）。
- 落盘：`_save_video` 按内容魔数/`content-type` 存 `.mp4`；`/v1/files/<name>.mp4` 可下载。
- 单次 1 条，Seedance 2.0 Mini 10s 实测约 8–15 分钟；`timeout_completion_s` 需足够大。
- MP4 URL 带签名（`dy_q` 时间戳 + `ft` 哈希），拿到后立即下载；不暴露给客户端。
- URL 里有 `lr=video_gen_watermark_dyn` 参数——疑似动态水印版本，**未验证画面是否带水印**
  （本机无 ffmpeg/ffprobe 可抽帧确认）。

## 7. 音乐（doubao-music）

- composer 内四枚 chips（`[data-slot="dropdown-menu-trigger"]`，按索引）：
  0=歌词模式（AI 帮我写歌词/自定义歌词）、1=曲风、2=情绪、3=音色。
- 主题输入：`input[data-music-theme]`。
- 词汇：曲风 11 项 / 情绪 9 项 / 音色 男声·女声（见 `doubao_selectors`）。
- 生成是**异步**的：同一 answer 容器先出占位，完成信号是
  `[data-testid="music-generation-card-content"][data-finished="true"]`。
- **豆包音乐没有 `<audio>` 元素**：点播放按钮后播放器调
  `POST /alice/media/bigmusic/get_video`，JSON `data.url` 为签名 MP4
  （封面+音轨，`v*-videoweb.doubao.com`）——用 `page.expect_response` 捕获，按 MP4 落盘。
- 实测一首歌约 7 分钟（`timeout_completion_s` 默认 600s 刚好够，别调小）。

## 8. 本地状态与坑

- `data/profiles/doubao/` **就是登录态**，勿删勿提交。
- 探测脚本独占 profile（持久化 context 锁），**校准脚本必须串行跑**。
- `AsyncCamoufox(os="macos")` 必须显式传，否则中文 UI 出豆腐块。
- zsh 里 `echo ====X` 会被 `=cmd` 展开吞掉，脚本里别用 `=` 开头的词。
- `mypy` 只剩既有的 `cli.py:72 msvcrt.locking` 2 个错误（Windows-only 属性）。
