# 缓存可追溯性设计文档

## 概述

本文档描述了 FrameLearn 的缓存 manifest 系统，用于解决缓存来源不可追溯的问题。

## 问题背景

**原有问题**：
- 缓存判断散落在 `video_pipeline.py` 和 `doc_generator.py` 中
- 仅检查文件存在性，不验证来源
- 无法追溯：输入文件变化、配置变化、模型变化时仍使用旧缓存
- 用户无法判断产物使用了哪个源文件、模型、prompt 或配置

## 解决方案

### 1. Manifest 结构

每个缓存目录都会生成一个 `manifest.json` 文件，记录：

```json
{
  "version": "1.0",
  "created_at": "2024-08-05T23:30:00",
  "input_file": {
    "path": "/path/to/video.mp4",
    "size": 123456789,
    "mtime": 1722879000.0,
    "sha256": "abc123def456"
  },
  "subtitle_file": {
    "path": "/path/to/subtitle.srt",
    "size": 12345,
    "mtime": 1722879100.0,
    "sha256": "xyz789"
  },
  "config": {
    "scene_threshold": 0.3,
    "fallback_interval": 30,
    "max_keyframes": 100,
    "doc_mode": "visual_script",
    "segment_duration": 90,
    "max_keyframes_per_segment": 10,
    "vision_provider": "siliconflow",
    "vision_model": "Qwen/Qwen2.5-VL-72B-Instruct",
    "asr_provider": "dashscope",
    "asr_model": "paraformer-v2",
    "keyframe_selection": false,
    "quality_review": false
  },
  "git_commit": "d91148f",
  "segments_total": 5,
  "segments_completed": [
    {
      "index": 0,
      "completed": true,
      "timestamp": "2024-08-05T23:31:00",
      "error": null
    },
    {
      "index": 1,
      "completed": true,
      "timestamp": "2024-08-05T23:32:00",
      "error": null
    }
  ],
  "cache_key": "a1b2c3d4e5f6"
}
```

### 2. Cache Key 计算

Cache key 由以下因素生成：
- 输入文件的 SHA256（前 1MB）+ 文件大小
- 外部字幕文件的 SHA256 + 文件大小（如果有）
- 配置快照的 JSON 序列化后的 SHA256
- Git commit hash（前 8 位）

**示例**：
```
input:abc123:123456789|subtitle:xyz789:12345|config:def456|git:d91148f
→ SHA256 → a1b2c3d4e5f6 (前 16 位)
```

### 3. 缓存位置与 Manifest

| 缓存类型 | 目录 | Manifest 路径 | 说明 |
|---------|------|--------------|------|
| 字幕 | `output/video_name/src/` | `subtitle_manifest.json` | ASR 结果缓存 |
| 关键帧 | `output/video_name/src/` | `keyframe_manifest.json` | 关键帧提取结果 |
| 文档段落 | `output/video_name/segments_{mode}/` | `manifest.json` | 段落生成进度 |

### 4. 缓存读取流程

```python
# 1. 检查缓存文件和 manifest 是否存在
cached_files = src_dir.glob("frame_*.jpg")
manifest_path = src_dir / "keyframe_manifest.json"

if cached_files and manifest_path.exists():
    # 2. 加载 manifest
    manifest = CacheManifest.load(manifest_path)
    
    # 3. 校验 manifest 是否与当前输入/配置匹配
    if manifest.validate(
        video_path=video_path,
        subtitle_path=subtitle_path,
        config_get_fn=config_get,
        mode="keyframe",
        asr_provider="n/a",
        asr_model="n/a",
    ):
        # 4. 缓存有效，直接使用
        print("✅ 使用已缓存的关键帧")
        use_cache = True
    else:
        # 5. 缓存失效，重新生成
        print("⚠️ 关键帧缓存失效（输入或配置已变更）")
        use_cache = False
```

### 5. 缓存写入流程

```python
# 1. 处理完成后创建 manifest
from framelearn.pipeline.cache_manifest import create_manifest

manifest = create_manifest(
    video_path=video_path,
    subtitle_path=subtitle_path,
    config_get_fn=config_get,
    mode="keyframe",
    asr_provider="n/a",
    asr_model="n/a",
)

# 2. 保存 manifest
manifest.save(src_dir / "keyframe_manifest.json")
print("✅ 关键帧 manifest 已保存")
```

### 6. 段落生成进度追踪

对于分段生成的文档，manifest 还记录每个段落的完成状态：

```python
from framelearn.pipeline.cache_manifest import mark_segment_completed, get_completed_segments

# 标记段落完成
mark_segment_completed(manifest_path, segment_index=0)

# 标记段落失败
mark_segment_completed(manifest_path, segment_index=1, error="API timeout")

# 获取已完成的段落
completed = get_completed_segments(manifest_path)  # {0, 2, 3, ...}
```

**恢复流程**：
1. 加载 manifest 并验证 cache key
2. 如果有效，获取已完成段落集合
3. 跳过已完成的段落，只生成未完成的
4. 每完成一个段落，立即更新 manifest

## 实现细节

### 核心模块

