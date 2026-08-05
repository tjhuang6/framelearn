## Context

见 proposal.md — Why。

当前 `provider_adapter.py` 的 `call_llm()` 只支持单轮文本响应。`_evaluate()` 通过解析自由文本 JSON 获取决策，不涉及任何工具调用。三类 provider（openai-compatible / google / claude）的函数调用 API 格式各不相同，目前没有统一的 tool-calling 层。

## Goals / Non-Goals

**Goals:**
- 在 `provider_adapter.py` 中新增 `call_llm_with_tools()` 函数，支持 OpenAI-compatible 的 provider 类型的函数调用格式。
- 新建 `framelearn/pipeline/vision_agent.py`，实现 `VisionAgentEvaluator` 类，封装完整的"截帧 → 评估 → 重截 → 决策"工具调用循环。
- `AgentKeyframeSelector._evaluate()` 委托给 `VisionAgentEvaluator`；其余方法（`_decide()`、`_heuristic_needs_frame()`、fallback）保持不变。
- 最大重试次数通过 `settings.toml` 的 `runtime.vision_agent_max_retries` 配置，默认值 5。

**Non-Goals:**
- 不为 Claude provider 实现工具调用（当前没有使用 Claude 作为 vision 模型的配置路径）,太贵了不考虑。
- 不修改文字 LLM 的调用链（`_decide()` 步骤）。
- 不将 `_heuristic_needs_frame()` 替换为 Agent 驱动。 （以后会加 暂且标记  每一个小时切分一次 返回一个 JSON 列表：[{"timestamp": 12.5, "reason": "..."}]）
- 不引入异步/并发框架；循环仍为同步顺序执行。

## Decisions

### 决策 1：新建 `vision_agent.py` 而非扩展 `agent_keyframe_selector.py`

工具循环逻辑（消息历史管理、工具分发、重试计数）独立于字幕分段遍历逻辑。分离到专门模块后，单元测试更容易注入 mock，未来复用也更简单。

替代方案：直接在 `_evaluate()` 中内联循环 — 会让 `agent_keyframe_selector.py` 承担 HTTP 请求构造和消息历史管理的双重责任，与现有职责划分不符。

### 决策 2：在 `provider_adapter.py` 新增 `call_llm_with_tools()`，而非独立维护两套 HTTP 客户端

现有 `_build_openai_request` / `_build_gemini_request` 已处理认证、URL 构造和图像编码。在其基础上扩展工具字段，复用已有逻辑，避免重复维护 httpx 客户端和 env-loading 代码。

函数签名：
```python
def call_llm_with_tools(
    messages: list[dict],       # 完整对话历史
    tools: list[dict],          # JSON Schema 工具定义
    config: ProviderConfig,
    images: list[str] | None = None,   # 仅首轮附加图像
    max_tokens: int = 512,
    timeout: int = 60,
) -> dict                       # 原始响应 body，调用方自行解析 tool_calls
```

返回原始 body 而非解析后文本，是因为工具调用结构因 provider 不同差异较大，让 `VisionAgentEvaluator` 持有解析逻辑更清晰。

替代方案（不用这个）：使用 openai Python SDK — 引入新依赖，且 SiliconFlow / 自定义 endpoint 需要 base_url override，不如直接用 httpx 灵活。

### 决策 3：工具定义使用两个工具 `capture_frame` + `decide`，而非单工具 + 文本 JSON

两个独立工具有各自的 JSON Schema，可强制模型填写必填字段（`keep`、`reason`），避免解析自由文本带来的健壮性问题。`decide` 作为终止工具，天然充当循环退出信号。

替代方案：单一工具 `evaluate(action, timestamp?, keep?, reason?)` — 参数多态，Schema 校验弱，工具含义不清晰。

### 决策 4：消息历史格式统一为 OpenAI 对话格式，Gemini 在发送前转换

OpenAI 格式（`role: user/assistant/tool`）是事实标准，已有大量内部消息构造代码使用该格式。Gemini 的 `contents[].parts` 格式在 `_build_gemini_with_tools_request()` 中转换，调用方无感知。

## Risks / Trade-offs

- **token 消耗增加** → 每次 `capture_frame` 需携带图像（base64）重新发送消息历史，成本随重试次数线性增长。缓解：`max_tokens=512` 限制输出长度；`max_retries` 默认 5 限制循环次数。
- **部分 OpenAI-compatible provider 不支持 vision + tool calling 同时使用** → 例如某些 SiliconFlow 模型可能支持视觉但不支持函数调用。缓解：捕获 API 错误并 fallback 至 `_evaluate_text_only()`，与现有 fallback 路径一致。
- **循环无限等待风险** → 若模型持续调用 `capture_frame` 不调用 `decide`，已由 `max_retries` 上限保障。

## Migration Plan

1. 新增 `provider_adapter.call_llm_with_tools()`（不破坏现有接口）。
2. 新建 `framelearn/pipeline/vision_agent.py`，含 `VisionAgentEvaluator`。
3. 修改 `AgentKeyframeSelector._evaluate()` 调用 `VisionAgentEvaluator.evaluate()`；保留 `_evaluate_text_only()` fallback。
4. `settings.toml` 增加 `runtime.vision_agent_max_retries = 3`。
5. 更新 `test_agent_keyframe.py`，覆盖：首帧决策、单次重截、达到上限、fallback。

无需数据迁移。功能降级：若新模块导入失败，`_evaluate()` 可 catch ImportError 并直接调用 `_evaluate_text_only()`（极端兜底，不作为常规路径）。
