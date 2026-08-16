# blog-faithful-transcript Specification

## Purpose
把 `blog.md` 的生成目标从摘要式博客改为忠实润色讲稿，并将 chunk 大小与并行处理方式做成适合长视频的配置。

## ADDED Requirements

### Requirement: BlogGenerator 生成忠实润色讲稿
系统 SHALL 要求文本模型按字幕原始顺序转写全部教学内容，MUST NOT 重排、合并、提炼或压缩老师讲解。输出 SHALL 保留讲述人的第一人称语气、设问、强调、例子和现场感。

#### Scenario: 保留老师语气
- **WHEN** 字幕包含“我们来看一下这个图”
- **THEN** 输出可以是“我们来看一下这张图”，而不是“老师展示了该图”

#### Scenario: 不压缩课程信息
- **WHEN** 一个 chunk 内老师讲解多个知识点、推导和例子
- **THEN** 输出包含这些知识点、推导和例子，不能只保留结论

### Requirement: chunk 时长来自 settings.toml 且默认 10 分钟
系统 SHALL 从 `[chunking].segment_minutes` 读取 chunk 时长，支持小数分钟。默认值 SHALL 为 10 分钟。

#### Scenario: 配置为 5
- **WHEN** `settings.toml` 中 `segment_minutes = 5`
- **THEN** 视频按约 5 分钟边界切 chunk

### Requirement: chunk 端到端并行处理
系统 SHALL 对每个 chunk 的文本生成、锚点绑定、视觉验图并行处理，并发上限来自 `[chunking].concurrency`。单 chunk 失败 MUST 只降级该 chunk，不影响其他 chunk。

#### Scenario: 长视频
- **WHEN** 视频被切成 24 个 10 分钟 chunk，`concurrency = 5`
- **THEN** 最多 5 个 chunk 同时在处理

#### Scenario: 单 chunk 文本生成异常
- **WHEN** 某 chunk 的 BlogGenerator 重试后仍异常
- **THEN** 该 chunk 降级为原始字幕拼接，其他 chunk 正常写入 `blog.md`
