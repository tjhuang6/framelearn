# 设计：博客锚点流水线（blog-anchor-pipeline）

## 模块职责

| 模块 | 职责 |
|------|------|
| `srt_chunker.py` | 继续只处理 raw SRT，按 `segment_minutes` 切 chunk |
| `frame_distributor.py` | 把启发式候选帧按 timestamp 分配到 chunk |
| `blog_generator.py` | 文本模型：annotated SRT chunk → `blog_markdown` + `frame_requests` |
| `vision_frame_evaluator.py` | 视觉模型：对每张候选帧输出验图决策，支持 retake 循环 |
| `chunked_doc_generator.py` | 编排：切块 → 插标记 → 生成 → 校验锚点 → 补截 → 验图 → 拼装 |
| `md_assembler.py` | 替换 `[[FRAME:...]]` 锚点，生成 `blog.md` 与 `srt_picture.md` |

## Annotated SRT chunk（方案 A）

程序**先切块**，再对每个 chunk 生成 BlogGenerator 输入：

```text
<SRT_MD>
00:00:51,000 --> 00:00:56,000
第3层这个数要和这个值去相乘。

![picture 1 @ 53.0s](src/frame_00h00m53s000ms_interval_005.jpg)

00:00:56,000 --> 00:00:59,000
接下来看 padding。
</SRT_MD>
```

规则：

- 图片标记按 `timestamp_sec` 插入到该 chunk 内最近的字幕段之后；
- 文本模型可以引用图片路径，但**不能修改已有图片的真实时间戳**；
- 文本模型需要新图时，在 `frame_requests` 中给 `request_type="new_capture"` 和精准 `timestamp`。

## BlogGenerator 输出 schema

文本模型输出一个 JSON 对象：

```json
{
  "blog_markdown": "卷积层负责提取局部特征。\n[[FRAME:a1@53.0]]\n实际工程中更常用 3x3 卷积。\n[[FRAME:a2@633.8]]",
  "frame_requests": [
    {
      "anchor_id": "a1",
      "srt_id": 12,
      "timestamp": 53.0,
      "request_type": "reuse",
      "source_frame_path": "src/frame_00h00m53s000ms_interval_005.jpg",
      "reason": "这里展示卷积核滑动"
    },
    {
      "anchor_id": "a2",
      "srt_id": 13,
      "timestamp": 633.8,
      "request_type": "new_capture",
      "source_frame_path": null,
      "reason": "这里需要代码截图，启发式帧不够准确"
    }
  ]
}
```

约束：

- `anchor_id` 在 chunk 内唯一；
- `[[FRAME:...]]` 中的 `anchor_id@timestamp` 必须与 `frame_requests` 一致；
- `request_type="reuse"` 时 `source_frame_path` 必须是 annotated SRT 中出现过的真实路径；
- `request_type="new_capture"` 时 `source_frame_path` 必须为 null；
- `srt_id` 是 chunk 内 1-based 字幕序号。

## 锚点校验与帧绑定

对每个 `frame_request` 依次执行：

```text
request_type == "reuse"
  ├─ source_frame_path 在候选帧集合中
  │    → 直接绑定该真实帧与它的真实 timestamp
  └─ 否则 → 非法锚点，删除并记录

request_type == "new_capture"
  ├─ timestamp 在 [chunk.start_sec, chunk.end_sec + tolerance] 内
  │    ├─ 候选帧中存在 |frame.timestamp - timestamp| <= frame_match_tolerance
  │    │    → 绑定该候选帧
  │    └─ 否则 → FFmpeg.capture_single_frame(timestamp)
  └─ 否则 → 非法锚点，删除并记录
```

配置：

```toml
[blog_gen]
frame_match_tolerance = 2.0   # 秒
max_retakes = 1               # 视觉验图 retake 上限
```

## VisionFrameEvaluator 输入

对每个锚点绑定的候选帧，发送：

```text
锚点：[[FRAME:a1@53.0]]
上下文字幕段：
12. 第3层这个数要和这个值去相乘。

图片：src/frame_00h00m53s000ms_interval_005.jpg
```

输出：

```json
{
  "anchor_id": "a1",
  "frame": "src/frame_00h00m53s000ms_interval_005.jpg",
  "retake": false,
  "retake_timestamp": null,
  "keep_image": true,
  "content_type": "diagram",
  "caption": "卷积核在输入特征图上滑动",
  "text_representation": ""
}
```

### content_type 枚举

```text
text_slide, terminal, code, diagram, formula, table, screenshot,
face, blank, transition, other
```

### retake 循环

```text
retake == true
  → FFmpeg 按 retake_timestamp 重新截帧
  → 新帧再次交给 VisionFrameEvaluator
  → 最多 max_retakes 次
  → 超限：记录 fallback，keep_image=true（保守保留）
```

## MDAssembler 规则

`keep_image == true`：

```text
content_type 任意
  → 插入图片
  → caption 非空时，图片下插入 caption
  → text_representation 非空时，继续插入该文字内容
```

`keep_image == false`：

```text
删除锚点，不插入任何内容
```

`blog.md` 替换顺序：

1. 遍历 `blog_markdown`，按 `anchor_id` 查决策；
2. 锚点存在且 `keep_image=true` → 替换为图片 Markdown；
3. 锚点存在且 `keep_image=false` → 删除锚点；
4. 锚点不存在 → 删除并记录非法锚点。

`srt_picture.md`：

- 使用 raw SRT 全量 segment 顺序；
- 把保留图片插入到决策对应 `srt_id` 的段后；
- 多条图片按 chunk 内顺序排列。

## 降级策略

| 失败点 | 降级 |
|--------|------|
| 启发式截帧失败 | `frames=[]`，BlogGenerator 只能请求 `new_capture` |
| BlogGenerator LLM 失败 | 该 chunk 的 blog 降级为原始字幕拼接，无锚点 |
| BlogGenerator schema 非法 | 重试 2 次后同上 |
| 锚点非法 | 删除该锚点并记录 |
| 补截失败 | 删除该锚点并记录 |
| VisionFrameEvaluator LLM 失败 | `keep_image=true, content_type="other"` 保守保留 |
| retake 超限 | `keep_image=true` 保守保留 |
