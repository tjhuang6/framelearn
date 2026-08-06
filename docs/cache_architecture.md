# 缓存 Manifest 系统架构

## 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                     Video Pipeline                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │   1. 检查字幕缓存              │
              │   - subtitle.txt/srt          │
              │   - subtitle_manifest.json    │
              └───────────────┬───────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  Manifest 验证     │
                    │  ✓ 输入文件 hash   │
                    │  ✓ ASR 配置        │
                    │  ✓ Git commit     │
                    └─────────┬─────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                ✅ 有效            ❌ 失效
                    │                   │
                    ▼                   ▼
            ┌───────────┐       ┌──────────────┐
            │ 复用缓存   │       │ 重新生成 ASR │
            └───────────┘       │ + 创建 manifest│
                                └──────────────┘
                              
              ┌───────────────────────────────┐
              │   2. 检查关键帧缓存            │
              │   - frame_*.jpg               │
              │   - keyframe_manifest.json    │
              └───────────────┬───────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  Manifest 验证     │
                    │  ✓ 输入文件 hash   │
                    │  ✓ 关键帧配置      │
                    │  ✓ Git commit     │
                    └─────────┬─────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                ✅ 有效            ❌ 失效
                    │                   │
                    ▼                   ▼
            ┌───────────┐       ┌──────────────┐
            │ 复用缓存   │       │ 重新提取帧   │
            └───────────┘       │ + 创建 manifest│
                                └──────────────┘

              ┌───────────────────────────────┐
              │   3. 文档生成（分段）          │
              │   - segments_*/seg_*.md       │
              │   - segments_*/manifest.json  │
              └───────────────┬───────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  Manifest 验证     │
                    │  ✓ 输入文件 hash   │
                    │  ✓ Vision 配置     │
                    │  ✓ 文档模式        │
                    │  ✓ Git commit     │
                    └─────────┬─────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                ✅ 有效            ❌ 失效
                    │                   │
                    ▼                   ▼
        ┌───────────────────┐   ┌──────────────┐
        │ 获取已完成段落    │   │ 清空旧缓存   │
        │ {0, 2, 3, ...}    │   │ 创建新 manifest│
        └─────────┬─────────┘   └──────────────┘
                  │
                  ▼
        ┌─────────────────────┐
        │  For each segment:  │
        ├─────────────────────┤
        │  if completed:      │
        │    skip (复用缓存)   │
        │  else:              │
        │    generate         │
        │    mark_completed() │
        └─────────────────────┘
```

## Manifest 数据流

```
┌──────────────┐
│ 输入文件      │  video.mp4, subtitle.srt
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 计算元数据    │  path, size, mtime, sha256 (1MB)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 配置快照      │  scene_threshold, max_keyframes, ...
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Git commit   │  git rev-parse HEAD
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Cache Key    │  SHA256(input|config|git)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ manifest.json│  保存到 src/ 或 segments_*/
└──────────────┘
```

## Cache Key 计算

```
┌─────────────────────────────────────────────────┐
│  Cache Key Components                           │
├─────────────────────────────────────────────────┤
│                                                 │
│  1. Input File                                  │
│     input:abc123def456:123456789                │
│           └─ sha256  └─ size                    │
│                                                 │
│  2. Subtitle File (optional)                    │
│     subtitle:xyz789:12345                       │
│                                                 │
│  3. Config Snapshot                             │
│     config:e4f5g6h7                             │
│            └─ SHA256 of JSON(config)            │
│                                                 │
│  4. Git Commit (optional)                       │
│     git:d91148f                                 │
│         └─ first 8 chars                        │
│                                                 │
└─────────────────────────────────────────────────┘
                       │
                       ▼
        input:abc123:123456789|config:e4f5g6h7|git:d91148f
                       │
                       ▼ SHA256
                       │
                   a1b2c3d4e5f6 (16 chars)
