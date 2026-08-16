# online-platform-subtitles Specification

## Purpose
在线视频下载完成后自动接入平台原生字幕，并按 youtube-digest / Bilitato 的方式处理 YouTube 与 Bilibili 字幕。

## ADDED Requirements

### Requirement: YouTube 字幕按 youtube-digest 方式获取
系统 SHALL 在配置 `SUPADATA_API_KEY` 时调用 Supadata native transcript API，参数为 `text=false`、`lang=en`、`mode=native`。202 响应 MUST 轮询到完成或超时；206/404 SHALL 视为无原生字幕并继续回退。

#### Scenario: 英文视频有原生字幕
- **WHEN** YouTube 视频存在原生英文字幕且配置了 Supadata key
- **THEN** 系统下载 timestamped chunks，去除 `>>` 标记并写入 SRT，传给 VideoPipeline

#### Scenario: 无原生英文字幕
- **WHEN** Supadata 返回 206 或未配置 key
- **THEN** 系统回退 yt-dlp 字幕；仍无字幕则走 ASR

### Requirement: Bilibili 字幕按 Bilitato 方式获取
系统 SHALL 通过 `x/player/pagelist` 解析 CID，通过 `x/player/v2` 读取 `data.subtitle.subtitles`，中文优先选择字幕，并解析字幕 JSON 的 `body[].from/to/content` 为 SRT。

#### Scenario: 中文 CC 字幕可用
- **WHEN** B 站视频存在中文 CC 字幕
- **THEN** 系统选择中文字幕并写入 `{video_id}.zh.srt`

#### Scenario: 字幕需要登录
- **WHEN** 字幕接口要求登录
- **THEN** 系统使用 `.env` 中 `BILIBILI_COOKIE`；无 Cookie 时回退 yt-dlp/ASR

### Requirement: 在线字幕接入 Pipeline 且 ASR 仍为兜底
系统 SHALL 将下载到的字幕路径传给 `VideoPipeline`。显式 `--subtitle` MUST 优先于在线字幕；无任何字幕时 MUST 保持现有 ASR 行为。

#### Scenario: 在线字幕存在
- **WHEN** 下载器返回 `subtitle_path` 且用户未传 `--subtitle`
- **THEN** `VideoPipeline` 使用该字幕并跳过 ASR

#### Scenario: 手动字幕优先
- **WHEN** 用户传了 `--subtitle`
- **THEN** 忽略在线字幕，使用手动字幕
