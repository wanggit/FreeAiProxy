# 腾讯元宝（Yuanbao）接入说明

本文记录 `https://yuanbao.tencent.com/` 的标定结论与实现方式。DeepSeek 的网页驱动仍见
[DESIGN.md](DESIGN.md)。面向调用方的完整接口说明见 [API.md](API.md)。

## 1. 多 Provider 架构

- 每个 provider 拥有独立的 `BrowserManager`、页面驱动、持久 profile 和 FIFO `RequestQueue`。
- provider 内部严格串行；DeepSeek 与元宝使用两个浏览器，跨 provider 可并行。
- `conversations.provider` 标识会话所属 provider；显式 `session_id` 不能跨 provider。
- `conversations.mode` 记录最近使用的元宝模式；auto-match 只匹配同 provider + 同 mode，
  显式 `session_id` 可在同 provider 内切换模式。

配置：

```toml
[providers.deepseek]
enabled = true
profile = "data/profile"

[providers.yuanbao]
enabled = true
profile = "data/profiles/yuanbao"
```

## 2. 模型映射

| API model | 元宝页面组合 | 说明 |
|---|---|---|
| `yuanbao-hunyuan` | Hy4 preview + Expert | 选择 Hy4 会自动切到 Expert |
| `yuanbao-hunyuan-thinking` | Hy3 + Thinking | 只返回最终答案 |
| `yuanbao-hunyuan-instant` | Hy3 + Instant | 快速对话 |
| `yuanbao-hunyuan-search` | Hy3 + Instant + Search | 强制联网搜索 |
| `yuanbao-writing` | Writing（General） | 专业写作，返回 markdown |
| `yuanbao-image` | AI Image（Hy Image3.5） | 图片生成，images 端点 / chat 均可 |
| `yuanbao-ppt` | Presentation Maker | 返回摘要 + 本地 .pptx 链接 |

模型定义集中在 `services/models.py`。

**Expert 与 Search 的限制**：Expert 模式的 Add 菜单只有 Upload Image / Local Files /
Tencent Docs，没有 Search。因此 `yuanbao-hunyuan` 不带显式搜索；强制搜索由
`yuanbao-hunyuan-search`（Hy3 + Instant）提供。

## 3. 登录与 profile

- 元宝登录态保存在 `data/profiles/yuanbao`，与 DeepSeek 的 `data/profile` 完全隔离。
- 登录方式：微信扫码、QQ、手机号验证码。
- 登录判定：可见的 `[class*="hyc-login"]` / `[class*="hyc-phone-login"]` /
  `.agent-dialogue__tool__login` 视为未登录；否则 `.ql-editor` 可见即视为已登录。
- 也可以从 Firefox profile 导入：`freeaiproxy profile import --browser firefox --provider yuanbao`。

## 4. 会话与页面行为

- 新会话：`https://yuanbao.tencent.com/` 会跳到 `/chat/<space>`。
- 首条消息发送后 URL 变为 `/chat/<space>/<chat-id>`；网关把 `space/chat-id` 存到
  `conversations.chat_id`。
- 刷新会话时页面回到**普通聊天**模式，因此续接时会重新进入目标模式：
  - Search：Add → Search
  - Writing：点击 Writing
  - AI Image：点击 AI Image
  - PPT：先对齐 Hy4 + Expert，再点击 Presentation Maker
- 模式切换会保留会话历史（实测 Search / Writing / Image 均如此）。

## 5. 模型/模式对齐

- 模型菜单位于 `button[aria-label="Switch model"]`：
  - `Models` 行（`button[aria-label="Select model"]`）打开模型子菜单：Hy4 preview、Hy3、DeepSeek。
  - 同一菜单内的 `menuitemradio` 是 Instant / Thinking / Expert。
- 选 Hy4 会自动切 Expert；Hy3 选择后可能重置为 Instant，所以每轮都显式选择目标模式。
- 模式/模型对齐状态可以从 `button[aria-label="Switch model"]` 的文本（Expert/Thinking/Instant）
  和 `Models` 行的文本（Hy4 preview/Hy3）读取。
- Search 激活后输入框占位符变为 `Enter a topic for real-time web insights`，
  并出现 `.app-search-bar` 的 Search 标签。

## 6. 回复提取与完成判定

- 消息项：`.agent-chat__list__item--human` / `.agent-chat__list__item--ai`。
- 最终答案：最后一个 AI 项内的 `.hyc-common-markdown`，但要排除：
  - 祖先类名含 `__think`（Thinking/Expert 过程）；
  - 自身类名含 `hyc-common-markdown-style-cot`（思考内容）；
  - 过程列表 `[class*="process-list"]`。
