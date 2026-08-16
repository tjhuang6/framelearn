# 任务列表：博客锚点流水线（blog-anchor-pipeline）

## 阶段 1：设计定稿

- [x] 与用户对齐 `0816.md` 流程与字段语义
- [x] 产出 proposal / design / spec

## 阶段 2：BlogGenerator

- [x] 定义 `BlogGeneratorOutput` / `FrameRequest` dataclass
- [x] 构建 annotated SRT chunk prompt
- [x] 解析 `blog_markdown` + `frame_requests`
- [x] 校验锚点唯一性与 `[[FRAME:id@timestamp]]` 一致性
- [x] LLM 失败降级为原始字幕拼接
- [x] 单元测试

## 阶段 3：VisionFrameEvaluator

- [x] 定义 `FrameEvaluation` dataclass
- [x] 构建单帧验图 prompt
- [x] 解析 `retake / keep_image / content_type / caption / text_representation`
- [x] 实现 retake 循环（FFmpeg 补截 + 再次验图）
- [x] 失败降级保守保留
- [x] 单元测试

## 阶段 4：ChunkedDocGenerator 编排

- [x] 方案 A：先切块，再向每个 chunk 插入候选帧标记
- [x] 并行运行文本生成与启发式截帧（按依赖关系：截帧完成后插标记，文本调用与启发式截帧并行，文本调用等待 annotated chunk）
- [x] 锚点校验与补截
- [x] 汇总决策与 srt_id 全局化
- [x] 调用 MDAssembler 输出双 Markdown
- [x] 集成测试

## 阶段 5：配置与缓存

- [x] `settings.toml` 增加 `[blog_gen] frame_match_tolerance / max_retakes`
- [x] `config._default_config` 同步默认值
- [x] `CacheManifest.ConfigSnapshot` 纳入 `blog_gen` 配置

## 阶段 6：回归与提交

- [x] 更新 e2e 测试
- [x] 全量 pytest 通过
- [x] git add + commit（不 push）