**`framelearn/pipeline/cache_manifest.py`**：
- `InputFileInfo`: 文件元数据（path, size, mtime, sha256）
- `ConfigSnapshot`: 配置快照（所有影响输出的配置项）
- `CacheManifest`: 完整的缓存清单
- `create_manifest()`: 创建新 manifest
- `mark_segment_completed()`: 标记段落完成
- `get_completed_segments()`: 获取已完成段落

### 集成点

**`video_pipeline.py`**：
- L85-120: 字幕缓存读取与 manifest 验证
- L135-150: 关键帧缓存读取与 manifest 验证
- L110-118: 字幕 manifest 创建
- L165-173: 关键帧 manifest 创建
- L192-196: 传递 ASR 信息给 doc_generator

**`doc_generator.py`**：
- L185-215: 段落 manifest 加载与验证
- L230-235: 使用 manifest 跳过已完成段落
- L255-258: 段落完成后更新 manifest

## 缓存失效场景

以下情况会导致缓存失效（cache key 不匹配）：

### 输入变化
- ✅ 视频文件内容变化（文件 hash 变化）
- ✅ 视频文件大小变化
- ✅ 外部字幕文件变化
- ❌ 视频文件路径变化（不影响，因为用的是 hash）

### 配置变化
- ✅ `video.scene_threshold` 变化（影响关键帧提取）
- ✅ `video.max_keyframes` 变化
- ✅ `doc_generation.segment_duration` 变化
- ✅ `runtime.vision_provider` 或 `vision_model` 变化
- ✅ ASR provider 或 model 变化
- ✅ `agent.keyframe_selection` 或 `quality_review` 开关变化

### 代码变化
- ✅ Git commit hash 变化（表示代码逻辑可能变化）
- ℹ️ 只记录 commit，不强制失效（避免开发时频繁重跑）

### 不影响缓存
- ❌ 输出目录位置变化
- ❌ `video.keep_temp_files` 等不影响结果的配置
- ❌ 文件 mtime 变化（只要内容和大小不变）

## 用户可见性

### 日志输出

缓存命中：
```
⏭️  使用已缓存字幕...
✅ Manifest 有效，已完成 3/5 段
⏭️  第 1/5 段已缓存，跳过...
```

缓存失效：
```
⚠️  字幕缓存失效（输入或配置已变更）
⚠️  Manifest 失效（输入或配置已变更），重新生成
```

新建缓存：
```
✅ 字幕 manifest 已保存
✅ 关键帧 manifest 已保存
✅ 创建新 manifest
```

### Manifest 文件查看

用户可以直接打开 `manifest.json` 查看：
- 使用了哪个视频文件（路径和 hash）
- 使用了哪个 ASR provider 和 model
- 使用了哪个 Vision provider 和 model
- 使用了哪些配置参数
- 代码版本（git commit）
- 生成时间和段落完成状态

## 测试覆盖

### 单元测试 (`test_cache_manifest.py`)
- ✅ InputFileInfo 创建和 hash 计算
- ✅ ConfigSnapshot 从 config 生成
- ✅ CacheManifest cache key 计算
- ✅ Cache key 在输入变化时改变
- ✅ Cache key 在配置变化时改变
- ✅ Manifest 保存和加载
- ✅ Manifest 验证成功场景
- ✅ Manifest 验证失败场景
- ✅ 段落完成标记
- ✅ 段落失败标记
- ✅ 外部字幕文件处理

### 集成测试 (`test_cache_integration.py`)
- ✅ 字幕 manifest 创建
- ✅ 关键帧 manifest 创建
- ✅ 段落 manifest 创建和进度追踪
- ✅ 视频变化导致缓存失效
- ✅ 配置变化导致缓存失效
- ✅ 缓存有效时可复用

## 未来改进

### 可选优化
1. **完整文件 hash**：当前只 hash 前 1MB，可选配置完整 hash
2. **远程 manifest 同步**：支持多机器共享缓存 manifest
3. **LLM response hash**：记录 LLM 实际返回内容的 hash，验证缓存完整性
4. **配置 diff 显示**：缓存失效时显示具体哪个配置项变化了

### 性能考虑
- Hash 计算只读取文件前 1MB，大文件也很快
- Manifest 文件很小（< 5KB），加载和验证开销可忽略
- 段落级别的 manifest 更新只涉及 JSON 追加，不影响性能

## 向后兼容

**旧缓存处理**：
- 如果缓存目录中没有 `manifest.json`，会被视为"来源不明"
- 用户会看到警告日志："⚠️ 字幕 manifest 损坏" 或缓存不存在
- 自动重新生成并创建新 manifest
- 不会破坏用户已有的缓存文件（只是不信任它们）

**迁移建议**：
- 对于重要项目，建议清空 `output/` 重新生成
- 对于测试项目，可以继续使用（但会提示 manifest 缺失）

## 总结

Manifest 系统提供了：
1. ✅ **可追溯性**：每个缓存都有完整的来源记录
2. ✅ **可靠性**：输入或配置变化时自动失效缓存
3. ✅ **可恢复性**：段落级进度追踪，支持断点续传
4. ✅ **可调试性**：用户可查看 manifest 了解产物来源
5. ✅ **向后兼容**：不影响旧代码，旧缓存会自动重建

系统在保持零配置的同时，让缓存变得可信赖和可追溯。
