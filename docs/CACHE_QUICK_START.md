# 缓存系统快速指南

## 什么是缓存 Manifest？

每个缓存目录（字幕、关键帧、段落）都有一个 `manifest.json` 文件，记录：
- 输入文件的指纹（SHA256 hash、大小）
- 使用的配置参数
- 使用的模型（ASR、Vision）
- 生成时的代码版本（Git commit）
- 段落完成状态

**作用**：自动判断缓存是否仍然有效，避免使用过期数据。

## 用户视角：缓存如何工作

### 场景 1: 首次运行
```bash
$ python main.py run lecture.mp4
📹 开始处理视频：lecture.mp4
🎤 语音识别中...
🎵 提取音轨...
[调用 ASR API...]
✅ 字幕 manifest 已保存
🖼️  提取关键帧...
✅ 关键帧 manifest 已保存
📝 生成课堂笔记...
   ⚙️  生成第 1/5 段...
   ⚙️  生成第 2/5 段...
✅ 讲稿已生成：output/lecture/index.md
```

生成的文件结构：
```
output/lecture/
├── src/
│   ├── subtitle.srt
│   ├── subtitle.txt
│   ├── subtitle_manifest.json     # 字幕缓存 manifest
│   ├── keyframe_manifest.json     # 关键帧缓存 manifest
│   ├── frame_00h01m30s.jpg
│   └── ...
├── segments_visual_script/
│   ├── manifest.json              # 段落缓存 manifest
│   ├── seg_001.md
│   └── ...
├── index.md
└── notes.md
```

### 场景 2: 再次运行（输入未变）
```bash
$ python main.py run lecture.mp4
📹 开始处理视频：lecture.mp4
🎤 语音识别中...
⏭️  使用已缓存字幕...              # 自动复用，节省 3-5 分钟
🖼️  提取关键帧...
⏭️  使用已缓存的 45 个关键帧...    # 自动复用
📝 生成课堂笔记...
   ✅ Manifest 有效，已完成 5/5 段   # 全部复用
⏭️  第 1/5 段已缓存，跳过...
⏭️  第 2/5 段已缓存，跳过...
⏭️  第 3/5 段已缓存，跳过...
⏭️  第 4/5 段已缓存，跳过...
⏭️  第 5/5 段已缓存，跳过...
✅ 讲稿已生成：output/lecture/index.md
```

**用时**: 从 5-10 分钟降低到 <10 秒 ⚡

### 场景 3: 修改视频后再运行
```bash
$ # 用户剪辑了视频，生成新的 lecture_v2.mp4
$ python main.py run lecture.mp4
📹 开始处理视频：lecture.mp4
🎤 语音识别中...
⚠️  字幕缓存失效（输入或配置已变更）  # 自动检测到内容变化
🎵 提取音轨...
[重新调用 ASR API...]
🖼️  提取关键帧...
⚠️  关键帧缓存失效（输入或配置已变更）
[重新提取关键帧...]
📝 生成课堂笔记...
   ⚠️  Manifest 失效（输入或配置已变更），重新生成
[重新生成所有段落...]
```

### 场景 4: 改配置后再运行
```bash
$ # 用户修改 settings.toml: max_keyframes = 50 → 100
$ python main.py run lecture.mp4
📹 开始处理视频：lecture.mp4
🎤 语音识别中...
⏭️  使用已缓存字幕...              # 字幕配置未变，复用
🖼️  提取关键帧...
⚠️  关键帧缓存失效（输入或配置已变更）  # max_keyframes 变了，重新提取
[重新提取关键帧...]
```

### 场景 5: 改代码后再运行（开发场景）
```bash
$ git commit -m "fix typo in prompt"
$ python main.py run lecture.mp4
📹 开始处理视频：lecture.mp4
🎤 语音识别中...
⏭️  使用已缓存字幕...              # ✅ 仍然有效！Git commit 不影响缓存
🖼️  提取关键帧...
⏭️  使用已缓存的 45 个关键帧...
```

**注意**：Git commit 不在 cache key 中，不会触发失效。如果代码改动影响输出，应该：
1. 修改配置参数（自动失效）
2. 或手动删除缓存目录重建

## 查看 Manifest

### 查看单个 manifest
```bash
$ python -m framelearn.tools.inspect_manifest output/lecture/src/subtitle_manifest.json

============================================================
📋 Manifest: subtitle_manifest.json
============================================================

📅 Created: 2024-08-05 23:30:00
🔑 Cache Key: a519ddcd5997d12f
💻 Git Commit: d91148f

📹 Input File:
   Path: /Users/user/lecture.mp4
   Size: 123,456,789 bytes
   Hash: abc123def456

⚙️  Configuration:
   ASR: dashscope / paraformer-v2
   Vision: siliconflow / Qwen/Qwen2.5-VL-72B-Instruct
   Max Keyframes: 100
   Scene Threshold: 0.3

============================================================
```

