# 任务列表：自然语言命令解析器

## 前置条件

- [ ] 确认项目结构（`framelearn/` 目录已创建）
- [ ] 确认依赖管理工具（uv）已安装
- [ ] 确认 HelloAgents 已添加到依赖

---

## 任务

### 1. 创建项目骨架

- [ ] 创建 `framelearn/__init__.py`
- [ ] 创建 `framelearn/__main__.py`（空骨架）
- [ ] 创建 `pyproject.toml`，添加基础依赖：
  ```toml
  [project]
  name = "framelearn"
  version = "0.1.0"
  dependencies = [
      "hello-agents>=0.1.0",
      "openai>=1.0.0",
  ]
  ```
- [ ] 运行 `uv sync` 确认依赖安装成功

---

### 2. 实现 CommandParser

**文件**：`framelearn/command_parser.py`

- [ ] 定义 `SYSTEM_PROMPT` 常量（从 `docs/modules/command_parser.md` 复制，包含本地文件支持）
- [ ] 实现 `CommandParser` 类：
  - [ ] `__init__(self, llm: HelloAgentsLLM)`
  - [ ] `parse(self, user_input: str) -> str`
  - [ ] `_is_traditional_command(self, text: str) -> bool`
  - [ ] `_parse_with_llm(self, text: str) -> str`
- [ ] 处理 `error:` 格式输出（抛出 `ValueError`）
- [ ] 添加 docstring 注释

**验收标准**：
- 传统命令（`run ...`）直接返回，不调用 LLM
- 自然语言（URL 或路径）调用 LLM，返回标准命令
- `error:` 格式正确抛出异常

---

### 3. 实现 CommandRouter

**文件**：`framelearn/router.py`

- [ ] 实现 `CommandRouter` 类：
  - [ ] `__init__(self)`（pipeline 和 qa_module 延迟初始化）
  - [ ] `execute(self, command: str)`（命令分发）
  - [ ] `_run_pipeline(self, source: str)`（区分 URL 和本地文件）
  - [ ] `_ask_question(self, question: str)`
  - [ ] `_summarize_learning(self)`
  - [ ] `_show_help(self)`
  - [ ] `_is_valid_video_url(self, url: str) -> bool`
  - [ ] `_is_video_file(self, path: str) -> bool`
- [ ] 定义 `HELP_TEXT` 常量（使用说明）

**临时实现**（主流程未完成前）：
- `_run_pipeline`：
  - URL：打印 "TODO: 在线视频处理流水线未实现"
  - 本地文件：打印 "TODO: 本地文件处理流水线未实现"
- `_ask_question`：打印 "TODO: 问答模块未实现"
- `_summarize_learning`：打印提示 "请运行：/summarize-learning"

**验收标准**：
- 命令分发正确
- URL / 本地路径正确识别和验证
- 文件存在性检查
- 视频格式验证（.mp4 / .mkv 等）
- 参数验证（URL 格式、问题非空）
- 未知命令抛出异常

---

### 4. 实现 CLI 入口

**文件**：`framelearn/__main__.py`

- [ ] 实现 `main()` 函数：
  - [ ] 解析 `sys.argv`
  - [ ] 初始化 `HelloAgentsLLM`（DeepSeek 配置）
  - [ ] 初始化 `CommandParser` 和 `CommandRouter`
  - [ ] 调用 `parser.parse()`
  - [ ] 打印解析结果（`[解析意图] → ...`）
  - [ ] 调用 `router.execute()`
  - [ ] 错误处理和友好提示
- [ ] 添加 `if __name__ == "__main__"` 入口

**环境变量配置**（`.env` 或直接硬编码测试）：
```bash
DEEPSEEK_API_KEY=your_key_here
```

**验收标准**：
- `python -m framelearn "帮我处理 https://..."` 正确解析
- `python -m framelearn run "https://..."` 跳过 LLM
- 缺少参数时友好提示

---

### 5. 单元测试

**文件**：`tests/test_command_parser.py`

- [ ] 测试传统命令识别
  ```python
  def test_traditional_command_run():
      parser = CommandParser(mock_llm)
      result = parser.parse('run "https://youtube.com/watch?v=xxx"')
      assert result == 'run "https://youtube.com/watch?v=xxx"'
  ```