- 答案可能被拆成多个 markdown 块，按文档顺序拼接。
- PPT 摘要位于外层 `hyc-component-deep-search-agent`，不会被上述过滤排除。
- 完成信号：
  - `Stop Answering` 按钮消失；
  - `Send` 按钮出现；
  - 非空答案文本稳定至少 2 个采样周期。
- 短答案可能在停止按钮消失后才落到 DOM，因此不能只看按钮状态；`.hyc-content-md-done`
  只能作为辅助信号。
- 流式响应通过轮询 DOM 生成增量；客户端断开时仍会 drain 并写入历史。

## 7. 联网搜索

- 入口：Add → Search（强制搜索）。
- 搜索过程先出现一个 AI 项，几秒后答案 markdown 才出现；首字超时按 AI 项文本活动刷新。
- 参考答案包含“来源”名称；来源面板的 URL 暂未抽取，因此 v1 不返回来源链接。

## 8. 附件与多模态

- Add 菜单：
  - Upload Image：文件选择器上传图片；
  - Local Files：上传文档；
  - Tencent Docs：外部文档（未接入）。
- 图片上传完成后，输入区出现 `img[alt="<文件名>"]` 预览；文档上传完成后输入区进入
  `--with-files` 状态。
- 文件选择器必须由真实用户手势触发；实现中使用 `page.expect_file_chooser()` 配合
  坐标鼠标点击，避免菜单动画导致的 Playwright actionability 超时。
- 混元可直接识别上传图片；文档会由元宝解析后作为上下文。

## 9. AI 生图

- 元宝一次生成 4 张图，约 15–25 秒。
- 宽高比：AI Image 工具条上的 `button[aria-label='Ratio']` 下拉，选项为
  `role="menuitemcheckbox"`，文本 `1:1 / 4:3 / 3:4 / 16:9 / 9:16 / Smart Ratio`
  （默认 Smart Ratio）。`/v1/images/generations` 的 `size` 字段映射到该选择。
- 生成的 `<img>` 来自 `hunyuan-prod.../text2img/...`，`src` 是带签名的缩略图 URL。
- 无水印下载：悬停图片后右下角出现 `.dic-card-item__download`，点击并捕获
  Playwright download，保存到 `data/generated/`，返回 `/v1/files/<name>`。
  账号需在元宝设置中开启“无水印保存”。
- 如果下载图标未出现，回退为去掉 `&imageMogr2...` 后直接下载原图；该方式带水印。
- `POST /v1/images/generations` 支持 `n` 1–4、`response_format=url|b64_json`。
- 聊天模型 `yuanbao-image` 返回 4 个本地 markdown 图片链接。

## 10. PPT 生成

- 入口：Hy4 + Expert 下的 `Presentation Maker`。
- 网页先返回摘要 markdown，并生成 `.pptx` 文件卡片。
- 下载流程：
  1. 点击 `.file-card...pptx` 文件卡片打开工作区；
  2. 悬浮 `.toolbar-export-format_download...` 下载图标；
  3. 点击下拉项 `PPTX`；
  4. 用 Playwright `expect_download` 保存文件。
- 生成/下载可能耗时数分钟；文件保存到 `data/generated/`，聊天响应返回摘要 + 本地链接。

## 11. 已知限制

- Expert 模式没有显式 Search；搜索模型使用 Hy3 + Instant。
- Thinking/Expert 思考过程不返回。
- 搜索来源 URL 未抽取。
- 暂未接入元宝的 DeepSeek 模型、Deep Research、Solve、Data Insights、投资/生活/旅行等技能。
- 元宝 UI 语言取决于账号/浏览器；当前标定基于英文 UI，改语言后文案选择器可能需要更新。
- 生成文件不会自动清理。

## 12. 验证脚本

标定脚本位于 `poc/`，使用独立 profile，可复用：

```bash
uv run python poc/yuanbao_calibrate.py       # 登录 + 首页
uv run python poc/yuanbao_probe_ui.py        # 模型/模式/生图/写作/附件菜单
uv run python poc/yuanbao_probe_chat.py      # 文本流式与 URL
uv run python poc/yuanbao_probe_attach.py    # 图片/文档附件
uv run python poc/yuanbao_probe_thinking.py  # Thinking
uv run python poc/yuanbao_probe_ppt.py       # PPT 生成
uv run python poc/yuanbao_probe_ppt_download3.py  # PPTX 下载
```

这些脚本会打开有头浏览器，需要已登录的 profile。
