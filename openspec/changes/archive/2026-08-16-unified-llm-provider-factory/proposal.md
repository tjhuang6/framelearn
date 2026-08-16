# 提案：统一 LLM Provider 工厂与文本/视觉统一入口

## 问题

当前 FrameLearn 的文本模型和视觉模型调用分散在 `provider_adapter.py` 的多个入口中：

- `call_text_llm()` 只有同步版本，`call_vision_llm()` 只有同步版本；
- pipeline 各处自行调用 `load_text_config()` / `load_vision_config()` 后再把 `ProviderConfig` 传给底层 `call_llm_async()`；
- `PROVIDERS` 只描述 wire 协议，不描述供应商/模型能力，配置里也没有 MiniMax、DashScope 等独立供应商预置；
- 协议分支散落在 `_dispatch_sync` / `_dispatch_async` / interleaved / tools 四套路径里，新增供应商时容易漏改；
- 项目明确走**非 Responses 格式**（OpenAI Chat Completions、Anthropic Messages、Gemini generateContent），但现有代码仍保留 Responses 分支，缺少一个“只选非 Responses”的权威工厂。

参考 cc-switch 的做法：它把 `piModelCatalog`（模型能力：text/image、context window、max tokens）和 `piProviderPresets`（供应商：base_url、api 格式、模型列表）分开，再通过工厂/映射函数物化出运行时 provider。FrameLearn 需要同样层次。

## 目标

1. 新增 `framelearn.llm` 包，提供唯一统一入口：
   - `complete("text", prompt, ...)` / `complete_async("text", prompt, ...)`
   - `complete("vision", prompt, images=[...], ...)` / `complete_async("vision", ...)`
   - 以及 `complete_text()` / `complete_vision()` 等便捷包装。
2. 新增 `create_llm_client(purpose)` 工厂：工厂读取 `TEXT_*` / `VISION_*` 环境变量和 `settings.toml`，决定具体供应商、模型、base_url、api_key 和 wire 协议，返回统一 `LlmClient`。
3. 新增 cc-switch 风格的静态目录：
   - `PROVIDER_PRESETS`：DeepSeek、MiniMax、Claude、OpenAI、OpenRouter、Kimi/Moonshot、智谱、SiliconFlow、Gemini、DashScope 等。
   - `MODEL_CATALOG`：模型能力（`input=["text","image"]` 等）。
4. 工厂只选择三种非 Responses wire 格式：`openai_chat`、`anthropic`、`gemini`。默认不产生 `/responses` 请求。
5. 保持 `provider_adapter.py` 的旧 API 完全向后兼容；`PROVIDERS` 改由新目录生成，并增加 `minimax` 等供应商 key。

## Capabilities

### New Capabilities
- `llm-provider-factory`: 基于供应商/模型目录的统一文本与视觉 LLM 工厂入口

### Modified Capabilities
<!-- 本次不修改既有 spec 级行为 -->

## Impact

- 新增：`framelearn/llm/__init__.py`、`framelearn/llm/catalog.py`、`framelearn/llm/client.py`
- 修改：`framelearn/provider_adapter.py`（`PROVIDERS` 从目录生成，增加异步便捷入口）
- 修改：`settings.toml`、`.env.example`、`README.md`（文档化 `minimax` provider 与统一入口）
- 新增测试：`test/src/test_llm_provider_factory.py`
- 兼容性：现有 `ProviderConfig`、`load_text_config()`、`load_vision_config()`、`call_llm*`、`call_text_llm()`、`call_vision_llm()` 均保持不变
