# AntiVibe P1 补丁记录：app-server 文档生成多模态支持

关联审计报告：[[antivibe-technical-report]] 第 165-169 行

## 1. 问题描述

原实现中，`DocumentGenerator._generate_via_appserver()` 调用 `session.run_turn(prompt)` 时，只传递纯文本 prompt。虽然 prompt 中列出了关键帧文件名（如 `frame_0001.jpg`），但 app-server 模型并不会自动读取这些图片文件。

即使 Codex 模型理论上可以通过文件工具主动读取图片，这依赖于：
- 工作目录设置正确
- 模型主动选择使用文件读取工具
- 审批策略允许文件访问
- 模型行为的不确定性

因此，当前 app-server 路径实际上是**文本模式**，不能算作确定性的多模态输入。

根据 `docs/app-server-video-multimodal-pipeline.md` 的设计文档，Codex app-server 协议完整支持结构化 `UserInput`，包括 `localImage` 类型。正确的做法是将关键帧路径作为 `localImage` 明确发送给模型。

## 2. 修复范围

本次修复只针对 P1 问题：使 app-server 文档生成真正支持多模态输入。

修改内容：
1. `session.py:run_turn()` 扩展为接受结构化 `inputs` 参数
2. `doc_generator.py:_generate_via_appserver()` 改用 `_build_multimodal_inputs()` 构建请求
3. 新增 `_build_multimodal_inputs()` 方法，按照 app-server 协议构造 `text` + `localImage` 交错输入

不修改：
- app-server 的其他调用路径（如 `ask` 命令）继续使用纯文本接口
- Vision API 路径保持不变
- Agent keyframe selector 的 app-server 分支（已在其他补丁中修复）

## 3. 补丁内容

### 3.1 扩展 `AppServerSession.run_turn()` 接受结构化输入

**文件**：`framelearn/app_server/session.py`

**修改位置**：第 97-131 行

**变更前**：

```python
def run_turn(
    self,
    text: str,
    ui_callback: Optional[Callable[[dict], None]] = None,
) -> TurnResult:
    """
    Send one user message and consume the turn until completion.

    Args:
        text: User message text
        ui_callback: Optional callable for streaming UI events (errors ignored)

    Returns:
        TurnResult with projected messages and metadata
    """
    # ...
    response = self._client.request(
        "turn/start",
        {
            "threadId": self._thread_id,
            "input": [{"type": "text", "text": text}],
        },
        timeout=self.TURN_START_TIMEOUT,
    )
```

**变更后**：

```python
def run_turn(
    self,
    text: str = "",
    inputs: Optional[list[dict]] = None,
    ui_callback: Optional[Callable[[dict], None]] = None,
) -> TurnResult:
    """
    Send one user message and consume the turn until completion.

    Args:
        text: User message text (deprecated when inputs is provided)
        inputs: Structured turn inputs (list of {type, text/path/url})
        ui_callback: Optional callable for streaming UI events (errors ignored)

    Returns:
        TurnResult with projected messages and metadata
    """
    # Build turn inputs
    if inputs is not None:
        turn_inputs = inputs
    elif text:
        turn_inputs = [{"type": "text", "text": text}]
    else:
        raise ValueError("Either text or inputs must be provided")

    # Start the turn
    response = self._client.request(
        "turn/start",
        {
            "threadId": self._thread_id,
            "input": turn_inputs,
        },
        timeout=self.TURN_START_TIMEOUT,
    )
```

**设计说明**：

- `text` 参数保留并设为可选，向后兼容现有调用
- 新增 `inputs` 参数接受结构化列表，每个元素为 `{"type": "text"|"localImage"|..., ...}`
- 当 `inputs` 存在时优先使用；否则从 `text` 构建单个文本输入
- 两者都不提供时抛出异常

### 3.2 新增 `_build_multimodal_inputs()` 方法

**文件**：`framelearn/pipeline/doc_generator.py`

**修改位置**：新增方法（第 361-404 行）

**实现**：

