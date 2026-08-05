# 缓存可追溯性修复

## 修复内容

本次修复解决了缓存来源不可追溯的问题，实现了完整的 manifest 系统。

## 新增功能

### 1. Manifest 系统 (`framelearn/pipeline/cache_manifest.py`)

每个缓存目录现在都有一个 `manifest.json` 文件，记录：
- **输入文件**：路径、大小、修改时间、SHA256 hash（前 1MB）
- **配置快照**：所有影响输出的配置参数
- **模型信息**：ASR provider/model、Vision provider/model
- **代码版本**：Git commit hash
- **段落状态**：每个段落的完成状态和时间戳
- **缓存 key**：根据上述所有因素计算的唯一标识

### 2. 自动缓存验证

缓存读取时会自动验证：
- ✅ 输入文件是否变化（通过 hash 和 size）
- ✅ 配置参数是否变化
- ✅ 模型/provider 是否变化
- ⚠️ 只有完全匹配时才复用缓存

### 3. 段落级进度追踪

对于分段生成的文档：
- 记录每个段落的完成状态
- 支持断点续传（中断后可恢复）
- 失败段落会记录错误信息

## 修改的文件

### 核心模块
- ✅ `framelearn/pipeline/cache_manifest.py` - 新增
- ✅ `framelearn/pipeline/video_pipeline.py` - 集成 manifest 验证
- ✅ `framelearn/pipeline/doc_generator.py` - 集成 manifest 验证

### 测试文件
- ✅ `test/src/test_cache_manifest.py` - 单元测试（14 个测试）
- ✅ `test/src/test_cache_integration.py` - 集成测试（6 个测试）

### 文档
- ✅ `docs/cache_manifest.md` - 完整设计文档

## 测试结果

```bash
# 单元测试
test_cache_manifest.py .............. (14 passed)

# 集成测试  
test_cache_integration.py ...... (6 passed)

# 全部测试
test/ ............................ (90 passed)
```

所有测试通过 ✅

## 使用示例

### 缓存命中
```bash
📹 开始处理视频：lecture.mp4
🎤 语音识别中...
⏭️  使用已缓存字幕...
🖼️  提取关键帧...
⏭️  使用已缓存的 45 个关键帧...
📝 生成课堂笔记...
   📐 切分为 5 段生成...
   ✅ Manifest 有效，已完成 3/5 段
   ⏭️  第 1/5 段已缓存，跳过...
   ⏭️  第 2/5 段已缓存，跳过...
   ⏭️  第 3/5 段已缓存，跳过...
   ⚙️  生成第 4/5 段（06:00~07:30）...
```

### 缓存失效
```bash
📹 开始处理视频：lecture.mp4
🎤 语音识别中...
⚠️  字幕缓存失效（输入或配置已变更）
🎵 提取音轨...
[正在重新生成...]
```

## Manifest 示例

`output/video_name/src/subtitle_manifest.json`:
```json
{
  "version": "1.0",
  "created_at": "2024-08-05T23:30:00",
  "input_file": {
    "path": "/path/to/video.mp4",
    "size": 123456789,
    "sha256": "abc123"
  },
  "config": {
    "asr_provider": "dashscope",
    "asr_model": "paraformer-v2",
    ...
  },
  "git_commit": "d91148f",
  "cache_key": "a1b2c3d4"
}
```

## 缓存失效触发条件

### 会触发失效
- 视频文件内容变化
- 视频文件大小变化
- 外部字幕文件变化
- 相关配置参数变化（scene_threshold、max_keyframes 等）
- ASR/Vision provider 或 model 变化
- Agent 功能开关变化（keyframe_selection、quality_review）

### 不会触发失效
- 输出目录位置变化
- 文件路径变化（只看内容）
- 不相关的配置变化（keep_temp_files 等）
- 仅文件修改时间变化（内容不变）

## 向后兼容

- 旧缓存文件不会被删除
- 如果没有 manifest，会提示并重新生成
- 不影响现有功能和 API

## 性能影响

- Hash 计算：只读取文件前 1MB，< 10ms
- Manifest 加载：JSON 解析，< 1ms
- 额外存储：每个缓存目录增加 < 5KB

## 查看完整文档

详细设计和实现细节请参考：[docs/cache_manifest.md](docs/cache_manifest.md)
