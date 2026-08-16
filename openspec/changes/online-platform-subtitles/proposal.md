# 提案：在线视频自动接入平台字幕

## 问题

当前 `framelearn run <URL>` 只下载视频流，`subtitle_path` 始终为空，线上视频一律走“提取音轨 → ASR”。这忽略了 YouTube 和 B 站已有的原生字幕，既慢又增加了 ASR 成本，而且英文视频用 ASR 的效果通常不如平台原生 transcript。

## 目标

1. 下载完成后自动寻找并保存平台字幕，传给 `VideoPipeline`。
2. YouTube 采用 youtube-digest 的方式：Supadata `lang=en&mode=native`，解析 timestamped chunks，去除 `>>` speaker marker，长视频 202 异步轮询。
3. Bilibili 采用 Bilitato 的方式：`x/player/pagelist` 取 CID，`x/player/v2` 取字幕列表，中文优先，下载 JSON 后把 `body[].from/to/content` 转成 SRT。
4. 平台字幕拿不到时回退 yt-dlp 字幕，再回退现有 ASR，不改变本地 `--subtitle` 优先级。

## 非目标

- 不实现抖音/快手平台字幕（没有稳定公开字幕接口）。
- 不把 Supadata 用作付费 AI 转写；只使用 `mode=native`，与 youtube-digest 一致。