```python
def _build_multimodal_inputs(
    self,
    keyframes: list[tuple[Path, float]],
    subtitle: str,
    mode: DocMode,
) -> list[dict]:
    """Build structured turn inputs with text + localImage for app-server."""
    def format_timestamp(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        else:
            return f"{m:02d}:{s:02d}"

    # Select template based on mode
    if mode == "visual_script":
        template = _VISUAL_SCRIPT_PROMPT
    elif mode == "notes":
        template = _NOTES_PROMPT
    else:
        template = _TEXTBOOK_PROMPT

    # Build instruction with subtitle but WITHOUT frame file names
    # (actual frames will be sent as localImage)
    instruction = template.format(
        subtitle=subtitle,
        frames_description="(关键帧将以图片形式提供)",
    )

    inputs: list[dict] = [{"type": "text", "text": instruction}]

    # Add each keyframe with timestamp and localImage
    for i, (frame_path, ts) in enumerate(keyframes[:20]):
        timestamp = format_timestamp(ts)
        inputs.append({
            "type": "text",
            "text": f"\n关键帧 {i+1} [{timestamp}]:",
        })
        # Send absolute path as localImage
        inputs.append({
            "type": "localImage",
            "path": str(frame_path.resolve()),
        })

    return inputs
```

**设计说明**：

- 返回符合 Codex app-server 协议的 `UserInput[]` 结构
- 第一个元素是完整的指令文本，包含字幕和任务描述
- `frames_description` 占位符不再填入文件名列表，改为提示"关键帧将以图片形式提供"
- 随后交错插入：时间戳文本 → `localImage` 对象
- `localImage.path` 使用 `frame_path.resolve()` 确保发送绝对路径
- 限制最多 20 张关键帧，与 Vision API 路径保持一致

### 3.3 修改 `_generate_via_appserver()` 调用方式

**文件**：`framelearn/pipeline/doc_generator.py`

**修改位置**：第 365-376 行

**变更前**：

```python
def _generate_via_appserver(
    self,
    keyframes: list[tuple[Path, float]],
    subtitle: str,
    mode: DocMode,
) -> str:
    """Generate via codex app-server."""
    from framelearn.app_server.session import AppServerSession

    prompt = self._build_prompt(keyframes, subtitle, mode)

    session = AppServerSession(workspace=".")
    result = session.run_turn(prompt)
    session.close()
```

**变更后**：

```python
def _generate_via_appserver(
    self,
    keyframes: list[tuple[Path, float]],
    subtitle: str,
    mode: DocMode,
) -> str:
    """Generate via codex app-server with multimodal input."""
    from framelearn.app_server.session import AppServerSession

    # Build structured multimodal inputs
    inputs = self._build_multimodal_inputs(keyframes, subtitle, mode)

    session = AppServerSession(workspace=".")
    result = session.run_turn(inputs=inputs)
    session.close()
```

## 4. 协议参考

根据 `docs/app-server-video-multimodal-pipeline.md`，Codex app-server 支持以下 `UserInput` 类型：

```typescript
type UserInput = 
  | { type: "text", text: string }
  | { type: "image", url: string }
  | { type: "localImage", path: string }
  | { type: "audio", url: string }
  | { type: "localAudio", path: string }
  | ...
```

当前 `gpt-5.6-sol` 模型的 `inputModalities` 为 `["text", "image"]`，因此 `text` + `localImage` 组合是当前可靠的输入方式。

发送示例（JSON-RPC 格式）：

```json
{
  "method": "turn/start",
  "params": {
    "threadId": "thread-123",
    "input": [
      {"type": "text", "text": "请根据关键帧和字幕生成教程文档。\n字幕：..."},
      {"type": "text", "text": "\n关键帧 1 [00:00:05]:"},
      {"type": "localImage", "path": "/absolute/path/frame_0005.jpg"},
      {"type": "text", "text": "\n关键帧 2 [00:00:12]:"},
      {"type": "localImage", "path": "/absolute/path/frame_0012.jpg"}
    ]
  }
}
```

## 5. 验证方式

### 5.1 代码层面验证

- [ ] `session.py:run_turn()` 可以接受 `inputs` 参数
- [ ] 当只传递 `text` 时，向后兼容旧调用方式
- [ ] 当传递 `inputs` 时，直接发送给 `turn/start`
- [ ] `_build_multimodal_inputs()` 返回的列表结构符合协议
- [ ] 每个 `localImage.path` 是绝对路径
- [ ] 关键帧数量不超过 20 张

