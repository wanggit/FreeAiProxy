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
| `wenxin-music` | 音乐生成 | 已接入（曲风/音色/歌词，`/v1/music` 或 chat） |

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

## 5. 音乐生成

- 入口不在默认 capsule 行里：先用**真实鼠标点击**展开 `.more-dropdown-trigger`
  （“更多”，合成事件不生效），DOM 中才会出现文本为“音乐生成”的 `.capsule-text`，
  再按普通 capsule 点击进入。
- 音乐 composer 有三个指令触发器（`.ci-tooltip`）：选曲风 / 选音色 / AI写歌词；
  选中后触发器文本变为所选值。弹层选项是 `.music-directive-button`，
  点击即生效（无需确认按钮）。
  - 曲风：默认/摇滚/电子/R&B/嘻哈/金属/蓝调/流行/民谣/爵士/说唱/拉丁/朋克。
  - 音色：智能匹配（默认）/深情男声/甜美女声。
  - 歌词：AI写歌词（默认，`prompt` 作为主题描述）或自定义歌词（把歌词直接
    敲进 `div.ci-editor`）。
- 发送后 answer 容器先显示“AI音乐创作中，预计需要2分钟”占位（`.ai-music-player`
  带 loading 图标、无 `<audio>`）；**同一个** answer 容器随后被替换为成品，
  `aCount` 不增加，因此完成判定是：`.ai-music-player` 内出现非 blob 的
  `<audio>/<source>` src 且 loading 图标消失且 URL 稳定。
- 成品 src 是 `aigc-idea-platform.bj.bcebos.com/...mp3?authorization=bce-auth-v1...`
  签名直链，可离线 httpx 下载；对象名虽为 `.mp3`，实测内容是 FLAC，
  网关按魔数落盘 `.flac` 并经 `/v1/files/<name>` 返回。
- 探测记录：`poc/wenxin_music_probe1..7.py`（更多下拉、指令弹层选项、
  端到端生成与轮询）。
