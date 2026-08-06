# FrameLearn 文档索引与状态

仓库中的文档来自多个开发阶段。阅读前请先确认文档类别，避免把历史设计当成当前实现。

## 当前实现文档

这些文档已按当前代码更新：

- [项目说明（中文）](../README.md)
- [Project README (English)](../README.en.md)
- [当前技术架构](architecture.md)
- [Current Technical Architecture](architecture.en.md)
- [流水线实现说明](pipeline-overview.md)
- [AntiVibe 技术报告](antivibe-technical-report.md)
- [Codex app-server 指南](codex-app-server-guide.md)

## 专题研究文档

这些文档记录某个协议、决策或调研，不等同于当前功能承诺：

- [Codex app-server 多模态研究](app-server-video-multimodal-pipeline.md)
- [从 Bilitato 到 FrameLearn 的决策记录](decisions/bilitato-to-framelearn.md)
- [问题解答记录](questions-answered.md)

其中 `app-server-video-multimodal-pipeline.md` 包含建议方案。当前 `AppServerSession.run_turn()` 仍只接收字符串并发送 text input；以 [当前架构](architecture.md) 为准。

## 已实现功能的历史规格

- [顺序讲稿与分段规格](SPEC-visual-script-segmentation.md)

该文件是实施前规格，部分任务已经完成，但其中的“当前方案”、成本估算、建议类名和未来阶段不应作为当前代码说明。实际触发条件、缓存和降级逻辑见 [流水线实现说明](pipeline-overview.md)。

## 历史/未实现模块设计

`docs/modules/` 下的大部分文件是早期目标架构，不是当前 Python API：

| 文档 | 状态 |
|---|---|
| `planner.md` | 未实现；仓库没有 `PlannerAgent` |
| `executor.md` | 未实现；没有统一 `ToolExecutor`、yt-dlp、Whisper 或 Tesseract 工具层 |
| `analyzer.md` | 未实现；没有 OCR Content Analyzer |
| `generator.md` | 历史设计；当前实现是 `pipeline/doc_generator.py`，接口和输出不同 |
| `qa.md` | 未实现；没有 Chroma/RAG 问答 |
| `command_parser.md` | 历史设计；当前实现使用环境 API 或本地规则，接口已变化 |

这些文件保留用于追溯设计思路，并已加上状态提示。

## 学习笔记

`docs/hello-agents/` 是对 HelloAgents 教材章节的学习笔记。它们解释可借鉴的 Agent/RAG/工具概念，不表示对应模块已集成进 FrameLearn。

## OpenSpec

`../mine/openspec/changes/` 保存提案、设计和任务清单。它们是变更记录，不是用户手册。判断功能是否存在时，应以 `framelearn/` 源码、测试和”当前实现文档”为准。
