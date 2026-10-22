# FreeAiProxy

把 **DeepSeek** 与 **腾讯元宝** 网页版包装成 **OpenAI 兼容 API**。用反检测浏览器
（Camoufox）驱动 https://chat.deepseek.com/ 和 https://yuanbao.tencent.com/ ，对外暴露
`POST /v1/chat/completions` 和 `POST /v1/images/generations`，任何支持 OpenAI 协议的
客户端都能接入。

- 多 Provider：DeepSeek 与元宝可同时在线，各自独立浏览器、登录态和 FIFO 队列
- DeepSeek：普通对话 + 深度思考
- 元宝：Hy4（Expert）、Thinking、Instant、强制联网搜索、专业写作、AI 生图、PPT 生成
- 支持流式（SSE）、会话续接、附件上传（图片 / 文档）
- 生图与 PPT 落盘为本地文件，由网关提供下载链接
- 登录一次长期复用（持久化浏览器 profile）
- 支持无头运行；仅在需要登录时临时弹出有头浏览器，登录成功后自动切回

> 完整 API 文档：[docs/API.md](docs/API.md)
>
> 元宝页面标定与实现细节见 [docs/YUANBAO.md](docs/YUANBAO.md)；
> DeepSeek 设计见 [docs/DESIGN.md](docs/DESIGN.md)。

## 快速开始

```bash
uv sync
uv run camoufox fetch        # 首次需要下载浏览器二进制（约 1.2GB）
cp config.example.toml config.toml

uv run freeaiproxy start     # 会同时启动 DeepSeek / 元宝两个有头浏览器
```

首次启动时，如果某个 provider 未登录，对应浏览器窗口会等待人工登录：
DeepSeek 用手机号/邮箱，元宝用微信 / QQ / 手机号。登录态分别保存在
`data/profile` 和 `data/profiles/yuanbao`。

服务默认监听 `http://127.0.0.1:8000`：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"你好"}]}'
```

> Windows Git Bash 会把命令行里的中文按 GBK 传给原生 curl；网关会自动做兼容解码。
> 其他客户端仍应发送 UTF-8，也可以把 JSON 写入 UTF-8 文件后用 `--data-binary @body.json`。

## 模型

| model | Provider | 对应网页能力 |
|---|---|---|
| `deepseek-chat` | DeepSeek | 普通对话 |
| `deepseek-reasoner` | DeepSeek | 深度思考（DeepThink） |
| `yuanbao-hunyuan` | 元宝 | Hy4 preview + Expert |
| `yuanbao-hunyuan-thinking` | 元宝 | Hy3 + Thinking |
| `yuanbao-hunyuan-instant` | 元宝 | Hy3 + Instant |
| `yuanbao-hunyuan-search` | 元宝 | Hy3 + Instant + 强制联网搜索 |
| `yuanbao-writing` | 元宝 | Writing（专业写作） |
| `yuanbao-image` | 元宝 | AI Image（Hy Image3.5） |
| `yuanbao-ppt` | 元宝 | Presentation Maker（PPT 生成） |

说明：

- `yuanbao-hunyuan` 选中 Hy4 后网页会自动切到 Expert；这是元宝当前唯一带 Expert 的入口。
- Expert 模式的 Add 菜单没有 Search 入口，因此强制搜索单独映射到 Hy3 + Instant 的
  `yuanbao-hunyuan-search`。
- Thinking / Expert 的思考过程不会返回，只返回最终答案。
- 每个模型每轮都会显式对齐网页上的模型/模式，同一会话可切换模型；显式传入
  `session_id` 时可在同一元宝会话内切换模式。

### 示例

```bash
# 元宝 Hy4 / Expert
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"yuanbao-hunyuan","messages":[{"role":"user","content":"你好"}]}'

# 元宝 Thinking
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"yuanbao-hunyuan-thinking","messages":[{"role":"user","content":"9.11 和 9.9 哪个大？"}]}'

# 元宝强制联网搜索
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"yuanbao-hunyuan-search","messages":[{"role":"user","content":"今天杭州天气怎么样？"}]}'

