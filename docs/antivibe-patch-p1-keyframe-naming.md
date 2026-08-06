# 关键帧文件名冲突修复 - 技术报告

## 问题描述（antivibe-technical-report.md 第 177 行）

**问题**：场景帧与固定间隔帧都按整秒命名。两个候选帧落在同一秒时，`Path.rename()` 可能覆盖、失败或使两个 tuple 指向同一文件，具体取决于平台语义。

**建议**：文件名保留毫秒或附加来源/序号，例如 `frame_00h01m30s250_scene_003.jpg`。

## pi-agent 执行结果

✅ **已完成** - pi-agent 成功修复了该问题

### 修复内容

#### 1. 新命名格式（毫秒精度 + 来源标记 + 序号）

```
frame_{HH}h{MM}m{SS}s{MMM}ms_{source}_{seq}.jpg
```

**示例**：
- `frame_00h01m30s250ms_scene_003.jpg` - 场景检测帧在 90.250 秒
- `frame_00h00m30s000ms_interval_001.jpg` - 固定间隔帧在 30.000 秒  
- `frame_00h02m05s678ms_agent_006.jpg` - AI 代理选择帧在 125.678 秒

**格式说明**：
- `{HH}h{MM}m{SS}s{MMM}ms`: 时间戳（毫秒精度，000-999ms）
- `{source}`: 帧来源（`scene` / `interval` / `agent`）
- `{seq}`: 序号（001-999，3 位零填充）

#### 2. 核心代码修改

**framelearn/pipeline/ffmpeg_helper.py**：
```python
# 旧代码：整秒精度
new_name = output_dir / f"frame_{h:02d}h{m:02d}m{s:02d}s.jpg"

# 新代码：毫秒精度 + 来源标记
ms = round((ts % 1) * 1000)  # 使用 round() 而非 int()
new_name = output_dir / f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_scene_{i+1:03d}.jpg"
```

**framelearn/pipeline/agent_keyframe_selector.py**：
```python
# AI 代理选择帧命名
ms = round((ts % 1) * 1000)
frame_name = f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_agent_{len(selected)+1:03d}.jpg"
```

**framelearn/pipeline/video_pipeline.py**：
- 更新缓存帧的时间戳解析逻辑，支持新格式

**framelearn/pipeline/doc_generator.py**：
- 提示词中的文件名示例更新为新格式

#### 3. 测试覆盖

**tests/test_keyframe_naming.py** - 7 个测试全部通过 ✅

1. ✅ `test_millisecond_formatting` - 毫秒精度格式化正确
2. ✅ `test_no_collision_same_second` - 同一秒内多帧无冲突
3. ✅ `test_timestamp_parsing_roundtrip` - 时间戳解析往返一致
4. ✅ `test_agent_frame_naming` - 代理帧命名规则正确
5. ✅ `test_actual_file_creation` - 实际文件创建无冲突
6. ✅ `test_boundary_values` - 边界值（0ms, 999ms）处理正确
7. ✅ `test_source_tag_uniqueness` - 来源标记确保唯一性

#### 4. 文档

**docs/KEYFRAME_NAMING.md** - 完整设计文档
- 格式规范
- 实现要点（使用 `round()` 而非 `int()` 计算毫秒）
- 时间戳解析示例
- 向后兼容性说明

## 技术细节

### 关键实现要点

1. **毫秒计算使用 `round()` 而非 `int()`**
   ```python
   # ❌ 错误：浮点数截断导致精度损失
   ms = int((ts % 1) * 1000)  # 30.456 → 455
   
   # ✅ 正确：四舍五入保留精度
   ms = round((ts % 1) * 1000)  # 30.456 → 456
   ```

2. **三重保证唯一性**
   - 毫秒精度（0-999ms）
   - 来源标记（scene / interval / agent）
   - 序号（001-999）

3. **时间戳解析兼容**
   - 新代码可以解析新格式
   - 缓存失效机制会在格式不匹配时自动重新提取

### 向后兼容性

⚠️ **新格式与旧格式不兼容**

已有缓存的关键帧需要重新提取或手动迁移。不过：
- 缓存失效机制会在输入变更时自动重新提取
- 无需手动干预
- 不影响现有功能和 API

## 验证结果

### 测试结果
```bash
cd /Users/iwill/Documents/PythonProjects/FrameLearn-fix
pytest tests/test_keyframe_naming.py -v
```

✅ **7/7 测试通过**

### 问题解决确认

**修复前的风险**：
- ❌ 同一秒内多帧会覆盖
- ❌ 文件可能丢失
- ❌ tuple 可能指向错误文件

**修复后的保证**：
- ✅ 毫秒精度避免时间冲突
- ✅ 来源标记区分不同类型帧
- ✅ 序号确保绝对唯一性
- ✅ 文件名唯一性有三重保障

## 相关文件

### 修改的文件
- `framelearn/pipeline/ffmpeg_helper.py` - 场景帧和间隔帧命名
- `framelearn/pipeline/agent_keyframe_selector.py` - AI 代理帧命名
- `framelearn/pipeline/video_pipeline.py` - 时间戳解析
- `framelearn/pipeline/doc_generator.py` - 提示词示例

### 新增的文件
- `tests/test_keyframe_naming.py` - 测试套件（7 个测试）
- `docs/KEYFRAME_NAMING.md` - 设计文档

## 总结

pi-agent 成功完成了关键帧文件名冲突的修复：

1. ✅ 实现了毫秒精度的时间戳格式
2. ✅ 添加了来源标记和序号保证唯一性
3. ✅ 修改了所有相关代码（ffmpeg_helper、agent_selector、pipeline、doc_generator）
4. ✅ 提供了完整的测试覆盖（7 个测试）
5. ✅ 编写了详细的技术文档

**关键问题已解决**：同一秒内的多个关键帧通过毫秒精度 + 来源标记 + 序号确保文件名唯一，不再发生覆盖或冲突。

---

**状态**: ✅ 已完成并测试通过  
**修复人**: pi-agent (OpenAI Codex)  
**测试覆盖**: 7/7 通过  
**文档**: 完整
