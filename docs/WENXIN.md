# 文心（wenxin.baidu.com）适配说明

百度「文心」（原文心一言）作为第三个 provider 接入，profile 为 `data/profiles/wenxin`，
浏览器 locale 为 `zh-CN`。首次使用需在**有头**浏览器里用百度账号登录一次
（`freeaiproxy relogin --provider wenxin`），登录态持久化后无头模式亦可正常调用。

选择器于 2026-10-23 对着真实登录页面校准。

## 1. 模型

| 模型 | 能力 | 状态 |
|---|---|---|
| `wenxin-image` | 图片生成 | 已接入（无水印） |
| `wenxin-writing` | 帮我写作 | 已接入（提示词→文本） |

## 2. 登录与会话

- 登录墙是覆盖在首页上的百度 passport 弹窗（`pass-module` / `pass-text-input` /
  `phoenix-login`，文案“请登录/扫码登录/账号登录/短信登录”）。
- 会话 URL 形如 `/search/<数字id>`；`conversations.chat_id` 存该数字 id。
- 首页（对话）composer 是 `textarea.ci-textarea`，但任一 agent 模式会把它换成
  contenteditable 的 `div.ci-editor`；因此“已登录”判据同时接受两者。

## 3. 收发与完成判定

- 输入：点击 composer → 全选清空 → `keyboard.insert_text` → 点 `.ci-submit-button`。
- 一轮回答渲染为新的 `[class*="conversation-flow-answer-container"]`；正文取其内
  `.cosd-markdown` 文本（追问建议 `cs-question-closely-*` 在其外，不纳入）。
- 完成信号：出现新的 answer 容器 + `.cosd-markdown-loading` 消失 + 文本稳定。

## 4. 图片生成

- 一次生成 4 张 `<img>`，src 在 `gips*.baidu.com/it/u=...`，带 `wm=1,baiduai3,...`
  与 `wmo=...` 水印参数。
- 无水印：直接对 URL 去掉 `&wm=...`、`&wmo=...`（`_watermark_free`）后 httpx 下载，
  存 `data/generated/`，经 `/v1/files/<name>` 返回。实测无头/有头均无水印。
- 文心 composer 无宽高比控件，`select_image_ratio` 为空操作；`size` 仅元宝生效。
