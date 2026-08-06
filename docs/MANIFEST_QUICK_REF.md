# 缓存 Manifest 快速参考

## 📝 什么是 Manifest？

Manifest 是一个 JSON 文件，记录缓存的完整来源信息，用于验证缓存是否仍然有效。

## 📍 Manifest 位置

| 缓存类型 | Manifest 路径 |
|---------|--------------|
| 字幕 | `output/video_name/src/subtitle_manifest.json` |
| 关键帧 | `output/video_name/src/keyframe_manifest.json` |
| 文档段落 | `output/video_name/segments_{mode}/manifest.json` |

## 🔍 查看 Manifest

```bash
# 查看整个目录的所有 manifest
python -m framelearn.tools.inspect_manifest output/my_video

# 查看单个 manifest
python -m framelearn.tools.inspect_manifest output/my_video/src/subtitle_manifest.json
```

## 📊 Manifest 内容

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
    "scene_threshold": 0.3,
    "asr_provider": "dashscope",
    "vision_model": "Qwen/Qwen2.5-VL-72B-Instruct",
    ...
  },
  "git_commit": "d91148f",
  "cache_key": "a1b2c3d4"
}
```

## ✅ 缓存有效条件

缓存只有在以下情况都不变时才有效：
- ✅ 输入文件内容（hash + size）
- ✅ 相关配置参数
- ✅ 模型/provider
- ✅ 代码版本（git commit）

## ⚠️ 缓存失效场景

### 会导致失效
- 视频文件内容变化
- 配置改变（如 `max_keyframes: 100 → 200`）
- 模型切换（如 `dashscope → siliconflow`）
- Agent 功能开关（如 `quality_review: false → true`）

### 不会导致失效
- 文件路径变化
- 输出目录变化
- 文件修改时间变化（内容不变）

## 🔧 常见问题

### Q: 如何强制重建缓存？

**方法 1**: 删除 manifest 文件
```bash
rm output/my_video/src/subtitle_manifest.json
```

**方法 2**: 删除整个缓存目录
```bash
rm -rf output/my_video/src/
```

### Q: 缓存在哪里？

```
output/video_name/
├── src/                    # 源文件缓存
│   ├── subtitle.*          # 字幕
│   ├── frame_*.jpg         # 关键帧
│   └── *_manifest.json     # ← Manifest 在这里
├── segments_notes/         # 笔记版段落
│   └── manifest.json       # ← Manifest 在这里
└── segments_visual_script/ # 讲稿版段落
    └── manifest.json       # ← Manifest 在这里
```

### Q: Manifest 损坏了怎么办？

系统会自动提示并重建：
```
⚠️  字幕 manifest 损坏
🎵 提取音轨...
[重新生成...]
✅ 字幕 manifest 已保存
```

### Q: 如何查看缓存是否被使用？

看日志输出：
```bash
⏭️  使用已缓存字幕...          # ← 缓存命中
✅ Manifest 有效，已完成 3/5 段  # ← 部分缓存命中
⚠️  缓存失效（输入或配置已变更） # ← 缓存失效
```

### Q: 段落生成中断了怎么办？

重新运行即可，系统会自动恢复：
```
📐 切分为 5 段生成...
✅ Manifest 有效，已完成 3/5 段
⏭️  第 1/5 段已缓存，跳过...
⏭️  第 2/5 段已缓存，跳过...
⏭️  第 3/5 段已缓存，跳过...
⚙️  生成第 4/5 段...            # ← 从中断处继续
```

### Q: 性能影响有多大？

几乎可忽略：
- Hash 计算: < 10ms
- Manifest 验证: < 1ms
- 总开销: < 20ms

相比 ASR（数十秒）、文档生成（数分钟），完全可以忽略。

## 📚 更多信息

- **完整设计**: [cache_manifest.md](cache_manifest.md)
- **架构图**: [cache_architecture.md](cache_architecture.md)
- **完成总结**: [../mine/antivibe/CACHE_FIX_SUMMARY.md](../mine/antivibe/CACHE_FIX_SUMMARY.md)

## 🧪 测试

```bash
# 运行所有测试
python -m pytest test/src/ -v

# 只测试 manifest 功能
python -m pytest test/src/test_cache_manifest.py -v
python -m pytest test/src/test_cache_integration.py -v
```

## 💡 提示

- Manifest 文件很小（< 5KB），可以直接用文本编辑器打开查看
- Cache key 是 16 位十六进制字符串，用于快速比对
- 段落 manifest 中的 `segments_completed` 数组记录了每个段落的状态
- 失败的段落会记录 `error` 信息，方便调试

---

**快速开始**: 正常使用 `framelearn run video.mp4`，系统会自动创建和验证 manifest！
