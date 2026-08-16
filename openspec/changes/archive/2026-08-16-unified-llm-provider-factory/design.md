# 设计：统一 LLM Provider 工厂（unified-llm-provider-factory）

## Context

FrameLearn 当前 `provider_adapter.py` 的 `PROVIDERS` 是 wire 协议字典（`openai` / `claude` / `google`），不包含模型能力，也不区分“供应商”和“wire 格式”。配置当前使用 `provider="claude"` + `model="MiniMax-M3"` + MiniMax Anthropic 兼容端点，说明供应商名和协议名是解耦的。

cc-switch 的参考结构：

- `src/config/piModelCatalog.ts`：静态模型目录，包含 `name`、`reasoning`、`input: ["text","image"]`、`contextWindow`、`maxTokens`；
- `src/config/piProviderPresets.ts`：静态供应商预置，包含 `baseUrl`、`api` 格式、模型列表；
- 运行物化由 factory/map 完成，调用方只拿物化后的 provider。

FrameLearn 不需要 cc-switch 的 UI/切换/路由功能，只借用“供应商预置 + 模型能力目录 + 工厂物化”三层结构。

## Goals / Non-Goals

**Goals:**

- 为文本、视觉分别提供唯一的 Python 统一入口。
- 工厂根据 `purpose ∈ {text, vision}` 解析配置并实例化 `LlmClient`。
- 供应商目录决定 wire 协议，只允许非 Responses 协议。
- 模型目录提供能力校验：已知模型不具备 image 输入时，不允许作为 vision client。
- 旧 `provider_adapter` API 不破坏，现有测试继续通过。

**Non-Goals:**

- 不实现 Responses API 选择；旧 `_build_responses_request` 仅保留为未使用的历史代码。
- 不实现流式输出、用量统计、自动故障转移、OAuth。
- 不在本变更中大规模重写所有 pipeline 调用点；新增入口供新代码使用，旧入口作为兼容层。
- 不自动从 cc-switch TS 目录生成 Python 数据，只人工对齐 FrameLearn 当前需要的条目。

## Decisions

### Decision 1: 目录分为 `PROVIDER_PRESETS` 与 `MODEL_CATALOG`

`framelearn/llm/catalog.py` 定义：

```python
ApiFormat = Literal["openai_chat", "anthropic", "gemini"]

@dataclass(frozen=True)
class ProviderPreset:
    key: str
    name: str
    api_format: ApiFormat
    base_url: str
    api_key_url: str | None = None
    aliases: tuple[str, ...] = ()

@dataclass(frozen=True)
class ModelCapabilities:
    name: str
    vendor: str
    input: tuple[str, ...]          # ("text",) or ("text", "image")
    reasoning: bool = False
    context_window: int | None = None
    max_tokens: int | None = None
```

供应商预置（首批）：

| key | api_format | base_url |
|---|---|---|
| deepseek | openai_chat | https://api.deepseek.com/v1/ |
| minimax | anthropic | https://api.minimaxi.com/anthropic |
| claude | anthropic | https://api.anthropic.com |
| openai | openai_chat | https://api.openai.com/v1/ |
| openrouter | openai_chat | https://openrouter.ai/api/v1/ |
| kimi / moonshot | openai_chat | https://api.moonshot.cn/v1/ |
| zhipu | openai_chat | https://open.bigmodel.cn/api/paas/v4/ |
| siliconflow | openai_chat | https://api.siliconflow.cn/v1/ |
| gemini | gemini | https://generativelanguage.googleapis.com/v1beta/ |
| dashscope | openai_chat | https://dashscope.aliyuncs.com/compatible-mode/v1 |

模型目录覆盖当前配置会出现的模型（MiniMax-M3、deepseek-chat、Qwen/Qwen3-VL-*、Gemini、GPT、Kimi、GLM 等），并支持前缀匹配（如 `Qwen/Qwen3-VL` 前缀映射为 image-capable）。

### Decision 2: 工厂 API

```python
def create_llm_client(
    purpose: Literal["text", "vision"],
    *,
    config: ProviderConfig | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> LlmClient
```

- 默认不传参数时，`purpose="text"` 调 `load_text_config()`，`purpose="vision"` 调 `load_vision_config()`；配置解析顺序保持不变：env override > settings.toml。
- 显式参数用于测试和调用方覆盖。
- `provider` 支持 alias（如 `minimax` / `moonshot`）。
- 返回的 `LlmClient` 持有最终 `ProviderConfig`、`ProviderPreset`、`ModelCapabilities | None` 和 `purpose`。

能力校验：

- 已知模型且 `purpose="vision"` 但 `input` 不含 `"image"` → `ValueError`，并给出候选。
- 未知模型 → 允许透传（保证自定义 endpoint / 新模型可用），能力记为 `None`。

### Decision 3: `LlmClient` 是唯一运行入口

```python
client = create_llm_client("text")
client.complete(prompt, max_tokens=4096)                      # sync
await client.complete_async(prompt, max_tokens=8192)          # async
await client.complete_interleaved_async(segments, ...)        # vision
client.complete_with_tools(messages, tools, images=None)      # tool call
await client.complete_with_tools_async(...)
```

`LlmClient` 内部调用 `provider_adapter` 现有的 `_dispatch_*` 实现，避免复制请求构造和解析逻辑。这样现有 `httpx` mock 测试路径不变。

模块级统一入口：

```python
from framelearn.llm import complete, complete_async, complete_text, complete_vision

complete("text", "问题")
complete("vision", "看图", images=["frame.jpg"])
```

`complete_*` 是糖：`complete_text()` = `complete("text")`，`complete_vision()` = `complete("vision")`。

### Decision 4: 非 Responses 协议是权威约束

- 目录的 `ApiFormat` 只有 `openai_chat` / `anthropic` / `gemini`。
- `provider_adapter.PROVIDERS` 由目录生成，wire type 映射固定为 `openai_chat→openai`、`anthropic→claude`、`gemini→google`。
- 工厂从不构造 `responses` type 的 provider；因此新入口不可能选中 `/responses`。
- 旧 `_build_responses_request` 保留但不在目录中引用。

### Decision 5: 向后兼容层

- `provider_adapter.load_text_config()` / `load_vision_config()` 继续返回 `ProviderConfig`。
- `provider_adapter.call_text_llm()` / `call_vision_llm()` 继续工作；另增 `call_text_llm_async()` / `call_vision_llm_async()`。
- `PROVIDERS` 保持 `{provider: {name, base_url, type, reg_url}}` 形状，现有测试不受影响。
- 新目录中 `claude` key 仍是 Anthropic wire；现有 `settings.toml` 的 `provider="claude"` + `model="MiniMax-M3"` 无需改动即可运行。

## Risks / Trade-offs

| 风险 | 对策 |
|---|---|
| `PROVIDERS` 改为从目录生成可能改变旧 provider 的 base_url 或 type | 逐项对齐当前字典；先跑全量 pytest 再提交 |
| 模型目录前缀匹配误判 | 只对明确的 `vendor/` 或已知前缀做映射；未知模型透传，不阻止运行 |
| 新增包与 `provider_adapter` 循环导入 | 依赖方向固定：`llm.catalog` → `provider_adapter` → `llm.client`（延迟导入 factory 结果），`llm/__init__` 只聚合导出 |
| vision 配置了 text-only 模型 | 工厂抛 `ValueError`，错误信息列出可用的 image-capable 模型，调用方在 pipeline 初始化阶段快速失败 |
