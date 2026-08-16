# 设计：在线字幕接入

## 数据结构

`DownloadedVideo` 增加：

```python
subtitle_path: Path | None = None
subtitle_language: str | None = None
subtitle_source: str | None = None  # supadata | bilibili | yt-dlp
```

## 字幕查找顺序

```text
YouTube:
  SUPADATA_API_KEY 存在？
    → GET https://api.supadata.ai/v1/transcript
      url=<canonical watch url>&text=false&lang=en&mode=native
    → 200 解析 content[].text/offset/duration
    → 202 每 1s 轮询，最多 60 次
    → 206/404 视为无原生字幕
  → 下载目录内 yt-dlp 字幕文件（优先 en，其次 zh）
  → VideoPipeline ASR 兜底

Bilibili:
  GET https://api.bilibili.com/x/player/pagelist?bvid=...
    → 第一个/URL 指定分 P 的 cid
  GET https://api.bilibili.com/x/player/v2?bvid=...&cid=...
    → data.subtitle.subtitles[].{lan, lan_doc, subtitle_url}
    → 中文优先，其次英文，否则第一个
  GET subtitle_url（带 BILIBILI_COOKIE 和 Referer）
    → body[].from/to/content 转 SRT
  → 下载目录内 yt-dlp 字幕文件
  → VideoPipeline ASR 兜底
```

## 与 youtube-digest / Bilitato 的对应关系

| FrameLearn | 参考项目 | 移植内容 |
|---|---|---|
| `_fetch_supadata_subtitle` | youtube-digest `handleFetchTranscript` | `lang=en`、`mode=native`、202 轮询、`>>` 清理 |
| `_fetch_bilibili_subtitle` | Bilitato `refreshSubtitleOptionsForCurrentVideo` / `fetchSubtitleBody` | `x/player/v2` 字幕列表、中文优先、`body` 解析 |

## Router

显式 `--subtitle` 优先级最高；否则使用 `downloaded.subtitle_path`；都为空则保持原 ASR 路径。