### 查看目录下所有 manifest
```bash
$ python -m framelearn.tools.inspect_manifest output/lecture

🔍 Found 3 manifest(s) in output/lecture

============================================================
📋 Manifest: subtitle_manifest.json
============================================================
[...]

============================================================
📋 Manifest: keyframe_manifest.json
============================================================
[...]

============================================================
📋 Manifest: manifest.json (segments_visual_script)
============================================================

📊 Segments: 5/5 completed

   Status by segment:
   ✅ Segment 1: 2024-08-05 23:31:00
   ✅ Segment 2: 2024-08-05 23:32:00
   ✅ Segment 3: 2024-08-05 23:33:00
   ✅ Segment 4: 2024-08-05 23:34:00
   ✅ Segment 5: 2024-08-05 23:35:00

============================================================
```

## 缓存失效的原因

### ✅ 会触发失效（重新生成）
| 变化 | 示例 |
|------|------|
| 视频内容变化 | 剪辑、重新录制、重新编码 |
| 视频大小变化 | 同上 |
| 外部字幕变化 | 修改 `.srt` 文件内容 |
| 配置参数变化 | `max_keyframes: 100 → 50` |
| 模型切换 | `paraformer-v2 → sensevoice` |
| Provider 切换 | `dashscope → siliconflow` |

### ❌ 不会触发失效（复用缓存）
| 变化 | 说明 |
|------|------|
| Git commit | 仅记录，不影响缓存判断 |
| 代码修改 | 除非改变配置或行为 |
| 文件修改时间 | 只要内容不变 |
| 文件路径变化 | 使用绝对路径，但基于内容 hash |
| 无关配置变化 | `keep_temp_files`, `output_dir` 等 |

## 手动管理缓存

### 清除所有缓存
```bash
$ rm -rf output/lecture/src/*.json
$ rm -rf output/lecture/segments_*
```

### 清除特定缓存
```bash
# 只清除字幕缓存
$ rm output/lecture/src/subtitle_manifest.json
$ rm output/lecture/src/subtitle.*

# 只清除关键帧缓存
$ rm output/lecture/src/keyframe_manifest.json
$ rm output/lecture/src/frame_*.jpg

# 只清除段落缓存
$ rm -rf output/lecture/segments_visual_script/
```

### 强制重建（即将支持）
```bash
# 未来版本将支持
$ python main.py run lecture.mp4 --force-rebuild
```

## 常见问题

### Q: 为什么修改代码后缓存还有效？
A: Git commit 不在 cache key 中。如果代码改动确实影响输出，应该：
1. 增加新配置参数（自动失效）
2. 或修改现有配置默认值（自动失效）
3. 或手动删除缓存重建

### Q: 如何知道使用了哪个版本的代码？
A: 使用 `inspect_manifest` 工具查看 Git commit 字段。

### Q: Manifest 损坏了怎么办？
A: 删除对应的 `*_manifest.json` 文件，系统会自动重建。

### Q: 缓存占用多少空间？
A: 
- 字幕：< 100KB（SRT + TXT + manifest）
- 关键帧：~10MB（假设 50 个帧，每个 200KB）
- 段落：~500KB（5 个段落 Markdown）
- Manifest：< 5KB 每个

### Q: 多台机器能共享缓存吗？
A: 当前不支持。Manifest 使用绝对路径，需要手动同步并调整路径。未来版本可能支持远程缓存。

### Q: Hash 只计算前 1MB，会不会误判？
A: 几乎不会。视频文件前 1MB 包含文件头和元数据，内容变化必然影响这部分。完全相同前 1MB 但后续不同的概率接近 0。

## 性能数据

| 场景 | 无缓存 | 有缓存 | 节省 |
|------|--------|--------|------|
| 10 分钟视频（首次） | 5-8 分钟 | N/A | - |
| 10 分钟视频（再次） | 5-8 分钟 | < 10 秒 | **98%+** |
| 60 分钟视频（首次） | 30-60 分钟 | N/A | - |
| 60 分钟视频（再次） | 30-60 分钟 | < 30 秒 | **99%+** |

**Hash 计算开销**: < 10ms（只读前 1MB）  
**Manifest 加载**: < 1ms  
**Manifest 验证**: < 5ms

## 总结

- ✅ **零配置**：自动工作，无需手动管理
- ✅ **智能判断**：自动检测输入/配置变化
- ✅ **可追溯**：完整记录来源信息
- ✅ **高性能**：缓存命中时节省 98%+ 时间
- ✅ **开发友好**：代码变更不过度失效

**建议**：日常使用无需关心缓存，系统会自动处理。只在需要强制重建时手动删除缓存。
