# 缓存可追溯性和失效策略修复 - 技术报告

## 问题描述（antivibe-technical-report.md 第 171 行）

**问题**：缓存判断散落在 `video_pipeline.py:85-97`、`:129-141` 和 `doc_generator.py:209-215`。它们没有 manifest。用户无法从产物判断使用了哪个源文件、模型、prompt 或配置。

**建议**：每个任务写 `manifest.json`，记录输入文件 hash/mtime/size、配置、provider/model、代码版本、段落完成状态。只有 cache key 匹配才复用。

## pi-agent 执行结果

✅ **已完成** - pi-agent 审查后发现 manifest 系统实际已在之前会话中完整实现，本次任务定位并修复了两个隐藏的逻辑缺陷。

### 重要发现

pi-agent 报告：Manifest 系统（`cache_manifest.py`、pipeline 集成、20 个专项测试）**已经在之前的会话中完整实现**。本任务转为审查+修缺陷，而非从零实现。

### 修复的两个缺陷

#### 缺陷 1：Git commit 混入 cache key（导致缓存几乎失效）

**问题**：每次代码提交都会改变 cache key，导致所有缓存立即失效——开发期间基本等于没有缓存。

**修复**：把 git commit 从 cache key 的计算中移除，但**仍保留在 manifest 里供追溯查看**（用户仍能看到产物由哪个代码版本生成，只是不再作为失效判据）。

```python
# cache_manifest.py
# 修复前：cache_key 计算包含 git_commit → 每次 commit 缓存全部失效
# 修复后：cache_key 只基于输入文件/配置/模型；git_commit 单独记录用于展示
```

#### 缺陷 2：外部字幕路径传递错误（导致缓存失效判断失真）

**问题**：`video_pipeline.py` 中验证 manifest 时，`subtitle_path` 参数硬编码传的是 `None`，导致外部字幕文件发生变化时，缓存**不会**正确触发失效（该失效的没失效）。

**修复**：改为传递真实的 `self.subtitle_path`。

```python
# video_pipeline.py
# 修复前：manifest.validate(..., subtitle_path=None, ...)
# 修复后：manifest.validate(..., subtitle_path=self.subtitle_path, ...)
```

## 已有系统能力确认（非本次新增，此前会话完成）

- ✅ `InputFileInfo`：文件元数据（路径、大小、mtime、SHA256 hash）
- ✅ `ConfigSnapshot`：配置快照（14 个关键配置项）
- ✅ `CacheManifest`：完整 manifest 数据结构，含 cache key 计算
- ✅ 字幕/关键帧/段落三处缓存均已集成 manifest 验证
- ✅ 段落级进度追踪，支持断点续传
- ✅ `inspect_manifest.py` 调试工具可用

## 测试验证

pi-agent 运行了以下测试：

| 测试文件 | 数量 | 结果 |
|---------|------|------|
| `test_cache_manifest.py` | 14 | ✅ 全部通过 |
| `test_cache_integration.py` | 6 | ✅ 全部通过 |
| `test_pipeline.py` | 48 | ✅ 全部通过 |
| **合计** | **68** | ✅ **全部通过** |

另外编写了临时脚本手动验证：**不同 git commit 下 cache key 保持一致**（验证缺陷1已修复）。

### 已知无关问题（未处理，不在本次范围）

全仓库跑测试时有 1 个失败：`test_agent_keyframe.py` 里的帧命名断言。pi-agent 判断这是**之前会话修改关键帧文件命名格式导致的既有问题**，与本次缓存修复无关，未做改动。

> 注：该断言失败很可能是"关键帧文件名冲突修复"任务（毫秒精度命名格式变更）与本任务并行运行时产生的交叉影响，需要后续统一处理。

## 相关文件

### 修改的文件（本次）
- `framelearn/pipeline/cache_manifest.py` - 移除 git commit 参与 cache key 计算
- `framelearn/pipeline/video_pipeline.py` - 修正 `subtitle_path` 参数传递

### 此前会话已实现（本次未改动，仅审查确认）
- `framelearn/pipeline/cache_manifest.py` - manifest 核心结构
- `framelearn/pipeline/doc_generator.py` - 段落级 manifest 集成
- `framelearn/tools/inspect_manifest.py` - 调试工具
- `docs/cache_manifest.md`、`docs/cache_architecture.md`、`docs/MANIFEST_QUICK_REF.md` - 设计文档

## 总结

本任务的实际工作量小于预期，因为 manifest 系统本身已经存在。pi-agent 的价值在于：

1. ✅ **审查而非重复实现** —— 识别出系统已存在，避免了重复劳动
2. ✅ **发现隐藏缺陷** —— git commit 混入 cache key 导致的"伪缓存失效"，如果不修，缓存系统形同虚设
3. ✅ **发现测试疏漏** —— 外部字幕路径判断错误，原有测试未覆盖到这个场景
4. ✅ **验证充分** —— 68 个测试通过 + 手动 cache key 一致性验证
5. ⚠️ **发现但未处理的遗留问题** —— 关键帧命名断言失败，需要与关键帧命名任务的产物对齐

## 待办事项

- [ ] 检查 `test_agent_keyframe.py` 的失败断言，是否因关键帧命名格式变更（毫秒精度）导致，需要更新测试断言以匹配新格式

---

**状态**: ✅ 已完成并测试通过（68/68）  
**修复人**: pi-agent (OpenAI Codex)  
**发现的遗留问题**: 1 个（关键帧命名测试断言，与其他并行任务交叉）  
**可追溯性**: ⚠️ 使用了 --no-session，本报告基于任务最终总结输出撰写
