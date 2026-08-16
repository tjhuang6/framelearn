# 设计：忠实润色讲稿与小粒度并行 chunk

## Prompt 变化

`BlogGenerator` 的角色从“教育内容编辑/博客作者”改为“字幕润色员”。Prompt 明确要求：

- 不重排、不合并、不提炼、不压缩；
- 保留老师第一人称、设问、强调和现场感；
- 只删纯口水词和明显口误；
- 术语在老师原话有依据时可用括号做初学者向解释；
- 公式与代码原样保留。

锚点 schema 和 `frame_requests` 校验规则不变。

## Chunk 配置

`[chunking]` 默认值：

```toml
segment_minutes = 10
max_images_per_chunk = 20
concurrency = 5
```

- `segment_minutes` 支持 float，方便配置 5.0 / 7.5 / 10.0 等粒度。
- 配置进入 `CacheManifest.ConfigSnapshot`，变更会触发旧缓存失效。

## 并行编排

`ChunkedDocGenerator` 将每个 chunk 的完整后处理放入一个 worker：

```text
for each chunk in parallel (asyncio.Semaphore(chunking.concurrency)):
    BlogGenerator → globalize anchors → resolve anchors/FFmpeg → VisionFrameEvaluator
```

- FFmpeg 锚点绑定通过 `asyncio.to_thread` 执行，不阻塞事件循环。
- 文本生成、锚点绑定、视觉验图任一阶段异常只降级当前 chunk。
- `asyncio.gather` 的结果按 chunk 顺序组装，保证 `blog.md` 与 `srt_picture.md` 的时间顺序稳定。