```

## 缓存失效决策树

```
                   缓存存在？
                       │
          ┌────────────┴────────────┐
         NO                        YES
          │                          │
          ▼                          ▼
      重新生成                  manifest 存在？
                                    │
                       ┌────────────┴────────────┐
                      NO                        YES
                       │                          │
                       ▼                          ▼
                   重新生成                  加载 manifest
                                                  │
                                                  ▼
                                         计算当前 cache key
                                                  │
                                                  ▼
                                         key 匹配？
                                                  │
                                     ┌────────────┴────────────┐
                                    YES                        NO
                                     │                          │
                                     ▼                          ▼
                               ✅ 复用缓存              ⚠️ 缓存失效
                                                              │
                                                              ▼
                                                          重新生成
                                                              │
                                                              ▼
                                                      创建新 manifest
```

## 段落进度恢复流程

```
启动文档生成
     │
     ▼
segments_*/manifest.json 存在？
     │
     ├─ NO ─→ 创建新 manifest (segments_total=N)
     │
     └─ YES ─→ 加载 manifest
                    │
                    ▼
               验证 cache key
                    │
         ┌──────────┴──────────┐
        有效                  失效
         │                      │
         ▼                      ▼
  获取已完成段落          清空 seg_*.md
  {0, 2, 3, ...}          删除旧 manifest
         │                      │
         └──────────┬───────────┘
                    ▼
            For i in 0..N-1:
                    │
         ┌──────────┴──────────┐
         │                     │
      i in completed?      i not in completed
         │                     │
         ▼                     ▼
    跳过，读取缓存         生成 seg_i
         │                     │
         │                     ▼
         │              mark_completed(i)
         │                     │
         │                     ▼
         │             保存 seg_i.md
         │                     │
         └──────────┬──────────┘
                    ▼
               合并所有段落
```

## 目录结构

```
output/
└── video_name/
    ├── src/
    │   ├── subtitle.txt               # 字幕文本
    │   ├── subtitle.srt               # 时间轴字幕
    │   ├── subtitle_manifest.json     # 字幕 manifest ⭐
    │   ├── frame_00h00m15s.jpg
    │   ├── frame_00h01m30s.jpg
    │   ├── ...
    │   └── keyframe_manifest.json     # 关键帧 manifest ⭐
    │
    ├── segments_notes/
    │   ├── manifest.json              # 笔记版 manifest ⭐
    │   ├── seg_001.md
    │   ├── seg_002.md
    │   └── ...
    │
    ├── segments_visual_script/
    │   ├── manifest.json              # 讲稿版 manifest ⭐
    │   ├── seg_001.md
    │   ├── seg_002.md
    │   └── ...
    │
    ├── notes.md                       # 最终笔记
    └── index.md                       # 最终讲稿
```

## Manifest 文件示例

### subtitle_manifest.json
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
  "cache_key": "a1b2c3d4"
}
```

### keyframe_manifest.json
```json
{
  "version": "1.0",
  "input_file": {...},
  "config": {
    "scene_threshold": 0.3,
    "max_keyframes": 100,
    ...
  },
  "cache_key": "e5f6g7h8"
}
```

### segments_*/manifest.json
```json
{
  "version": "1.0",
  "input_file": {...},
  "config": {
    "doc_mode": "visual_script",
    "vision_provider": "siliconflow",
    "vision_model": "Qwen/Qwen2.5-VL-72B-Instruct",
    ...
  },
  "segments_total": 5,
  "segments_completed": [
    {"index": 0, "completed": true, ...},
    {"index": 1, "completed": true, ...},
    {"index": 2, "completed": false, "error": "timeout", ...}
  ],
  "cache_key": "i9j0k1l2"
}
```

## 性能影响分析

```
操作                    耗时        影响
─────────────────────────────────────────
计算 SHA256 (1MB)      < 10ms      可忽略
加载 manifest JSON     < 1ms       可忽略
验证 cache key         < 1ms       可忽略
保存 manifest JSON     < 5ms       可忽略
─────────────────────────────────────────
总额外开销             < 20ms      可忽略
```

相比 ASR（数十秒）、关键帧提取（数秒）、文档生成（数分钟），manifest 开销完全可忽略。

## 总结

Manifest 系统通过三层缓存（字幕、关键帧、文档段落）实现：
1. ✅ 可追溯：记录完整来源信息
2. ✅ 可验证：自动校验缓存有效性
3. ✅ 可恢复：段落级断点续传
4. ✅ 低开销：< 20ms 额外耗时
5. ✅ 零配置：自动集成，无需用户干预
