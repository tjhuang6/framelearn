## 1. provider_adapter：新增工具调用接口

- [ ] 1.1 在 `provider_adapter.py` 中定义 `call_llm_with_tools(messages, tools, config, images, max_tokens, timeout) -> dict`，返回原始响应 body
- [ ] 1.2 实现 OpenAI-compatible 路径：在请求 body 中注入 `tools` 数组和 `tool_choice`，解析响应中的 `choices[0].message.tool_calls`
- [ ] 1.3 实现 Google Gemini 路径：将 OpenAI-format `tools` 转换为 `functionDeclarations`，解析响应中的 `functionCall` parts
- [ ] 1.4 为 Claude provider 路径抛出 `NotImplementedError`（当前无 Claude vision 配置路径，不实现）

## 2. 新建 vision_agent.py

- [ ] 2.1 创建 `framelearn/pipeline/vision_agent.py`，定义 `TOOL_CAPTURE_FRAME` 和 `TOOL_DECIDE` 两个工具的 JSON Schema 常量
- [ ] 2.2 实现 `VisionAgentEvaluator.__init__()`，从 config 读取 `runtime.vision_agent_max_retries`（默认 3）
- [ ] 2.3 实现 `VisionAgentEvaluator.evaluate(frame_path, context, video_path, output_dir, timestamp) -> KeyframeEvaluation`：初始化消息历史，附加首帧图像，启动循环
- [ ] 2.4 实现循环体：解析 `call_llm_with_tools` 返回的 body，分派到 `_handle_capture_frame()` 或 `_handle_decide()`；达到 `max_retries` 后强制返回 `keep=True`
- [ ] 2.5 实现 `_handle_capture_frame(timestamp)`：调用 `FFmpegHelper.capture_single_frame()`，将新帧图像作为 tool result 追加到消息历史
- [ ] 2.6 实现 `_handle_decide(keep, reason)`：构造并返回 `KeyframeEvaluation`，结束循环

## 3. 接入 AgentKeyframeSelector

- [ ] 3.1 修改 `AgentKeyframeSelector._evaluate()`：实例化 `VisionAgentEvaluator` 并调用 `evaluate()`；捕获所有异常并 fallback 至 `_evaluate_text_only()`
- [ ] 3.2 在 `settings.toml` 中添加 `runtime.vision_agent_max_retries = 3`

## 4. 测试

- [ ] 4.1 在 `test_agent_keyframe.py` 中新增 mock 测试：模型首帧直接调用 `decide`（happy path）
- [ ] 4.2 新增 mock 测试：模型调用一次 `capture_frame` 后调用 `decide`（重截后保留）
- [ ] 4.3 新增 mock 测试：模型持续调用 `capture_frame` 达到上限，验证循环强制退出并返回 `keep=True`
- [ ] 4.4 新增 mock 测试：`call_llm_with_tools` 抛出异常，验证 fallback 至 `_evaluate_text_only()`
- [ ] 4.5 在 `provider_adapter` 单元测试中验证 OpenAI 路径的 `tools` 字段注入格式正确
- [ ] 4.6 在 `provider_adapter` 单元测试中验证 Gemini 路径的 `functionDeclarations` 转换格式正确
