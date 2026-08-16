# llm-provider-factory Specification

## Purpose
为 FrameLearn 的文本模型与视觉模型提供统一调用入口；入口由工厂根据配置选择具体供应商、模型和非 Responses 协议，并通过静态模型目录校验模型能力。
## Requirements
### Requirement: 文本与视觉模型具有统一调用入口
系统 SHALL 提供 `complete` / `complete_async` 统一入口，调用方通过 `purpose ∈ {"text", "vision"}` 选择文本模型或视觉模型，不直接拼接供应商请求。系统 SHALL 同时提供 `complete_text` / `complete_vision` 等便捷包装。

#### Scenario: 文本模型统一入口
- **WHEN** 调用方执行 `complete("text", prompt)` 或 `await complete_async("text", prompt)`
- **THEN** 系统按文本配置（`TEXT_*` 环境变量优先，其次 `settings.toml [text]`）调用文本模型，并返回模型文本

#### Scenario: 视觉模型统一入口
- **WHEN** 调用方执行 `complete("vision", prompt, images=[...])`
- **THEN** 系统按视觉配置（`VISION_*` 环境变量优先，其次 `settings.toml [vision]`）调用视觉模型并传入图片

### Requirement: 工厂根据配置决定具体模型
系统 SHALL 提供 `create_llm_client(purpose)` 工厂。工厂 MUST 根据 purpose 解析对应配置段，并将最终确定的 provider / model / base_url / api_key 和 wire 协议封装进 `LlmClient`。工厂 SHALL 支持显式覆盖 provider、model、base_url、api_key。

#### Scenario: 配置从 claude 协议供应商切换到 MiniMax 供应商
- **WHEN** 文本配置为 `provider="minimax"` 且 `model="MiniMax-M3"`
- **THEN** 工厂解析出 `minimax` 预置的 Anthropic Messages 协议与 MiniMax base_url，返回可调用的文本 client

#### Scenario: DeepSeek 使用 Chat Completions
- **WHEN** 文本配置为 `provider="deepseek"` 且 `model="deepseek-chat"`
- **THEN** 工厂选择 `openai_chat` 协议，请求发往 `/chat/completions`，不请求 `/responses`

### Requirement: 工厂只选择非 Responses 协议
工厂可选择的 wire 协议 MUST 仅为 `openai_chat`、`anthropic`、`gemini`。供应商预置 SHALL NOT 包含 Responses API 格式；统一入口 MUST NOT 构造 Responses 请求。

#### Scenario: 所有内置供应商都走非 Responses 端点
- **WHEN** 工厂物化任一内置供应商预置
- **THEN** 该预置的 `api_format` 属于 `openai_chat` / `anthropic` / `gemini` 之一，且生成的请求端点不是 `/responses`

### Requirement: 模型能力目录用于 vision 能力校验
系统 SHALL 维护静态模型目录，记录模型是否支持 `text` / `image` 输入。已知模型被配置为视觉模型但不支持 image 输入时，工厂 MUST 抛出 `ValueError`；未知模型 SHALL 允许透传以支持自定义 endpoint。

#### Scenario: 已知 text-only 模型不能作为视觉模型
- **WHEN** `create_llm_client("vision")` 解析到的模型在目录中且 `input` 不含 `"image"`
- **THEN** 工厂抛出 `ValueError`，错误信息包含模型名和 image-capable 候选

#### Scenario: 未知自定义模型允许透传
- **WHEN** 视觉配置的模型不在模型目录中，但 provider 和 base_url 有效
- **THEN** 工厂创建 vision client，不因能力未知而拒绝调用

### Requirement: 旧 provider_adapter API 保持兼容
系统 SHALL 保持 `provider_adapter` 的 `ProviderConfig`、`load_text_config`、`load_vision_config`、`call_llm`、`call_llm_async`、`call_text_llm`、`call_vision_llm` 等既有 API 可用；`PROVIDERS` 字典 SHALL 继续提供 `name`、`base_url`、`type` 字段。

#### Scenario: 旧调用方无需修改
- **WHEN** 现有 pipeline 或外部脚本仍调用 `provider_adapter.call_text_llm(prompt)`
- **THEN** 系统按原配置解析路径调用文本模型，行为与变更前一致

