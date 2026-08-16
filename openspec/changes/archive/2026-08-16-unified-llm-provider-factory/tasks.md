# 任务列表：统一 LLM Provider 工厂

## 1. OpenSpec 与设计

- [x] 1.1 创建 change `unified-llm-provider-factory` 并完成 proposal / design / spec delta
- [x] 1.2 运行 `openspec validate unified-llm-provider-factory` 通过

## 2. 供应商与模型目录

- [x] 2.1 新增 `framelearn/llm/catalog.py`：`ApiFormat` / `ProviderPreset` / `ModelCapabilities`
- [x] 2.2 录入非 Responses 供应商预置（deepseek / minimax / claude / openai / openrouter / kimi / moonshot / zhipu / siliconflow / gemini / dashscope）
- [x] 2.3 录入模型能力目录（至少覆盖 MiniMax-M3、deepseek-chat、Qwen3-VL、Gemini、GPT、Kimi、GLM）
- [x] 2.4 提供 `get_provider_preset` / `get_model_capabilities` / prefix 与 alias 解析

## 3. 统一 LlmClient 与工厂

- [x] 3.1 新增 `framelearn/llm/client.py`：`LlmClient` 封装 sync/async/interleaved/tools 调用
- [x] 3.2 实现 `create_llm_client(purpose, ...)` 工厂，支持显式覆盖与能力校验
- [x] 3.3 实现模块级统一入口 `complete` / `complete_async` / `complete_text` / `complete_vision` 等
- [x] 3.4 新增 `framelearn/llm/__init__.py` 公共导出

## 4. provider_adapter 兼容层

- [x] 4.1 `PROVIDERS` 改为从 `PROVIDER_PRESETS` 生成，保持旧形状和 `type` 值
- [x] 4.2 保持 `load_text_config` / `load_vision_config` / `call_llm*` / `call_text_llm` / `call_vision_llm` 行为不变
- [x] 4.3 新增 `call_text_llm_async` / `call_vision_llm_async` 便捷入口

## 5. 文档与配置

- [x] 5.1 `settings.toml` 注释补充 provider 可选表与非 Responses 说明
- [x] 5.2 `.env.example` 补充 `MINIMAX_API_KEY` 等供应商 key 说明
- [x] 5.3 `README.md` 增加统一入口与工厂示例

## 6. 测试与回归

- [x] 6.1 新增 `test/src/test_llm_provider_factory.py`：目录/工厂/能力校验/非 Responses 协议
- [x] 6.2 全量 pytest 通过
- [x] 6.3 更新任务勾选，git add + commit（不 push）
