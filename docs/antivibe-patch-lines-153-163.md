# AntiVibe P0 漏洞补丁记录：第 153–163 行

关联审计报告：[[antivibe-technical-report]]

## 1. 文档策略

本次操作保留原始审计报告，不修改其中第 153–163 行。原报告继续作为问题发现时的审计快照；本文单独记录对应问题的修复范围、实现方式和验证证据。

## 2. 修复范围

原报告中的两个 P0 问题：

1. 安装声明与实际 import 不一致：源码依赖 Pillow、ImageHash 和 `oss2`，但项目元数据与锁文件未声明。
2. Agent 图像 API 路径不可执行：`AgentKeyframeSelector` 导入不存在的 `ProviderAdapter`，导致 API 模式无法真正评估关键帧图片。

本次只修复这两个问题，不处理报告后续列出的 app-server 多模态、缓存、文件名冲突或其他风险。

## 3. 补丁一：补齐运行依赖

### 修改

在 `pyproject.toml` 的正式运行依赖中加入：

- `imagehash>=4.3.2`
- `oss2>=2.19.1`
- `pillow>=12.3.0`

同步更新 `uv.lock`，使干净环境执行 `uv sync` 时能够安装关键帧 pHash 和 DashScope OSS 路径所需的包。

### 为什么放入正式依赖

`framelearn/pipeline/keyframe_dedup.py` 在模块导入阶段直接导入 Pillow 和 ImageHash；当前仓库默认 ASR provider 又是 DashScope，其 OSS 后端需要 `oss2`。因此这三个包属于当前默认功能路径，而不是仅供开发或测试使用的依赖。

### 验证标准

- `pyproject.toml` 明确列出三个包；
- `uv.lock` 包含三个直接依赖及其传递依赖；
- `uv sync --locked` 可以在不改变锁文件的情况下完成同步；
- Python 可以导入 `PIL`、`imagehash` 和 `oss2`。

## 4. 补丁二：修复 Agent Vision API

### 原因

旧实现把图片手动编码成 base64，然后尝试：

```python
from framelearn.provider_adapter import ProviderAdapter
```

但项目中不存在 `ProviderAdapter` 类，只有函数式的 `call_llm()`、`call_text_llm()` 和 `call_vision_llm()`。该导入必然失败；异常随后被 `_evaluate()` 捕获，流程静默退化为纯文本判断，所以表面上没有崩溃，实际视觉评估从未执行。

### 修改

`framelearn/pipeline/agent_keyframe_selector.py` 现在：

1. 将真实 `Path` 传入 `_call_vision_llm()`，不再在 selector 内手动生成 base64；
2. 从现有 `PROVIDERS` 中校验 `vision_provider`；
3. 使用 selector 从 `settings.toml` 读取的 provider/model 构造 `ProviderConfig`；
4. SiliconFlow 优先读取通用 `VISION_API_KEY`/`VISION_BASE_URL`，未配置时回退到 `SILICONFLOW_API_KEY`/`SILICONFLOW_BASE_URL`；其他 provider 读取通用 Vision 配置；
5. 调用现有 `call_llm(prompt, config, images=[真实图片路径], max_tokens=200)`；
6. 图片编码和各 provider 的多模态请求格式继续由统一 provider adapter 负责。

这样既消除了不存在类的引用，也避免直接调用环境驱动的 `call_vision_llm()` 时忽略 selector 中 provider/model 配置的问题。

### TDD 证据

新增测试：

```text
test/src/test_agent_keyframe.py::TestLLMDecision::test_api_vision_call_uses_existing_provider_function
```

测试先在旧代码上失败，错误为：

```text
ImportError: cannot import name 'ProviderAdapter'
```

应用补丁后，测试验证：

- API 模式调用现有 `call_llm()`；
- provider 与 model 来自 selector 配置；
- SiliconFlow API key 正确进入 `ProviderConfig`；
- `images` 参数包含真实帧路径；
- `max_tokens` 固定为 200。

## 5. 兼容性与剩余边界

- app-server 分支保持原行为，仍只发送文本；这属于原报告第 165 行之后的独立风险，不在本次范围内。
- `_evaluate()` 仍保留失败后转为文字评估的降级策略，但 API 路径不再因为确定性的缺失类导入而必然降级。
- SiliconFlow 与统一 provider adapter 保持同一环境变量契约：通用 `VISION_*` 优先，专用 `SILICONFLOW_*` 作为兼容别名。
- 本补丁没有发起真实收费 Vision API 请求；网络协议通过 provider adapter 的既有单元边界和 mock 测试验证。

## 6. 变更文件

```text
pyproject.toml
uv.lock
framelearn/pipeline/agent_keyframe_selector.py
test/src/test_agent_keyframe.py
docs/antivibe-patch-lines-153-163.md
```

## 7. 完成条件

以下条件全部满足后，本补丁视为完成：

- 定向 Vision API 回归测试通过；
- 完整 pytest 测试套件通过；
- `uv sync --locked` 通过；
- 三个新增依赖可导入；
- `git diff --check` 通过；
- 原始报告 [[antivibe-technical-report]] 保持不变，补丁历史由本文承载。