### 5.2 集成测试验证

运行文档生成流程：

```bash
framelearn learn video.mp4 --runtime.doc_mode appserver
```

预期行为：
- app-server 收到的 `turn/start` 请求中包含 `localImage` 对象
- 模型能够看到真实图片内容
- 生成的文档能够准确描述画面细节，而不是仅根据文件名猜测

### 5.3 日志检查

在 `session.py` 中可添加调试日志：

```python
import json
print(f"[DEBUG turn/start] inputs: {json.dumps(turn_inputs, ensure_ascii=False, indent=2)}")
```

确认发送的 `input` 数组包含 `localImage` 元素。

### 5.4 对比测试

分别使用：
1. 旧实现（纯文本 prompt，只包含文件名）
2. 新实现（`text` + `localImage` 交错）

对比生成结果的画面描述准确度。预期新实现能够：
- 识别屏幕截图中的代码、终端、图表
- 准确描述界面布局
- 引用画面中的文字内容
- 不会出现"根据文件名推测"的模糊描述

## 6. 兼容性与影响

### 6.1 不影响现有功能

- `ask` 命令继续使用 `runtime.run_turn(text=...)` 纯文本接口
- Vision API 路径 (`_generate_via_api`) 保持不变
- Agent keyframe selector 的文本模式不受影响
- `RuntimeAdapter.run_turn()` 仍然只接受 `user_text`，不需要修改

### 6.2 新旧调用兼容

`session.run_turn()` 的签名变更向后兼容：

```python
# 旧调用方式（仍然有效）
result = session.run_turn("用户问题")

# 新调用方式
result = session.run_turn(inputs=[
    {"type": "text", "text": "指令"},
    {"type": "localImage", "path": "/path/to/image.jpg"}
])
```

### 6.3 路径安全

- `localImage.path` 使用 `Path.resolve()` 确保绝对路径
- app-server 子进程有权读取项目目录下的关键帧文件
- 未来可增强：检查路径是否在允许的工作目录范围内

## 7. 后续改进空间

本次修复专注于 P1 问题的最小实现。后续可以改进：

1. **统一 input builder**：抽取 `build_turn_inputs()` 为独立模块，供所有 app-server 调用点复用
2. **路径验证**：在发送前检查文件存在性、格式、大小
3. **模型能力检测**：调用 `model/list` 确认目标模型支持 `image` modality
4. **失败降级**：当 `localImage` 发送失败时，可选降级到纯文本模式
5. **审批策略**：明确配置 file read 审批策略，避免模型需要审批才能访问图片

## 8. 变更文件列表

```text
framelearn/app_server/session.py
framelearn/pipeline/doc_generator.py
docs/antivibe-patch-p1-multimodal-doc-gen.md
```

## 9. 完成条件

以下条件全部满足后，本补丁视为完成：

- [ ] `session.py:run_turn()` 接受 `inputs` 参数
- [ ] `doc_generator.py:_build_multimodal_inputs()` 正确构建结构化输入
- [ ] `_generate_via_appserver()` 使用新方法调用
- [ ] 集成测试确认 app-server 收到 `localImage`
- [ ] 生成的文档能够准确描述画面内容
- [ ] 现有 `ask` 命令功能不受影响
- [ ] `git diff --check` 通过
- [ ] 原始报告 [[antivibe-technical-report]] 保持不变，补丁历史由本文承载

## 10. 与其他补丁的关系

- 本补丁独立于 [[antivibe-patch-lines-153-163]]（P0 依赖缺失和 Agent Vision API 修复）
- 可以单独应用，不依赖其他补丁
- 与 P1 缓存策略、文件名冲突等问题无关，可并行修复

## 11. 参考文档

- [[antivibe-technical-report]]：原始审计报告
- [[docs/app-server-video-multimodal-pipeline]]：app-server 多模态设计文档
- [[docs/codex-app-server-guide]]：app-server 协议和生命周期
- [[docs/architecture]]：FrameLearn 整体架构