- [ ] 测试自然语言解析（mock LLM 返回）
  ```python
  def test_natural_language_parse_url(mock_llm):
      mock_llm.invoke.return_value = "run https://bilibili.com/video/BV1xx"
      parser = CommandParser(mock_llm)
      result = parser.parse("帮我处理这个视频 https://...")
      assert result.startswith("run https://")
  
  def test_natural_language_parse_file(mock_llm):
      mock_llm.invoke.return_value = "run /path/to/video.mp4"
      parser = CommandParser(mock_llm)
      result = parser.parse("处理本地视频 /path/to/video.mp4")
      assert result.startswith("run /")
  ```

- [ ] 测试错误处理
  ```python
  def test_missing_source_error(mock_llm):
      mock_llm.invoke.return_value = "error: 缺少视频链接或文件路径"
      parser = CommandParser(mock_llm)
      with pytest.raises(ValueError, match="缺少视频链接或文件路径"):
          parser.parse("处理这个视频")
  ```

**文件**：`tests/test_router.py`

- [ ] 测试命令分发逻辑
- [ ] 测试 URL 验证
- [ ] 测试本地文件验证（存在性、格式）
- [ ] 测试参数缺失错误

**运行测试**：
```bash
pytest tests/ -v
```

**验收标准**：
- 所有测试通过
- 覆盖率 > 80%

---

### 6. 集成测试（手动）

- [ ] 测试自然语言输入（在线视频）：
  ```bash
  python -m framelearn "帮我处理这个视频 https://youtube.com/watch?v=dQw4w9WgXcQ"
  ```
  预期：打印 `[解析意图] → run https://...`，然后 "TODO: 在线视频处理流水线未实现"

- [ ] 测试自然语言输入（本地文件）：
  ```bash
  python -m framelearn "处理本地视频 /tmp/test.mp4"
  ```
  预期：打印 `[解析意图] → run /tmp/test.mp4`，检查文件存在性

- [ ] 测试传统命令（URL）：
  ```bash
  python -m framelearn run "https://bilibili.com/video/BV1xx..."
  ```
  预期：跳过意图识别，直接打印 TODO

- [ ] 测试传统命令（本地文件）：
  ```bash
  python -m framelearn run "/path/to/video.mp4"
  ```
  预期：跳过意图识别，验证文件存在

- [ ] 测试问答：
  ```bash
  python -m framelearn "第 3 章讲了什么"
  ```
  预期：打印 `[解析意图] → ask 第 3 章讲了什么`，然后 TODO

- [ ] 测试错误处理（缺少来源）：
  ```bash
  python -m framelearn "处理这个视频"
  ```
  预期：提示缺少视频链接或文件路径

- [ ] 测试错误处理（文件不存在）：
  ```bash
  python -m framelearn run "/nonexistent/video.mp4"
  ```
  预期：提示文件不存在

- [ ] 测试错误处理（无效格式）：
  ```bash
  python -m framelearn run "/path/to/document.pdf"
  ```
  预期：提示不支持的文件格式

- [ ] 测试帮助：
  ```bash
  python -m framelearn help
  ```
  预期：打印使用说明

**验收标准**：
- 所有手动测试场景符合预期
- 错误提示友好清晰
- URL 和本地文件路径都能正确识别

---

### 7. 文档更新

- [ ] 更新 `README.md` "使用示例" 章节：
  - 添加自然语言示例
  - 保留传统命令示例（向后兼容）
- [ ] 更新 `README.en.md` 同步英文版
- [ ] 在 `docs/architecture.md` 确认已包含 CommandParser 说明（已完成）

---

### 8. 优化与收尾

- [ ] 添加 LLM 调用超时处理（5 秒）
- [ ] 添加网络错误重试（1 次）
- [ ] 优化 System Prompt（如果测试发现解析不准）
- [ ] 添加 `--debug` 标志，打印完整 LLM 输入输出
- [ ] 代码格式化（black / ruff）
- [ ] 类型注解检查（mypy）

---

## 完成标准

- [ ] 所有单元测试通过
- [ ] 所有手动测试场景符合预期
- [ ] 文档已更新
- [ ] 代码已格式化，无 lint 错误
- [ ] `python -m framelearn "帮我处理 https://..."` 能正确解析意图

---

## 依赖其他 change

**无依赖**——这是第一个 change，CommandParser 是整个系统的入口层。

后续 change（视频处理流水线、问答模块）会调用 Router 暴露的接口，但不影响 CommandParser 的实现。

---

## 预计工作量

- 任务 1-4（核心实现）：4-6 小时
- 任务 5-6（测试）：2-3 小时
- 任务 7-8（文档和优化）：1-2 小时

**总计**：1-2 个工作日
