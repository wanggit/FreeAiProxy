# FreeAiProxy 设计文档

> 多 Provider 架构与腾讯元宝适配见 [YUANBAO.md](YUANBAO.md)。本文档主要描述 DeepSeek
> 网页版（M1–M3）的设计与标定结论。

- 版本：v0.1（2026-10-21）
- 状态：设计定稿，待实施
- 里程碑：M1（本文约定的第一期范围）为当前实施目标

---

## 目录

1. [项目定位](#1-项目定位)
2. [总体架构](#2-总体架构)
3. [技术选型](#3-技术选型)
4. [浏览器引擎层设计](#4-浏览器引擎层设计)
5. [会话管理设计](#5-会话管理设计)
6. [登录态管理设计](#6-登录态管理设计)
7. [HTTP API 规范](#7-http-api-规范)
8. [CLI 设计](#8-cli-设计)
9. [配置与数据目录](#9-配置与数据目录)
10. [项目目录结构](#10-项目目录结构)
11. [里程碑计划](#11-里程碑计划)
12. [测试与验收](#12-测试与验收)
13. [风险与对策](#13-风险与对策)
14. [附录](#14-附录)

---

## 1. 项目定位

### 1.1 一句话定义

FreeAiProxy 是一个**单机部署的网页转 API 网关**：用反检测浏览器（Camoufox）驱动
DeepSeek 网页版（https://chat.deepseek.com/），并对外暴露 **OpenAI Chat Completions
兼容 API**，使任意支持 OpenAI 协议的客户端无需改动即可使用 DeepSeek 网页版（免费额度）。

### 1.2 目标

| 编号 | 目标 |
|---|---|
| G1 | 提供 OpenAI 兼容的 `POST /v1/chat/completions`（含流式 SSE） |
| G2 | 登录一次，长期复用（持久化浏览器 profile） |
| G3 | 会话连续：多轮对话在 DeepSeek 网页的同一对话中展开，不重复灌历史 |
| G4 | 从 Firefox 导入/同步已有登录态，支持迁移到其他机器 |
| G5 | 无登录态时自动弹出有头浏览器引导用户人工登录 |
| G6 | 调试阶段浏览器始终有头、全程可见，可观察每一步操作 |
| G7 | 降低被风控探测概率（Camoufox 指纹伪装 + 有头模式 + 拟人化输入） |
| G8 | 单账号、单浏览器实例，API 请求串行排队 |

### 1.3 非目标（本期明确不做）

| 编号 | 非目标 | 备注 |
|---|---|---|
| N1 | ~~附件上传~~ | **M3 已实现**，见 7.6 |
| N2 | ~~`deepseek-reasoner`（DeepThink）深度思考模式~~ | **M2 已实现**，见 7.7 |
| N3 | 思考过程（reasoning content）透出 | 网页版思考块为内部实现，**M2 决定不外泄**：思考块与答案是兄弟节点，提取时天然分离 |
| N4 | function calling / tools | 网页版无对应能力，永久忽略相关字段 |
| N5 | 破解登录验证码 / 绕过邮箱验证 | 人工登录一次，不自动化登录本身 |

### 1.4 风险声明（必须知情）

自动化操作学习型收益假设：本项目自动化的是用户本人已登录的 DeepSeek 账号的网页交互，
类似"个人浏览器使用"。但仍需注意：

- 自动化操作可能违反 DeepSeek 服务条款，存在**封号风险**，请使用非主力账号；
- 免费网页版有次数 / 速率限制，网关不放大请求，但需感知其存在；
- 本项目仅供个人技术研究，不得转售或对外提供服务。

---

## 2. 总体架构

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        调用方（OpenAI 客户端）                     │
│       openai-sdk / curl / LobeChat / ChatBox / 任意 HTTP          │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP (127.0.0.1:8000)
┌──────────────────────────────▼──────────────────────────────────┐
│                    FastAPI 服务层（uvicorn）                      │
│                                                                  │
│  /v1/chat/completions   会话解析 → 历史 diff → 入队 → 驱动浏览器   │
│  /v1/models             静态模型列表                              │
│  /healthz               服务健康                                 │
│  /admin/*               登录态查询 / 重新登录触发                 │
│                                                                  │
│  ┌──────────────┐   ┌───────────────┐   ┌────────────────────┐  │
│  │ 会话存储      │   │ Worker 队列    │   │ BrowserManager      │  │
│  │ SQLite       │   │ 全局串行锁     │──▶│ Camoufox 持久 profile│  │
│  │ 会话映射/消息 │   │ (asyncio)     │   │ 单浏览器实例常驻      │  │
│  └──────────────┘   └───────────────┘   └─────────┬──────────┘  │
└──────────────────────────────────────────────────┼──────────────┘
                                                   │ Playwright
                                     ┌─────────────▼─────────────┐
                                     │  chat.deepseek.com         │
                                     │  （云端保存所有对话历史）    │
                                     └───────────────────────────┘
```

### 2.2 关键认知：有状态 vs 无状态

**OpenAI API 是无状态的**（客户端每轮回传全部 messages 历史），**DeepSeek 网页是有状态的**
（每个对话页面自含历史，云端保存）。若把客户端的完整历史每轮灌进网页，页面会越来越长、
越来越慢，且大量重复。

因此核心设计是：**服务端维护一张映射表，本地 diff 出"新增的那条消息"，只把增量发给网页**。
DeepSeek 网页是真实的"历史存储"，我们本地只存 diff 与映射所需的摘要。

### 2.3 并发与队列模型（单账号 MVP）

- **全局锁**：整个浏览器一次只能浏览一个页面、执行一个操作序列。所有驱动操作
  （导航、发送、抓取）被一个 `asyncio.Lock` 串行化。
- **同会话串行 + 跨会话串行**：MVP 不区分，全局排队即可；排队上限 `worker.max_queue_len`，
  超过则返回 OpenAI 错误格式（HTTP 429）。
- **客户端提前断开**：若流式响应中客户端断开，worker 继续**完成**回复抓取并入库
  （保证下次 diff 正确），仅停止向客户端转发。

---

### 2.4 请求串行化队列（`services/queue.py`）

单一浏览器窗口同一时刻只能处理一个请求。原实现用 `asyncio.Lock` 互斥，但
**锁不保证公平**：等待者被唤醒的顺序是任意的，一个快速重试的客户端可能插队到
等待更久的请求前面。改为显式的 FIFO 队列：

| 特性 | 说明 |
|---|---|
| 严格 FIFO | 请求按到达顺序依次占用浏览器，先来先服务 |
| 队列长度上限 | `worker.max_queue_len`（默认 20），超出立即返回 429 `queue_full` |
| 可观测 | `/admin/status` 暴露 `waiting` / `running` / `processed` / `max_wait_ms` |
| 取消安全 | 等待中的请求被取消时不会占住队列名额（`test_cancelled_waiter_does_not_block_the_queue`） |
| 覆盖流式 | 流式请求在整个 SSE 生命周期内持有队列名额，客户端断开后仍完成收尾并释放 |

排队等待超过 1 秒的请求会记一条 info 日志，便于观察拥塞。

## 3. 技术选型

| 组件 | 选型 | 版本约束 | 说明 |
|---|---|---|---|
| 运行时 | Python | >= 3.11 | uv 管理 |
| Web 框架 | FastAPI + uvicorn[standard] | 最新稳定 | 异步、SSE 原生支持 |
| 反检测浏览器 | Camoufox | `pip install camoufox[geoip]`，2026 年仍在活跃维护 | Firefox 深度改造，指纹注入 |
| 浏览器驱动 | Playwright（经 camoufox 包装） | camoufox 自带依赖 | `AsyncCamoufox` 异步 API |
| 存储 | SQLite（标准库 sqlite3，经 aiosqlite） | 内置 | 免运维，够用 |
| CLI | typer（+ rich） | 最新稳定 | 子命令、彩色输出 |
| 进程检测 | psutil | 最新稳定 | 检测 Firefox 是否在运行 |
| 配置 | pydantic-settings + TOML | 最新稳定 | 文件 + 环境变量覆盖 |
| 测试 | pytest + pytest-asyncio | 最新稳定 | 单测为主，集成手工 |

安装一次性步骤：

```bash
uv sync
uv run camoufox fetch        # 下载 Camoufox 浏览器二进制，约 1GB
```

---

## 4. 浏览器引擎层设计

### 4.1 Camoufox 启动参数

```python
from camoufox.async_api import AsyncCamoufox

context = await AsyncCamoufox(
    persistent_context=True,
    user_data_dir=str(config.profile_path),   # ./data/profile
    headless=<有效模式>,                        # 默认模式；登录时临时切为 False（有头）
    humanize=True,                             # 拟人化鼠标移动
    os="windows",                              # 指纹跟随宿主操作系统
    i18n=False,                                # 保持环境默认语言，避免与宿主 IP 不符
)
page = context.pages[0]
```

- **持久 profile**：`data/profile/` 即登录态本体，关闭进程后 cookies/registers 保留。
- **指纹一致性**：启动参数固定（os 固定 windows、持久 profile 固定），不要频繁变更
  指纹参数——同一账号反复更换新指纹反而是风控信号。
- 禁止在调试中临时手动操作该浏览器实例时安装插件、清缓存等行为（会破坏 profile 一致性）。

### 4.2 运行模式

| 模式 | 条件 | 行为 |
|---|---|---|
| 有头模式 | `debug.headed = true`，或未设置且 `worker.headless = false` | 有头浏览器；可配置 `slowmo`（每步延迟 ms）；开启 `trace` 时录制 Playwright trace 到 `data/traces/` |
| 无头模式 | `debug.headed = false`，或未设置且 `worker.headless = true` | 无头运行；遇到未登录时临时切到有头完成登录，登录成功后自动切回 |

调试模式的目标是"让用户看见每一步"，是当前阶段的硬约束（G6）。

### 4.3 页面驱动 操作规范

所有 DeepSeek 页面交互集中在 `engine/ds_driver.py`（页面驱动层），选择器集中在
`engine/selectors.py`（集中的原因见附录 A）。

#### 4.3.1 页面定位

| 操作 | 方式 |
|---|---|
| 前往既有会话 | `page.goto("https://chat.deepseek.com/a/chat/s/<chat_id>")`，等待输入框可见 |
| 新开会话 | `page.goto("https://chat.deepseek.com/a/chat")`（根路径即新对话） |
| 会话绑定识别 | 首条消息发送后，从 `page.url` 匹配 `#?id=` 参数提取 chat_id（以 PoC 实测为准，见 4.4），存映射表 |

#### 4.3.2 消息输入与发送

输入框是 React + 富文本组件（疑似 ProseMirror），**不能采用"设置 value"金融式赋值**，
统一走键盘路径：

```python
input_box = page.locator(SELECTOR_input_box)     # 集中在 selectors.py
input_box.click()
input_box.fill("")                                # 清掉残留草稿（若有）
# 多行文本走剪贴板粘贴，保换行：
await page.evaluate("navigator.clipboard.writeText(text)")   # 需申请剪贴板权限
await page.keyboard.press("Control+V")
await page.keyboard.press("Enter")               # 发送
```

- 发送后立即记录"发送前消息列表结尾指纹"，用于之后定位"最新一条 assistant 消息"。

#### 4.3.3 回复结束判定（关键难点）

回复是流式渲染的，没有"完成"事件。判定算法（M1 定稿，PoC 验证）：

```
初始：等 DOM 出现停止按钮（发送按钮变为停止形态），开始采样
循环（间隔 worker.poll_interval，默认 500ms）：
    两重信号双保险：
      ① 按钮态信号：发送按钮已从"停止"形态回到"发送"形态
      ② 文本稳定信号：最近两次采样（间隔≥1 个 poll）的最新回复文本完全一致
    若 ①与②同时满足 → 判定完成
    若累计等待 > worker.timeout_first_token 且从未出现任何回复文本 → 超时
    若最后一次文本变化后累计 > worker.timeout_completion → 超时（取已有文本返回）
```

超时处理：**返回已捕获的部分文本**（finish_reason = "stop"）并记录 warning 日志。
宁可返回部分结果，不返回 504——因为流式采样已经有内容在手，调用方重试反而丢上下文。

#### 4.3.4 回复内容提取

1. 定位"本轮最后一条用户消息"节点（发送前指纹定位）；
2. 取其之后的全部 markdown 块（同一条回复可能被拆成多个相邻块）拼接为最终文本；
3. 若最后的回复没有 markdown 块（短回复/异常），退化为拼接纯文本节点；
4. 非新版辅助：深度思考模式下页面会出现思考区块，**不将其计入回复文本**（M1）。

#### 4.3.5 错误分类（驱动层抛出）

| 错误 | 触发条件 | 网关层映射（HTTP / OpenAI error type） |
|---|---|---|
| NotLoggedIn | 页面跳转到登录态 / 登录弹窗出现 | 502 / `authentication_required`，并触发登录引导 |
| UpStreamTimeout | 4.3.3 超时（无任何输出） | 200 + finish_reason=stop（含部分内容或空）+ warning 日志 |
| SelectorMismatch | 关键选择器找不到 | 500 / `upstream_page_changed` |
| ChatNotFound | 目标 chat_id 页面加载后无法定位会话 | 404 / `session_invalid`，映射表标记该会话 dead |
| RateLimitedDetected | 页面出现限流提示 | 429 / `upstream_rate_limited` |

### 4.4 PoC 验证结果（2026-10-21 实测，全部通过）

> 实测脚本：`poc/poc_page.py`（复用生产驱动代码）；原始采样：`poc/dump/calibration.json`。

| # | 验证点 | 实测结论 |
|---|---|---|
| P1 | 输入框定位 | `<textarea placeholder="Message DeepSeek">`，可用 `insert_text` 输入多行（含 `\n` 正常换行） |
| P2 | 用户消息落库 | 用户气泡 = `.ds-message` 内含 `.ds-collapsible-text`；发送后气泡数 +1 |
| P3 | 生成中信号 | 主按钮 class：**生成中** = 含 `_52c986b` 且**不含** `bd74640a`；**空闲** = 含 `_52c986b bd74640a`（空输入时另有 `ds-button--disabled`） |
| P4 | chat_id 提取 | 发送后 URL 变为 `https://chat.deepseek.com/a/chat/s/<uuid>`，正则 `/a/chat/s/([0-9a-zA-Z_-]+)` 可提取 |
| P5 | 短回复 | 2.2s 内完成，正文 "收到" |
| P6 | 长回复 | 600 字散文 9.8s 完成，正文 908 字符，采样期间长度单调增长 |
| P7 | 新对话 | `https://chat.deepseek.com/a/chat/` 打开后用户消息数为 0 |
| P8 | 登录判定 | 登录时 composer 可见且无登录文案；未登录时出现登录面板文案 |

### 4.5 两个关键实现事实（与初版设计的差异，务必保留）

**① 消息列表是虚拟滚动的（`ds-virtual-list`）**

助手回复在流式生成时，**DOM 节点数量不增长，只有节点内的文本在变**。因此：

- ❌ 不能用"记录发送前的 `.ds-markdown` 数量，再取新增节点"的方式提取回复；
- ✅ 必须用 **锚点法**：以"最后一条用户消息"为锚，取其**文档顺序之后**的所有
  `.ds-assistant-message-main-content` 节点作为本轮回复。

**② 回复文本不能与基线做全等比较**

若新一轮回复文本恰好与上一轮完全相同（例如两轮都回答"小明"），"文本变化"永远不会触发，
请求会一直等到超时并返回空回复。锚点法天然规避此问题：回复位置由 DOM 顺序决定，
与文本内容无关。

**③ 结束判定采用双保险**

- 信号 A：文本连续 2 个采样周期无变化（默认 500ms/次）；
- 信号 B：主按钮回到空闲态（class 同时含 `_52c986b` 与 `bd74640a`）；
- 两者满足其一即结束；另加"文本稳定 8 个周期"的硬兜底，防止按钮态被改版打乱时卡死。

### 4.6 profile 损坏的自动修复

强制结束浏览器进程后，profile 会残留 `parent.lock` / `sessionCheckpoints.json` /
`sessionstore-backups` 等运行期状态，导致浏览器**启动即退出**（exitCode=0）。
处理策略（`engine/browser_manager.py`）：

1. 每次启动前清理上述运行期残留（不动 cookies / storage）；
2. 若仍启动失败，判定 profile 损坏 → **以最小 profile 重建**（只保留
   `cookies.sqlite`、`prefs.js`、`times.json`、`compatibility.ini`、`storage/`），
   登录态不丢失，随后重试一次。



---

## 5. 会话管理设计

### 5.1 术语

| 术语 | 含义 |
|---|---|
| `conv_id` | 本网关自己的会话 ID，格式 `conv_<12位随机hex>`，对外返回给调用方 |
| `chat_id` | DeepSeek 网页侧会话的 ID，形如 `https://chat.deepseek.com/a/chat/s/<chat_id>` 中的部分 |
| 映射 | 一个 `conv_id` ↔ 一个 `chat_id`，一一对应，**不可逆且无共享**（安全 & diff 正确性约束） |

### 5.2 存储结构（SQLite）

```sql
CREATE TABLE conversations (
    conv_id       TEXT PRIMARY KEY,        -- conv_xxx
    chat_id       TEXT,                    -- DeepSeek 侧 id，新建会话首条发送成功后填写
    status        TEXT NOT NULL,           -- active / dead / not_started
    created_at    REAL NOT NULL,
    last_used_at  REAL NOT NULL
);

CREATE TABLE history_msgs (
    conv_id       TEXT NOT NULL,
    seq           INTEGER NOT NULL,        -- 0,1,2,... 按发送顺序
    role          TEXT NOT NULL,           -- user / assistant
    content       TEXT NOT NULL,           -- 全文（用于 diff 与重复请求兜底）
    PRIMARY KEY (conv_id, seq),
    FOREIGN KEY (conv_id) REFERENCES conversations(conv_id)
);
```

- **只存**我们在本会话中经 diff 确认"应当属于该 DeepSeek 对话"的消息：
  我们发出的用户消息 + 网页返回的 assistant 回复。
- system 消息见 5.4 特殊规则。
- 容量预估：每轮对话约 2 条记录，文本级存储，个人使用一年可忽略不计。

### 5.3 会话 ID 传入通路（三条，按优先级）

**调用方如何告诉网关"这次请求属于哪个会话"：**

| 优先级 | 通路 | 说明 |
|---|---|---|
| 1 | 请求头 `X-Session-Id: conv_abc123xyz` | 标准通路，任何 HTTP 客户端都能传 |
| 2 | 请求体顶层字段 `session_id`（OpenAI SDK 经 `extra_body={"session_id": ...}`） | 与 1 等价，共同支持 |
| 3 | 历史前缀自动匹配（Auto-match） | 调用方完全未传：把传入的 messages 与各会话已存 history_msgs 做最长精确前缀匹配，**完整匹配**才自动绑定；匹配不到视作新会话 |

原则：
- 通路 1/2 显式指定 → 显式优先；conv_id 不存在且历史不匹配 → 报 `session_invalid`（404）。
  （长推历史与显式指定矛盾时，显式必胜——满足少量"用户模板+新内容"特定诉求需用 auto-match
  由调用者显式不带 ID 自行触发。）
- Auto-match 保守执行：仅**完整匹配**（除非最后一条差半条）即"整个 messages 数组 =
  已存历史 + 追加一条 user 消息"这种最常见的"回传"模式才算。
- 返回侧（这属于"给"而不是"接"）：
  - 非流式响应：**响应头永远带** `X-Session-Id`；响应体顶层额外加 `"session_id"` 字段；
  - 流式响应：响应头带；首个 SSE chunk 顶层额外带 `"session_id"` 字段（OpenAI SDK 会忽略
    未知顶层字段，兼容性安全）。

### 5.4 历史 diff 算法（实现版：按用户轮次对齐）

> **实现调整（2026-10-21）**：初版设计按"整条 messages 数组做严格前缀匹配"，
> 实测发现两个问题并已修正：
> 1. 客户端回传的 assistant 文本可能与本地记录不一致（裁剪、改写、流式拼接差异），
>    严格匹配会误报 400 冲突；
> 2. 客户端可能省略中间轮次（只发最近几轮），严格匹配会判定为"历史分叉"。
>
> 修正后的规则：**对齐只看用户轮次**，assistant 文本永不参与比较。
> 理由：真实会话由 DeepSeek 网页持有（它是唯一权威），客户端的 assistant 回显不可信。

```
def align_user_messages(history, incoming):
    """返回 (已对齐的用户轮次数, 尚未出现过的新用户消息列表)"""
    hist_users = [m for m in history if m.role == "user"]
    inc_users  = [m for m in incoming if m.role == "user"]
    k = 0
    while k < len(hist_users) and k < len(inc_users) and hist_users[k] == inc_users[k]:
        k += 1
    return k, inc_users[k:]
```

**显式指定会话（X-Session-Id / session_id）**

| 情形 | 处理 |
|---|---|
| 会话不存在 / 已标记 dead | 404 `session_invalid` |
| `aligned == 0`（首个用户消息都对不上） | 400 `history_conflict` |
| 无新增用户消息（客户端回传的历史已全部覆盖） | 回放该用户轮次对应的缓存回复（`repeat`） |
| 恰好 1 条新增用户消息 | 发送该消息（`send`） |
| ≥2 条新增用户消息 | 400 `history_conflict` |

**未指定会话（自动匹配）**：遍历所有活跃会话，按 `aligned` 降序择优：
- 存在"可发送"候选 → 取 `aligned` 最大的那个（`aligned` 相同则优先无副作用的 `repeat`）；
- 都不匹配 → 新建会话。

**系统消息**：仅在**新建会话**时，把开头的 system 内容并入首条用户消息发送；
已有会话中 system 一律忽略。

**Diff 边界情况处理**：

| 情形 | 处理 |
|---|---|
| 客户端回传的 assistant 文本与本地不同 | 忽略，不报错（对齐只看用户轮次） |
| 客户端省略了中间轮次 | 对齐到最后一个匹配的用户轮次，正常续接 |
| 客户端只回传最后一条用户消息 | `aligned=1`，无新增 → 回放该轮缓存回复 |
| 首个用户消息不同（换了话题却沿用 session id） | 400 `history_conflict` |
| 一次请求里塞了多条新用户消息 | 400 `history_conflict` |
| 传入为空 messages | HTTP 400 |



### 5.5 流式响应中的增量转发

完整流语义：**PDF 的增量**——本地不真正流式调用，而是**轮询拖拽** DOM 上生成的文本，
与上次快照对比出增量，封成 SSE chunk 发出：

```
收到 worker 启动 → 立刻发首个 chunk（含 role=assistant + "session_id" 顶层字段）
轮询循环每次：新文本 = 截至当前捕获的全文，delta = 新文本 - 上次快照
    → 若 delta 非空 → 发 chunk {"choices":[{"delta":{"content": delta}}]}
完成 → 发 chunk {"choices":[{"delta":{},"finish_reason":"stop"}]} → data: [DONE]
```

- 周期同 `worker.poll_interval`（500ms），新增量按轮次微小截断可以保证顺滑；
- 客户端断开不影响 worker 继续跑完（见 2.3）。

---

## 6. 登录态管理设计

### 6.1 术语与存储

- **Profile**：`data/profile/` 目录，即 Camoufox 的完整浏览器身份（cookies、localStorage
  fingerprint 都在里面）。**整个目录即登录态**，拷贝即迁移。

### 6.2 启动检查与登录引导（核心流程，M1 必含）

```
freeaiproxy start
  │
  ├─ 配置加载 ✓ → 启动 BrowserManager
  │      │
  │      ├─ data/profile 存在？
  │      │     ├─ 否 → 走 ②「登录引导」
  │      │     └─ 是 → goto chat.deepseek.com
  │      │            └─ 检测登录态：
  │      │                 ├─ 已登录（头像/用户菜单元素出现）→ 正常启动 /healthz 变 green
  │      │                 └─ 触发登录版画面 or 登录弹窗 → 走 ②
  │      │
  │      ├─ ② 登录引导：
  │      │     - 登录阶段强制使用有头浏览器（无头配置下临时切换）
  │      │     - 控制台打印：请在弹出的浏览器中完成登录（手机号/邮箱/验证码）
  │      │     - 每隔 2s 轮询登录态，成功（用户菜单出现）即继续
  │      │     - 若 /admin/relogin 触发（线上模式），HTTP 该接口挂起并等待 UI 完成
  │      └─ 未登录期间 /v1/chat/completions 全部返回 502 authentication_required
```

- `POST /admin/relogin`：线上进程不必杀掉即可触发引导窗口，特别针对**cookie 过期**
  （DeepSeek 会话有有效期，过期后所有调用报 NotLoggedIn）供应链；调试中用 CLI。

### 6.3 Firefox 登录态导入 / 同步（M1 必含）

> **仅支持 Firefox 系**（Firefox / Waterfox 等 Firefox 内核浏览器）。Chrome/Edge 是
> Chromium 内核 + DPAPI/App-Bound 加密，**拷贝到 Camoufox 毫无意义且无法解密 cookie**，
> 明确不支持，执行时给出明确解释。

```
freeaiproxy profile import --browser firefox     # 自动定位
freeaiproxy profile import --path <目录>          # 手动指定
freeaiproxy profile sync   --browser firefox     # 覆盖式重新同步
freeaiproxy profile export --out <zip路径>        # 打包当前 data/profile 供迁移
```

#### import / sync 共同流程

```
① 定位源 profile：
   --browser firefox：
     Windows:  %APPDATA%\Mozilla\Firefox\Profiles
     macOS:    ~/Library/Application Support/Firefox/Profiles
     Linux:    ~/.mozilla/firefox
     定位规则：解析 profiles.ini（Default 优先）→ 兜底取最近修改时间的 *.default* 目录
   --path：直接使用
     校验：目录内必须有 cookies.sqlite 或 places.sqlite，否则报"不是 Firefox profile"
② 检测 Firefox 是否在运行（psutil 扫 firefox.exe / firefox 进程）：
     在运行 → 打印："检测到 Firefox 正在运行（PID xxx）。请关闭 Firefox 后按回车重试（Ctrl+C 取消）"
     用户按回车 → 重新检测，直到通过
     探测原因：firefox 的 cookies.sqlite / places.sqlite 被 Firefox 进程锁，
     强拷可能得到损坏不一致的数据
③ 停用当前网关浏览器实例（若在跑）；
④ 清空 data/profile（import 初次导入时）；sync 时**覆盖**同名文件
   （import 与 sync 都是一次性全量覆盖，不做合并，理由：合并不可靠且易错）；
⑤ 重新启动浏览器 + 登录态检测，过程输出给用户；成功 → 打印"已导入登录态"
```

#### export（打包迁移）

排除大缓存目录减小体积，白名单内保留必需文件：

```
排除：cache2/、startCache/、shader-cache/、thumbnails/、crashes/、*.tmp
保留：cookies.sqlite、places.sqlite、storage/、prefs.js、xulstore.json、
      extensions/、handlers.json  （宁可多带，不缺关键）
```

### 6.4 Cookie 过期（运营期问题）

- 表现：ت某天起所有请求 502。诊断路径：`/admin/status` 返回 login=false；
  CLI `freeaiproxy status` 验证。
- 处理：`POST /admin/relogin`（或 SSH 上 CLI 交互）。网页 session 一般数周到数月，
  属于低频运维点，M1 做好引导="$(管理员)"能力即可。

---

## 7. HTTP API 规范

### 7.1 通用约定

- Base URL：`http://127.0.0.1:8000`（可配置）
- 鉴权：**可选**。配置 `server.api_key` 非空时，要求 `Authorization: Bearer <api_key>`；
  为空时关闭鉴权（本地个人用法）。
- 错误格式：统一 OpenAI 错误体：

```json
{
  "error": {
    "message": "<可读描述>",
    "type": "<error_type>",
    "param": null,
    "code": "<code 可空>"
  }
}
```

错误 type 对照：见 4.3.5 + 校验错误 `invalid_request_error`。

### 7.2 POST /v1/chat/completions

请求体 = OpenAI Chat Completions 标准字段，外加扩展字段 `"session_id"`。
**OpenAI 不支持的字段一律忽略**（temperature、max_tokens、top_p、frequency_penalty、
presence_penalty、stream_options、tools 等全部忽略，不做校验），只有 `model` 必须是网关
支持的值之一，`messages` 非空且最后一条为 user。

**请求示例**

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek-chat", "messages": [{"role": "user", "content": "你好"}]}'
```

**非流式响应示例**（附加字段已高亮→注释说明）

```json
{
  "id": "chatcmpl-8fa2c1",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "deepseek-chat",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "你好……"},
      "finish_reason": "stop"
    }
  ],
  "session_id": "conv_8fa2c1"    // ── 扩展字段，非 OpenAI 标准；同值也会出现在响应头 X-Session-Id
}
```

**流式响应示例**（SSE）

```
data: {"id":"chatcmpl-8fa2c1","object":"chat.completion.chunk","model":"deepseek-chat",
       "session_id":"conv_8fa2c1","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-8fa2c1","object":"chat.completion.chunk","model":"deepseek-chat",
       "choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}

data: {"id":"chatcmpl-8fa2c1","object":"chat.completion.chunk","model":"deepseek-chat",
       "choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

**OpenAI 兼容性对照**：

| OpenAI 字段 | 网关行为 |
|---|---|
| `model` | 必须 `deepseek-chat`（M1）；`deepseek-reasoner` M2 前返回 400 并说明 |
| `messages` | 直 见 5.4 diff |
| `stream` | 支持，SSE 输出（5.5） |
| `temperature` / `top_p` / `max_tokens` / `stop` / `n` | **忽略**（网页版无参数滴概念） |
| `tools` / `tool_choice` / `response_format` | **忽略**（不支持，永不生效） |
| `user` 字段 | 仅记录日志，无用于区分不同调用方 |
| `stream_options.include_usage` | 忽略，不返回 usage |

### 7.3 GET /v1/models

```json
{
  "object": "list",
  "data": [
    {"id": "deepseek-chat", "object": "model", "created": 0, "owned_by": "freeaiproxy"}
  ]
}
```

M2 起追加 `deepseek-reasoner`。

### 7.4 GET /healthz

```json
{"status": "ok", "logged_in": true, "queue_len": 0}
```

### 7.5 Admin 通道

| 端点 | 说明 |
|---|---|
| `GET /admin/status` | 登录态、当前会话数、最近错误（供诊断） |
| `POST /admin/relogin` | 触发登录引导窗口（浏览器若在跑先重置为引导态） |

admin 通道**不做**独立鉴权（本期），与 `/v1` 用同一 api_key 校验。防止公网时暴露，
绑定默认的 `127.0.0.1`。

### 7.6 附件支持（`services/attachments.py`）

**请求格式**（与 OpenAI 多模态一致）：

```json
{
  "model": "deepseek-chat",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "这张图是什么颜色？"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0K..."}}
    ]
  }]
}
```

也支持 `{"type": "file", "file": {"filename": "a.pdf", "file_data": "<base64>"}}`，
以及 `http(s)://` 远程地址（服务端下载后上传，可用 `attach.download_remote_urls` 关闭）。

**DOM 事实**（实测 2026-10-21，见 `poc/dump/upload_*.html`）：

| 事实 | 值 |
|---|---|
| 文件输入 | 输入框旁一个**隐藏**的 `<input type="file" multiple>`，`accept` 带完整扩展名白名单 |
| 上传入口 | 同一个隐藏 input，因此用 `set_input_files` 直接投喂，**无需驱动系统文件选择器** |
| 图片呈现 | `<img alt="原始文件名" src="blob:...">`（blob 预览） |
| 文档呈现 | 文件卡片，文本包含文件名与大小（如 `TXT 66B`） |
| 上传中 | 附件旁出现 `.ds-loading` 转圈 |

**实现要点**：

1. **附件跟随用户消息**：附件挂在 `Msg` 上，随消息进入会话层。diff 对齐时
   **只比较文本**，附件不参与比较，因此"同一段文字 + 不同附件"不会被误判成历史分叉。
2. **文件名必须保持原样**：网页端把文件名显示在预览与文件卡片上，
   若在磁盘上改名，页面上的名字就与判定用的名字对不上。因此每个附件放进
   以哈希命名的**独立子目录**，文件名本身保持不变（既不重名，也能在 DOM 中匹配）。
3. **上传完成判据**：等文件名的文本节点出现在 DOM 中（图片是 `img[alt]`，
   文档是文本节点），两种类型都满足；同时检测页面错误提示，超时 60s 报
   `upstream_page_changed`。
4. **先上传后发送**：附件挂载完成后再输入文字并回车，模型才能把图文一起理解。
5. **安全约束**：单文件大小上限、扩展名白名单、远程下载大小上限、
   严格 base64 校验、文件名去路径穿越、请求结束即清理临时目录。

**限制**：M3 只支持**单条消息内的附件**；不支持 `file_id` 复用（OpenAI 的文件 API），
也不支持多模态 assistant 消息。

| 端点 | 说明 |
|---|---|
| `GET /admin/status` | 登录态、当前会话数、最近错误（供诊断） |
| `POST /admin/relogin` | 触发登录引导窗口（浏览器若在跑先重置为引导态） |

admin 通道**不做**独立鉴权（本期），与 `/v1` 用同一 api_key 校验。防止公网时暴露，
绑定默认的 `127.0.0.1`。

---

### 7.7 `deepseek-reasoner`（深度思考开关）

DeepSeek 网页版的"深度思考"是输入框旁的一个开关按钮，官方 API 里对应
`deepseek-reasoner` 模型。网关把两者对应起来。

**DOM 事实**（实测 2026-10-21，见 `poc/dump/reasoner_calibration.json`）：

| 事实 | 值 |
|---|---|
| 开关元素 | `.ds-toggle-button`（输入框旁共 2 个，另一个是搜索） |
| 区分方式 | 开关的**父元素**文本包含 `DeepThink` |
| 开启状态 | `aria-pressed="true"` 且 class 含 `ds-toggle-button--selected` |
| 关闭状态 | `aria-pressed="false"` |
| 思考内容 | `.ds-think-content`（**与最终答案是兄弟节点**，不嵌套） |
| 最终答案 | `.ds-assistant-message-main-content` |

**实现要点**：

1. **每轮显式对齐**：发送前调用 `ensure_think_mode(enabled)`，把开关切到目标状态。
   不依赖"上一轮是什么模式"，因此同一个会话里可以逐轮切换模型
   （`reasoner → chat` 或反过来）而不会串味。开关状态在页面导航后保持，
   但显式对齐让行为不依赖网页端的状态保持策略。
2. **思考内容不外泄**：由于思考块与答案是兄弟节点，沿用既有的
   "锚点法 + 只取 `.ds-assistant-message-main-content`"提取逻辑，
   天然只拿到最终答案，不含思维链。
3. **思考时间算作活跃度**：推理模式下答案节点在思考结束后才出现。
   若只把"答案文本增长"算作活跃，一段长推理会误触发首字超时。
   因此 `.ds-think-content` 的长度变化同样刷新活跃时间戳，
   思考 3 分钟也不会被判超时。
4. **开关缺失时的降级**：普通模式找不到开关就按网页默认（关闭）处理；
   推理模式找不到开关则报 `upstream_page_changed`，不静默降级成普通回答。

**接口**：`model` 传 `deepseek-reasoner` 即可；`GET /v1/models` 同时返回两个模型。

## 8. CLI 设计

统一入口 `freeaiproxy`（typer）。

| 命令 | 作用 | 说明 |
|---|---|---|
| `freeaiproxy start` | 启动服务（Server 由进程内拉起 uvicorn + BrowserManager） | 标准入口；无登录态时会自动触发登录引导 |
| `freeaiproxy status` | 检测登录态（启动一个短生命周期浏览器去探测） | 轻量、不会占用长期实例 |
| `freeaiproxy relogin` | 等同 `POST /admin/relogin`（本地模式需服务未启动时） | 独立跑一个登录引导 |
| `freeaiproxy profile import` | 从 Firefox 导入登录态 | `--browser firefox` 或 `--path <dir>` |
| `freeaiproxy profile sync` | 从 Firefox 覆盖式重新同步 | 同上，但覆盖现有 data/profile |
| `freeaiproxy profile export` | 导出 data/profile zip 包 | `--out <zip>` |
| `freeaiproxy doctor` | 体检：camoufox 二进制、配置合法性、profile、可连通网页 | 快速定位环境问题（最后做） |

所有命令共用 `--config <path>`（默认 `./config.toml`）。

---

## 9. 配置与数据目录

### 9.1 config.toml（完整示例；文件不存在时有 sensible default）

```toml
[server]
host = "127.0.0.1"
port = 8000
api_key = ""                # 为空不鉴权

[debug]
headed = true               # true 强制有头；false 强制无头；不写由 worker.headless 决定
slowmo_ms = 0               # 每步 Playwright 操作固定延迟，调试时可设 200
trace = false               # 开启后录 Playwright trace 到 data/traces/

[worker]
headless = false            # 仅在 debug.headed 未设置时生效
poll_interval_ms = 500      # 回复采样周期
timeout_first_token_s = 120 # 从发送到首个回复 token 的上限
timeout_completion_s = 600  # 最后变化到完全停止的兜底上限
max_queue_len = 20          # 超过返回 429

[profile]
path = "data/profile"       # Camoufox 持久 profile 目录

[attach]
enabled = true             # 是否允许上传附件
max_size_mb = 20           # 单个附件大小上限
max_files = 5              # 单条消息最多附件数
allowed_extensions = [...] # 扩展名白名单
download_remote_urls = true # 是否允许下载 http(s) 附件
remote_timeout_s = 30
remote_max_bytes = 20971520
```

环境变量覆盖：`FREEAIPROXY__SERVER__PORT=9000`（dual underscore 分层）。**优先级
环境变量 > config.toml > 默认值**。

### 9.2 数据目录布局

```
data/
├── profile/          # Camoufox 持久 profile（登录态本体）
├── freeaiproxy.db    # SQLite：conversations / history_msgs
├── exports/          # profile export zipfile 默认输出
└── traces/           # Playwright trace 调试录像（debug.trace=true 时）
```

.gitignore 覆盖整个 `data/`。

---

## 10. 项目目录结构

```
FreeAiProxy/
├── pyproject.toml
├── uv.lock
├── config.toml                      # 用户本地配置（gitignore）
├── config.example.toml              # 上库的示例配置
├── docs/
│   └── DESIGN.md                    # 本文档
├── data/                            # 全部 gitignore
├── poc/
│   └── poc_page.py                  # 4.4 PoC 验证脚本（不进服务依赖链，可独立 `uv run python poc/poc_page.py`）
├── src/freeaiproxy/
│   ├── __init__.py
│   ├── config.py                    # pydantic-settings 加载与环境变量覆盖
│   ├── app.py                       # FastAPI 实例与路由挂载
│   ├── lifespan_dirs.py             # data 目录清理与初始化（挂到 FastAPI lifespan）
│   ├── db.py                        # aiosqlite 初始化 + 访问函数
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── browser_manager.py       # Camoufox 生命周期（启动/重启/销毁），登录引导状态机
│   │   ├── ds_driver.py             # DeepSeek 页面驱动（定位/发送/结束判定/抓取）—— 4.3
│   │   ├── selectors.py             # 集中选择器表（附录 A）
│   │   └── errors.py                # 引擎层错误类型
│   ├── services/
│   │   ├── __init__.py
│   │   ├── session_service.py       # conv_id 分配、映射、diff 与 auto-match —— 5
│   │   ├── chat_service.py          # 编排：请求 → 队列锁定 → 驱动 → 入库 → 返回
│   │   ├── queue.py                 # FIFO 请求队列 —— 2.4
│   │   └── attachments.py           # 附件解析/落盘/校验 —— 7.6
│   ├── api/
│   │   ├── deps.py                  # api_key 校验依赖
│   │   ├── v1_chat.py               # /v1/chat/completions（含流式）
│   │   ├── v1_models.py             # /v1/models
│   │   └── admin.py                 # /admin/status /admin/relogin + /healthz
│   └── cli.py                       # typer CLI —— 8
├── tests/
│   ├── test_diff.py                 # 5.4 diff 算法各分支（必测）
│   ├── test_session_match.py        # auto-match 前缀匹配
│   └── test_schema.py               # 请求/响应 OpenAI 兼容形状
└── scripts/
    └── (dev helpers)
```

---

## 11. 里程碑计划

### M1（本期）——跑通最小闭环 ✅ 已完成（2026-10-21）

1. 项目骨架：pyproject（uv）、config、cli 骨架、`freeaiproxy start` 起 FastAPI；✅
2. PoC：4.4 清单 8 项全部通过，产出 selectors.py（校准结论见 4.4–4.6）；✅
3. BrowserManager：启动/重启/登录引导、headed 强制、profile 损坏自动重建；✅
4. 会话层：conv_id 生成、SQLite 持久、按用户轮次对齐的 diff、auto-match（31 条单测）；✅
5. API：`/v1/chat/completions`（非流式 + 流式）+ `/v1/models` + `/healthz` + OpenAI 错误格式；✅
6. CLI：`start` / `status` / `relogin` / `profile import|sync|export`；✅
7. 验收：`scripts/e2e_check.py`（24 项）+ `scripts/sdk_check.py`（openai SDK 5 项）全绿；✅

**验收结果（2026-10-21）**：

- [x] `start` 起服务，无登录态时弹浏览器引导成功登录并常驻；
- [x] 连续多轮对话全部增量进入同一个 DeepSeek 对话，网页历史长度 = 发消息次数；
- [x] 流式客户端逐字看到内容增长，`[DONE]` 正常收尾；
- [x] 同一 `X-Session-Id` 换进程再请求也能续上（会话映射持久化于 SQLite）；
- [x] `profile import --browser firefox` 全流程闭环（关闭 Firefox 提示 → 导入 → 已登录）；
- [x] `openai` 官方 SDK 的 `models.list` / 非流式 / 流式 / `extra_headers` / `extra_body` 全部通过；
- [x] `ruff check` / `mypy` / `pytest`（31 passed）全绿。

### M2 ——增强

- ✅ **`deepseek-reasoner`（深度思考开关）**：见 7.7；
- ✅ **请求串行化队列**：见 2.4；
- ⬜ 限流感知（429 分级 + 冷却退避）——本轮按要求不做；
- ⬜ Windows 服务化（NSSM 或守护进程）与部署文档——本轮按要求不做。

### M3 ——扩展

- ✅ **附件上传**：见 7.6；
- ⬜ `/admin/status` 面板化与更多诊断；
- ⬜ （高级）思考内容透出：`reasoning_content` 扩展字段，与 DeepSeek 官方 API 同形。

**验收结果（2026-10-21）**：

- [x] 图片附件：64×64 纯红 PNG → 模型答"红色"；
- [x] 文档附件：txt 文件 → 模型答出文件里的代号"蓝鸟计划"；
- [x] 多轮续传：同一会话的后续轮次带附件，模型答"绿色"，会话 ID 不变；
- [x] reasoner + 图片：蓝色 PNG → 答"蓝色"；
- [x] 非法附件被拒：`file://` 地址与损坏的 base64 均返回 400；
- [x] `pytest` 43 passed（新增 7 条附件单测）。

---

## 12. 测试与验收

- **单元测试**（必做）：diff 算法全分支、auto-match 精确性、请求校验 schema、
  错误映射表。不涉及网络与浏览器。
- **端到端验收脚本**（需服务已启动）：

  | 脚本 | 覆盖 |
  |---|---|
  | scripts/e2e_check.py | M1：多轮对话、流式、错误格式、admin（24 项） |
  | scripts/sdk_check.py | M1：openai 官方 SDK 的 5 种调用方式 |
  | scripts/m2_check.py | M2：reasoner、队列串行化、队列统计（8 项） |
  | scripts/m2_multiturn.py | M2：多轮中逐轮切换模型 |
  | scripts/m3_check.py | M3：图片/文档附件、多轮续传、reasoner+附件、非法附件（12 项） |

- **手工集成**：curl 多轮调用、模拟 cookie 过期、断开重连、profile import；
- **PoC 回归**：DeepSeek 页面改版风险下，selectors 集 中化 + `freeaiproxy doctor`
  中含一组选择器自检项，能快速定位"哪个选择器死了"；
- **灰度试运行**：连续调用 4h 不崩溃、内存占用稳定（浏览器长跑）的观察指标。

---

## 13. 风险与对策

| # | 风险 | 等级 | 对策 |
|---|---|---|---|
| R1 | 网页 DOM 改版 → 选择器失效 | 高 | selectors.py 集中管理；doctor 自检；错误映射 `upstream_page_changed` 提示管理员 |
| R2 | 风控检测自动化 → 封号 | 高 | Camoufox 指纹 + 有头 + 拟人化 + 单账号低频；**请用非主力账号**（见 1.4） |
| R3 | cookie 过期 | 中 | 启动自检 + 502 明确提示 + `/admin/relogin` 一键重入 |
| R4 | 长回复超时 | 中 | 双信号 + 分级超时 + 部分返回；`timeout_completion` 可配 |
| R5 | 免费额度限速 | 中 | 429 透传 + M2 冷却避退 |
| R6 | SQLite 文件损坏 | 中 | 只有小并发写；WAL 模式 + 定期 `VACUUM`；profile 目录不启 API 也可恢复 |
| R7 | 并发请求排队过久 | 低 | queue_len 上限 → 429，客户端可自行退避；M1 不做优先级 |
| R8 | profile 目录被用户手动修改破坏 | 低 | 文档明示：**不要**手动改 data/profile；出错走 `import` 重导 |
| R9 | 多 instance 撞 profile | 低 | 启动时检查 profile 是否被其他本服务实例占用（浏览器插件层面 + 进程互斥文件） |

---

## 14. 附录

### 附录 A：集中选择器管理

> DeepSeek 网页是持续迭代的 React 应用。**选择器是这个项目最脆弱的部分**，约束：
> - 不允许在任意 `.py` 文件里出现硬编码 `CSS/XPath` 字符串（评审时对照）；
> - `selectors.py` 中的每个选择器必须**注释：定位意图、首次验证时间**；
> - `doctor` 命令内含选择器自检（`SelectorsLive` 检查表）。
> - 供测试友好的 OR 梯度：每个 selector 配 primary + fallback 两个候选（M1 可选）。

### 附录 B：术语速查

| 术语 | 含义 |
|---|---|
| 网关 / Gateway | FreeAiProxy 服务本体的统称 |
| 引擎 / Engine | BrowserManager + DeepSeek 驱动层的统称 |
| conv_id / chat_id | 见 5.1 |
| profile | Camoufox 浏览器 profile 目录（即登录态本体） |
| headed | 有头模式（窗口可见） |
| PoC | 设计验证脚本集，见 4.4 |

### 附录 C：与"另一类方案"的对比备忘（为什么不是它们）

| 备选 | 为什么不选 |
|---|---|
| Playwright chromium + stealth 插件 | 指纹拼凑易被识别、维护成本高；Camoufox 一站式 |
| 直接调 DeepSeek 官方 API | 稳定、便宜但仍在计费，违背"Free"目标 |
| 用网页站点的 SSE 接口直接抓包 | 点偏内网加密/签名验证，破坏成本高于自动化，且更易风控 |