# 元宝专业写作
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"yuanbao-writing","messages":[{"role":"user","content":"写一份城市夜跑活动策划文案"}]}'
```

## 附件上传

支持 OpenAI 多模态格式，`content` 可以是内容片段数组：

```python
import base64, httpx

img = base64.b64encode(open("photo.png", "rb").read()).decode()
r = httpx.post(
    "http://127.0.0.1:8000/v1/chat/completions",
    json={
        "model": "yuanbao-hunyuan-instant",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "这张图是什么颜色？"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{img}"}},
            ],
        }],
    },
)
```

也支持 `{"type": "file", "file": {"filename": "a.pdf", "file_data": "<base64>"}}`
和 `http(s)://` 远程地址（服务端下载后上传，可用 `attach.download_remote_urls = false` 关闭）。
DeepSeek 与元宝均支持图片/文档附件；元宝图片附件可直接被 Hy4/Hy3 识别。

约束见 `config.toml` 的 `[attach]`：单文件大小上限、扩展名白名单、远程下载上限。

## 生图

```bash
curl http://127.0.0.1:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"yuanbao-image","prompt":"一只戴红围巾的猫，水彩风格","n":1}'
```

- 元宝每次网页生成 4 张图；`n` 取 1–4，默认 4。
- 网关会悬停图片并点击右下角下载图标，获取无水印原图（需在元宝设置中开启
  “无水印保存”），保存到 `data/generated/`，响应返回本地 URL：
  `http://127.0.0.1:8000/v1/files/<name>`。
- `response_format` 可选 `url`（默认）或 `b64_json`。
- 也可以用 `/v1/chat/completions` 调 `yuanbao-image`，返回 4 个 markdown 图片链接。

