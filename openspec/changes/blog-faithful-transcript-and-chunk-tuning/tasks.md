# 任务列表

- [x] 重写 BlogGenerator prompt 为“忠实润色讲稿”
- [x] `[chunking].segment_minutes` 默认 30 → 10，支持 float
- [x] `[chunking].max_images_per_chunk` 默认 50 → 20
- [x] `SRTChunker` / `ChunkedDocGenerator` / `ConfigSnapshot` 支持可配置 chunk 时长
- [x] ChunkedDocGenerator 改为按 chunk 端到端并行，FFmpeg 绑定放入 worker thread
- [x] 单 chunk 异常隔离并降级，不影响其他 chunk
- [x] 更新 settings.toml / README 中英文说明
- [x] 新增 prompt、配置与单 chunk 失败隔离测试
