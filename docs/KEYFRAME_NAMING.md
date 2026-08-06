# 关键帧文件命名规范

## 问题背景

旧版命名仅保留整秒精度（`frame_00h01m30s.jpg`），当场景检测帧和固定间隔帧落在同一秒时，`Path.rename()` 会导致：
- 文件覆盖
- 重命名失败
- 多个 tuple 指向同一文件

## 新命名格式

```
frame_{HH}h{MM}m{SS}s{MMM}ms_{source}_{seq}.jpg
```

### 格式说明

- `{HH}h{MM}m{SS}s{MMM}ms`: 时间戳（毫秒精度）
  - `HH`: 小时（00-99）
  - `MM`: 分钟（00-59）
  - `SS`: 秒（00-59）
  - `MMM`: 毫秒（000-999）
- `{source}`: 帧来源标记
  - `scene`: 场景检测帧（scene detection）
  - `interval`: 固定间隔帧（fallback timing）
  - `agent`: AI 代理选择帧（LLM-driven selection）
- `{seq}`: 序号（001-999，3 位零填充）

### 示例

```
frame_00h00m30s250ms_scene_001.jpg      # 场景帧在 30.250 秒
frame_00h00m30s000ms_interval_001.jpg   # 间隔帧在 30.000 秒
frame_00h01m15s678ms_agent_005.jpg      # 代理帧在 75.678 秒
```

## 实现要点

### 1. 毫秒计算使用 `round()` 而非 `int()`

```python
# ❌ 错误：浮点数截断导致精度损失
ms = int((ts % 1) * 1000)  # 30.456 → 455

# ✅ 正确：四舍五入保留精度
ms = round((ts % 1) * 1000)  # 30.456 → 456
```

### 2. 时间戳解析

```python
# 从新格式解析时间戳
name = "frame_00h01m30s250ms_scene_003"
parts = name.split("_", 1)
time_part = parts[1].split("_")[0]  # "00h01m30s250ms"
time_part = time_part.replace("ms", "")

h_part, rest = time_part.split("h")
m_part, rest = rest.split("m")
s_part = rest.split("s")[0]
ms_part = rest.split("s")[1] if rest.split("s")[1] else "0"

h, m, s, ms = int(h_part), int(m_part), int(s_part), int(ms_part)
timestamp = h * 3600 + m * 60 + s + ms / 1000.0
```

## 修改的文件

1. **framelearn/pipeline/ffmpeg_helper.py**
   - `extract_keyframes()`: 场景帧和间隔帧命名
   
2. **framelearn/pipeline/agent_keyframe_selector.py**
   - `select()`: AI 代理选择帧命名

3. **framelearn/pipeline/video_pipeline.py**
   - 缓存关键帧的时间戳解析逻辑

4. **framelearn/pipeline/doc_generator.py**
   - 提示词中的文件名示例更新

## 测试覆盖

见 `test/test_keyframe_naming.py`:
- ✅ 毫秒精度格式化
- ✅ 同一秒内多帧无冲突
- ✅ 时间戳解析往返一致
- ✅ 代理帧命名规则
- ✅ 实际文件创建无冲突
- ✅ 边界值（0ms, 999ms）

## 向后兼容性

⚠️ 新格式与旧格式**不兼容**。已有缓存的关键帧需要重新提取或手动迁移。

缓存失效机制会在输入变更时自动重新提取，无需手动干预。
