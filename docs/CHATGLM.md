# 智谱清言（chatglm.cn）适配说明

智谱清言作为第四个 provider 接入，profile 为 `data/profiles/chatglm`，
浏览器 locale 为 `zh-CN`。首次使用需在**有头**浏览器里登录一次
（手机号验证码或微信扫码，`freeaiproxy relogin --provider chatglm`），
登录态持久化后无头模式亦可正常调用。

选择器于 2026-10-23 对着真实登录页面校准（探测脚本见 `poc/chatglm_probe*.py`）。

## 1. 模型

| 模型 | 能力 | 状态 |
|---|---|---|
| `chatglm-glm5.3` | GLM-5.3 深度思考对话 | 已接入（思考链不返回） |
| `chatglm-image` | AI画图（GLM Image 模式，无水印） | 已接入（`/v1/images` 或 chat） |

## 2. 登录与会话

- 登录墙是覆盖在首页上的弹窗（`input[placeholder="请输入手机号"]` 等，
  文案“微信扫码登录/手机号登录/未注册手机号将自动注册”）。
- 会话以 URL query `cid=<24位hex>` 寻址：主聊天为
  `/main/alltoolsdetail?lang=zh&cid=...`，AI画图为固定 agent 页
  `/main/gdetail/65a232c082ff90a2ad2f15e2?lang=zh&cid=...`。
- `conversations.chat_id` 存 cid；画图会话加 `draw:` 前缀以区分两种页面。
- 该站吞掉 Playwright 原生鼠标点击：输入一律用 `fill()` + Enter 提交，
  弹层控件一律用 `evaluate(el.click())` 触发。

## 3. 模型与深度思考

- `.think-mode-trigger` 弹层（`.think-mode-list`）即模型选择器：
  `.think-mode-item .item-name` 为 `GLM-5.3` / `GLM-Flash`，另有“思考强度”
  子菜单（快速/深度/极致）。每次发送前显式对齐 `GLM-5.3`。
- GLM-5.3 总是先思考再作答；思考过程渲染为
  `.answer-content-wrap.text-advance-thinking-content`，正文取其余
  `.answer-content-wrap`，思考链不返回。

## 4. 收发与完成判定

- 输入：`#search-input-box textarea` fill → Enter。
- 一轮回答是新的 `.answer` 容器（问题为 `.conversation.question`）。
- 完成信号：`.enter` 不再有 `searching` 类（生成中该类存在并挂“停止对话”
  tooltip）+ 正文文本稳定。

## 5. 图片生成（AI画图）

- 入口是 AI画图 agent 页；composer 与主聊天相同。
- 工具条 `.prompt-item`：模式（快速/GLM Image）、风格、比例、咒语、
  去水印。driver 每次发送前确保：
  - 模式选中 `GLM Image`（chip 文本被 CSS 截断为“GLM”，故以弹层
    `.el-radio.is-checked` 校验）；
  - `去水印` 开启（弹层“图片去水印”里点“开启”，chip 变为“去水印（开）”）。
- 一次生成 4 张 `.img-answer-item img.limitSize`，src 在
  `sfile.chatglm.cn/testpath/<cid>_<i>_0.jpg`；去掉 `?image_process=...`
  即原图。去水印在生成时开启，则 CDN 直链本身就无水印，httpx 直接下载，
  存 `data/generated/` 经 `/v1/files/<name>` 返回。
- 预览弹层（image-editor-dialog）另有“下载图片/超清下载”blob 下载，
  已验证可被 Playwright 捕获，作为备用路径未启用。
- 比例 chip（`1:1`）的弹层选项未校准，`select_image_ratio` 为空操作；
  `size` 仅元宝生效。
