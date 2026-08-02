# FrameLearn 流水线实现说明

本文聚焦 `framelearn/pipeline/` 的当前运行行为。整体架构和 CLI 边界见 [architecture.md](architecture.md)。

## 1. 输入与缓存判定

`VideoPipeline(video_path, output_dir=None, subtitle_path=None)` 只接收本地视频。

每次运行会创建：

```text
output/<视频 stem>/src/
```

字幕来源按以下优先级选择：

1. 显式 `--subtitle`；
2. 同一输出目录中同时存在的 `src/subtitle.srt` 和 `src/subtitle.txt`；
3. 从视频或伴随音频中提取音轨后调用 ASR。

关键帧来源：

1. 若 `src/frame_*.jpg` 已存在，解析文件名中的整秒时间戳并复用；
2. 否则重新抽帧、去重并复制到 `src/`。

文档分段缓存位于 `segments_<mode>/seg_NNN.md`。只要文件存在就会复用，不会检查 prompt、模型或配置是否变化。

## 2. 音轨与 ASR

### 音轨提取

`FFmpegHelper.extract_audio()`：

- 视频有音轨时，转为 16 kHz AAC/m4a；
- 视频无音轨时，查找同目录同名或同前缀的 `.mp3`、`.m4a`、`.aac`；精确匹配还接受 `.wav`；
- 未找到伴随音频则终止流水线。

### DashScope

默认 `settings.toml` 使用 DashScope：

```text
audio.m4a
  → 按 asr.chunk_duration 切片
  → OSS 上传并签名
  → 并发提交异步 ASR
  → 串行轮询各任务
  → 解析 sentence begin/end
  → 加回 chunk.start_sec
  → 生成 subtitle.srt
```

实现包含：

- 切片文件复用；
- `asr_checkpoint.json` 原子写入；
- 已完成任务恢复；
- 429/5xx 退避；
- OSS 对象尽力清理。

`settings.toml` 中的 `asr.overlap` 当前没有被 DashScope 实现读取，切片之间也没有重叠去重。

### SiliconFlow

完整音频以 multipart 请求上传到 SenseVoice。输出只有纯文本，没有时间戳和 SRT。

## 3. 字幕清洗

`SubtitleCleaner.clean()` 当前执行：

1. 删除 `[]`、`【】`、`()`、`（）` 内的内容；
2. 将一组中文标点替换为 ASCII 标点；
3. 合并完全相同的连续行；
4. 在句末标点后换行；
5. 压缩多余换行和空格。

它不会进行通用口水词过滤；口水词处理主要依赖文档生成 prompt，以及可选质量检查中的少量词频规则。

`strip_timestamps()` 可把 SRT/VTT 转为纯文本，但显式传入 `.vtt` 时不会把 VTT 原文保存为 SRT，也不会提供给 `SegmentSplitter` 做精确时间分段。

## 4. 关键帧提取

`extract_keyframes()` 会同时运行：

- 场景检测：`select='gt(scene,<threshold>)',showinfo`；
- 固定间隔采样：从 0 秒开始，每 `fallback_interval` 秒截一帧。

随后两组帧：

1. 从 FFmpeg 输出解析时间戳；
2. 重命名为 `frame_HHhMMmSSs.jpg`；
3. 合并并按时间排序；
4. 截断到调用方要求的上限。

流水线先请求 `video.max_keyframes * 2` 张，再用 `KeyframeDeduplicator` 压缩到 `video.max_keyframes`。

### pHash 去重

相似度计算为：

```text
similarity = 1 - hamming_distance / 64
```

`similarity > 0.9` 时认为重复。比较对象是所有已保留帧，而不是仅比较相邻帧。

### 可选 Agent 补帧

`agent.keyframe_selection = true` 时，仅处理 `TranscriptResult.segments` 中有时间戳的段落：

1. 视觉关键词预筛；
2. LLM 决定是否截图；
3. 在段落开始时间截图；
4. LLM 评估图片；
5. 与已有帧在 ±2 秒内去重。

它保留基础抽帧结果，只添加或删除新补的帧。当前 API 图像调用存在未实现类引用，建议保持关闭。

## 5. 文档分段

`DocumentGenerator.generate()` 仅在以下情况分段：

```text
len(cleaned_subtitle) > 8000
或
len(keyframes) > 20
```

### 有 SRT

`_split_by_srt()` 解析 `HH:MM:SS,mmm --> HH:MM:SS,mmm`，按“当前条目的结束时间 - 本段起始时间”达到配置时长后封段。关键帧按闭区间分配：

```text
segment.start_time <= frame_timestamp <= segment.end_time
```

位于边界的帧理论上可能被相邻两段同时选中。

### 无 SRT

`_split_by_chars()` 固定按 4 字/秒估算，不寻找句子或段落边界，因此可能在词语或代码中间切开。

## 6. 生成、重试和质量检查

流水线每次调用生成器两次：

1. `mode="notes"` → `notes.md`；
2. `mode=doc_generation.mode` → `index.md`。

如果主模式也是 `notes`，同一内容会生成两遍并写入两个文件。

### 普通重试

分段模式下，每段捕获所有异常，最多尝试 3 次，等待 15、30、45 秒。短输入没有同层重试，只将错误包装为 `RuntimeError`。

### 质量评审

`agent.quality_review = true` 时，评审是本地启发式检查：

- 文稿长度是否小于 100；
- 指定口水词是否出现超过 3 次；
- 字幕含视觉提示但文稿没有 Markdown 图片。

失败后最多重生成 3 次；第三次可传入 `agent.upgrade_model`。但 app-server 后端不接受 `model_override`，所以升级模型只对 API 后端有效。最终失败时返回原始字幕。

## 7. Vision API 与 app-server

### API 模式

- provider/model 读取 `runtime.vision_provider` 和 `runtime.vision_model`；
- SiliconFlow 密钥读取 `SILICONFLOW_API_KEY`；其他 provider 读取 `VISION_API_KEY`；
- 图片读取为 base64 data URL；
- 单次调用 `max_tokens=8192`、`timeout=300`。

每段 prompt 最多列出前 20 张帧，但 API 实际会发送传入的全部帧。正常分段配置将帧数限制为每段 10 张。

### app-server 模式

`AppServerSession.run_turn(text)` 构造：

```json
{"input": [{"type": "text", "text": "..."}]}
```

因此模型只看到 prompt 中的时间戳和文件名，不会收到本地图片像素。若 Codex 通过工具写入 Markdown，生成器尝试读取 `fileChange` 事件中的 `.md`；否则使用 `final_text`。

## 8. 输出

```text
output/<视频名>/
├── index.md
├── notes.md
├── src/
│   ├── subtitle.txt
│   ├── subtitle.srt          # 有 SRT 时
│   └── frame_00h00m30s.jpg
├── segments_notes/
├── segments_visual_script/   # 或 segments_textbook/
└── temp/                     # 取决于 ASR 配置
```

`PipelineResult.keyframes` 只返回图片路径，不包含时间戳；时间戳编码在文件名中。

## 9. 当前最重要的工程边界

- 缓存没有输入指纹，配置变化后可能复用旧结果。
- 固定间隔帧不是失败 fallback，而是始终采样，长视频会产生较多 FFmpeg 子进程。
- 帧名只保留整秒，同秒的场景帧和定时帧可能冲突。
- 字数分段不是语义分段。
- 质量评审不是 LLM 审稿。
- app-server 文档生成当前不是视觉输入。
- 完整端到端路径依赖外部 API 和 FFmpeg，单元测试主要通过 mock 验证。