## PPT 生成

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"yuanbao-ppt","messages":[{"role":"user","content":"制作一份关于城市夜跑的 3 页 PPT，简洁商务风格"}]}'
```

模型返回 PPT 内容摘要 + 一个本地 `.pptx` 下载链接：

```markdown
[城市夜跑_PPT.pptx](http://127.0.0.1:8000/v1/files/<name>)
```

PPT 由元宝 Presentation Maker 生成并自动下载原文件；生成可能耗时数分钟。
文件同样保存在 `data/generated/`。

## 并发

每个 provider 驱动自己的**单个浏览器窗口**，因此每个 provider 内部所有请求进入一个
**FIFO 队列串行执行**（队列长度上限见 `worker.max_queue_len`，超出返回 429）。
DeepSeek 与元宝使用不同浏览器，跨 provider 可以并行。
队列状态可在 `/admin/status` 的 `providers.<name>.queue` 查看。

每个需要浏览器的请求在执行前都会检查对应 provider 的 Camoufox 是否存活且可响应。
窗口被关闭、进程崩溃或页面无响应时，网关会自动重启该 provider 的浏览器，
登录态从持久化 profile 恢复，无需重启整个网关。

## 会话 ID

响应会同时在**响应头**和**响应体**里返回 `session_id`（如 `conv_8fa2c1`）。
续接时仍建议回传完整 `messages`，并只追加**一条**新的 user 消息；也可以只保留会话首条
user 消息 + 新消息。只发送一条全新的“继续”会被判定为历史分叉并返回 400。

```python
client.chat.completions.create(
    model="yuanbao-hunyuan",
    messages=[
        {"role": "user", "content": "会话的首条用户消息"},
        {"role": "assistant", "content": "历史回复（可裁剪/改写，不参与匹配）"},
        {"role": "user", "content": "继续"},
    ],
    extra_headers={"X-Session-Id": "conv_8fa2c1"},
)
```

不带会话 ID 时，网关会按**用户轮次**在同一 provider + mode 内自动匹配已有会话。
显式传入的 `session_id` 必须属于同一 provider；同一 provider 内可以切换模型/模式。
完整 diff 规则见 [docs/API.md](docs/API.md#9-会话机制)。

## 命令

| 命令 | 作用 |
|---|---|
| `freeaiproxy start` | 启动网关（同时启动 DeepSeek / 元宝） |
| `freeaiproxy status --provider all` | 检测登录态（`deepseek` / `yuanbao` / `all`） |
| `freeaiproxy relogin --provider yuanbao` | 触发重新登录 |
| `freeaiproxy profile import --browser firefox --provider yuanbao` | 从 Firefox 导入元宝登录态 |
| `freeaiproxy profile sync --browser firefox --provider deepseek` | 覆盖式同步登录态 |
| `freeaiproxy profile export --provider yuanbao --out yuanbao.zip` | 导出登录态 profile |

导入 / 同步只支持 **Firefox 系**（Chromium 内核的 cookie 加密不兼容）。
若 Firefox 正在运行，命令会提示先关闭它再继续。

## 进程管理脚本

`scripts/manage.py` 用于后台启动/停止/重启/构建，并额外提供状态检查：

```bash
uv run python scripts/manage.py start      # 后台启动，等待 /healthz
uv run python scripts/manage.py status     # 查看 PID、健康状态、日志位置
uv run python scripts/manage.py stop       # 停止本脚本启动的服务及其浏览器
uv run python scripts/manage.py restart
uv run python scripts/manage.py build      # uv build，产物在 dist/
```

- PID 文件：`data/freeaiproxy.pid`
- 运行日志：`data/freeaiproxy.log`
- `start` 会以后台进程启动并等待最多 30 秒；可用 `--wait 90` 调整。
- `stop` 只停止有 PID 记录且进程信息匹配的服务；如果是手动启动的实例，脚本会提示但不强杀。
- 停止时会一并清理该进程树下的 Camoufox 浏览器。
- 自定义配置：`--config path/to/config.toml`。

## 端点

| 端点 | 说明 |
|---|---|
| `POST /v1/chat/completions` | 对话（支持 `stream: true`） |
| `POST /v1/images/generations` | 元宝 AI 生图 |
| `GET /v1/models` | 模型列表（两个 provider 合并） |
| `GET /v1/files/{name}` | 下载生成的图片 / PPTX |
| `GET /healthz` | 服务与各 provider 登录态 |
| `GET /admin/status` | 诊断信息（含各 provider 队列） |
| `POST /admin/relogin?provider=yuanbao` | 触发指定 provider 重新登录 |

## 配置

```toml
[providers.deepseek]
enabled = true
profile = "data/profile"

[providers.yuanbao]
enabled = true
profile = "data/profiles/yuanbao"
```

- `[profile] path` 仍作为 DeepSeek 的后备 profile 路径。
- 浏览器模式：`debug.headed = true` 强制有头，`false` 强制无头；不写则由
  `worker.headless` 决定。无头模式下遇到未登录会临时打开有头窗口供登录，
  登录成功后自动切回无头。
- 环境变量覆盖：`FREEAIPROXY__PROVIDERS__YUANBAO__ENABLED=false`，
  规则是 `FREEAIPROXY__SECTION__KEY`，优先级环境变量 > config.toml > 默认值。
- `data/generated/` 保存生图与 PPT 文件，不会自动清理。

## 开发

```bash
uv run pytest                 # 单元测试（离线，不启动浏览器）
uv run ruff check .           # lint
uv run mypy                   # 类型检查
uv run python poc/yuanbao_calibrate.py   # 元宝登录/首页标定
```

`scripts/e2e_check.py`、`scripts/m2_check.py` 等端到端脚本需要服务已启动且浏览器已登录。

## 注意事项

- 自动化操作 DeepSeek / 元宝网页版可能违反其服务条款并有封号风险，**请使用非主力账号**。
- 本项目仅供个人技术研究，不得转售或对外提供服务。
- 元宝 Expert 模式不提供显式 Search 开关；需要强制搜索请用 `yuanbao-hunyuan-search`。
- 元宝搜索答案暂不包含来源 URL（页面来源面板未抽取），但答案正文包含来源名称。
- 思考过程（Thinking / Expert）默认不外泄，只返回最终答案。
- PPT 生成可能耗时数分钟；请给客户端设置足够的超时。
- 元宝当前 UI 为英文（账号/浏览器语言决定），文案选择器按英文标定。
