# 错误被"偏向继续"掩盖修复 - 技术报告

## 问题描述（antivibe-technical-report.md 第 194 行）

**问题**：
- pHash 失败直接跳帧
- Agent LLM 决策失败默认补帧
- 图像评估失败最终默认保留
- DashScope 某些分段失败时，只要还有结果就继续合并

这些策略符合"不丢内容"的目标，但产物没有统一记录降级事件，用户难以知道结果是否完整。

**建议**：`PipelineResult` 增加 warnings；输出 `run-report.json`，列出失败分段、fallback、跳过帧和缓存命中。

## pi-agent 执行结果

✅ **已完成** - pi-agent 系统性地为所有静默容错路径添加了统一的事件记录机制

### 核心设计：RunReporter

**新模块**：`framelearn/pipeline/run_report.py`

`RunReporter` 类维护四个事件桶：
- `failed_segments` - 失败的分段
- `fallbacks` - 降级/回退事件
- `skipped_frames` - 跳过的帧
- `cache_hits` - 缓存命中

**全局访问器模式**：`get_reporter()` / `set_reporter()` / `reset_reporter()`，模仿现有的 `PrivacyTracker` 设计。这样调用栈深处的任何模块都能上报事件，而不需要把 reporter 一层层传递穿过每个函数签名。

`get_warnings()` 返回按时间顺序排列的人类可读字符串列表（缓存命中不计入 warnings，因为那不是降级）。

`write_report()` 把所有内容导出为 JSON。

### 全面覆盖的静默容错路径

pi-agent 逐一排查并接入了以下所有位置：

| 文件 | 原有静默行为 | 新增记录 |
|------|-------------|---------|
| `keyframe_dedup.py` | pHash 失败 → 裸 `continue` | `record_skipped_frame` |
| `agent_keyframe_selector.py` | LLM 决策失败 | `record_fallback` |
| `agent_keyframe_selector.py` | 帧截取失败 | `record_skipped_frame` |
| `agent_keyframe_selector.py` | vision 评估失败 | `record_fallback` |
| `agent_keyframe_selector.py` | vision+text 评估失败（默认保留） | `record_fallback` |
| `doc_generator.py` | 3 次质量审查耗尽 | `record_fallback` |
| `doc_generator.py` | 分段重试耗尽 | `record_failed_segment` |
| `doc_generator.py` | app-server 找不到 .md，回退到 final_text | `record_fallback` |
| `doc_generator.py` | 分段 manifest 失效 | `record_fallback` |
| `doc_generator.py` | 分段缓存命中 | `record_cache_hit` |
| `asr_backends/dashscope.py` | 分片提交失败 | `record_failed_segment` |
| `asr_backends/dashscope.py` | 分片轮询失败 | `record_failed_segment` |
| `asr_backends/dashscope.py` | 部分合并（分片缺失但继续） | `record_fallback` |
| `video_pipeline.py` | 字幕缓存失效/损坏 | `record_fallback` |
| `video_pipeline.py` | 字幕缓存命中 | `record_cache_hit` |
| `video_pipeline.py` | 关键帧缓存失效 | `record_fallback` |
| `video_pipeline.py` | 关键帧缓存命中 | `record_cache_hit` |

**覆盖范围**：技术报告中列出的全部 4 类问题（pHash 失败、Agent LLM 决策失败、图像评估失败、DashScope 分段失败）均已接入，并额外扩展到缓存失效/命中场景。

### PipelineResult 增加 warnings 字段

```python
# PipelineResult 现在包含
warnings: list[str]  # 从 reporter.get_warnings() 填充
```

无论运行成功还是失败，`_run_internal()` 完成后都会填充 `warnings`。

### run-report.json 输出

`VideoPipeline.run()` 现在会：
1. 每次运行创建一个 `RunReporter`
2. 设为运行期间的全局 reporter
3. 返回前写入 `<output_dir>/run-report.json`（包含 `status`/`error`/`summary`/各事件桶明细）
4. **无论运行成功或失败都会写入**

## 测试覆盖

**新增测试文件**：`test/src/test_run_report.py`（22 个新测试）

覆盖范围：
- `RunReporter` 本身的单元测试
- 全局访问器（get/set/reset）
- 每个原先静默的容错路径（dedup pHash、agent selector 的 decide/evaluate/capture、doc_generator 质量审查）
- `PipelineResult.warnings` 字段
- 2 个端到端测试：验证 `VideoPipeline.run()` 正确写入 `run-report.json` 并填充 `warnings`（使用 fast-fail FFmpeg-missing 路径，避免 CI 中需要真实 ffmpeg/ASR 凭据）

### 测试结果

**133 个测试全部通过**（111 个既有 + 22 个新增），**无回归**。

## 相关文件

### 新增的文件
- `framelearn/pipeline/run_report.py` - RunReporter 核心模块
- `test/src/test_run_report.py` - 22 个测试

### 修改的文件
- `framelearn/pipeline/keyframe_dedup.py` - pHash 失败记录
- `framelearn/pipeline/agent_keyframe_selector.py` - 4 处容错路径记录
- `framelearn/pipeline/doc_generator.py` - 5 处容错/缓存路径记录
- `framelearn/pipeline/asr_backends/dashscope.py` - 3 处分段失败/降级记录
- `framelearn/pipeline/video_pipeline.py` - 4 处缓存失效/命中记录，PipelineResult.warnings，run-report.json 写入

## 总结

pi-agent 完整实现了本问题建议的解决方案：

1. ✅ 设计了统一的 `RunReporter` 事件记录机制（四个事件桶）
2. ✅ 采用全局访问器模式，避免侵入式地修改每个函数签名
3. ✅ 系统性排查并接入了全部 4 类已知静默容错路径，并额外覆盖了缓存失效/命中场景（比原问题描述范围更广）
4. ✅ `PipelineResult` 新增 `warnings` 字段
5. ✅ 每次运行（无论成败）都写入 `run-report.json`
6. ✅ 22 个新测试全面覆盖，133 个测试全部通过，无回归

**修复价值**：用户现在可以通过 `run-report.json` 准确了解一次任务中发生了哪些降级、跳帧、失败分段，以及利用了哪些缓存——不再是"黑盒"式的静默容错。

---

**状态**: ✅ 已完成并测试通过（133/133，22 个新测试）  
**修复人**: pi-agent (OpenAI Codex)  
**覆盖范围**: 超出原问题描述，额外覆盖缓存失效/命中场景
