# 设计：自然语言命令解析器

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                      framelearn CLI                      │
│                       (__main__.py)                      │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
            ┌───────────────────────┐
            │   CommandParser       │
            │  (command_parser.py)  │
            └───────────┬───────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
  传统命令       自然语言         错误输入
  (跳过解析)    (LLM 解析)       (提示格式)
        │               │               │
        └───────────────┼───────────────┘
                        ▼
            ┌───────────────────────┐
            │   CommandRouter       │
            │     (router.py)       │
            └───────────┬───────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
    run <URL>       ask <问题>      summarize
        │               │               │
        ▼               ▼               ▼
   Pipeline         QA Module       Skill
```

---

## 模块详细设计

### 1. CommandParser

**职责**：识别用户意图，转换为标准命令。

**输入**：用户原始输入字符串
**输出**：标准命令字符串或错误信息

```python
class CommandParser:
    def __init__(self, llm: HelloAgentsLLM):
        self.llm = llm
        self.system_prompt = SYSTEM_PROMPT  # 见下文
    
    def parse(self, user_input: str) -> str:
        # 1. 快速路径：传统命令格式
        if self._is_traditional_command(user_input):
            return user_input
        
        # 2. 自然语言：调用 LLM 解析
        command = self._parse_with_llm(user_input)
        
        # 3. 验证输出格式
        if command.startswith("error:"):
            raise ValueError(command[6:].strip())
        
        return command
    
    def _is_traditional_command(self, text: str) -> bool:
        return text.strip().split()[0] in ["run", "ask", "summarize", "help"]
    
    def _parse_with_llm(self, text: str) -> str:
        prompt = f"{self.system_prompt}\n\n输入：{text}\n输出："
        return self.llm.invoke(prompt).strip()
```

**System Prompt**：

见 `docs/modules/command_parser.md` 的 Prompt 设计章节（已完整定义）。

---

### 2. CommandRouter

**职责**：根据命令类型分发到对应模块。

```python
class CommandRouter:
    def __init__(self):
        self.pipeline = None      # 延迟初始化（避免循环依赖）
        self.qa_module = None
    
    def execute(self, command: str):
        parts = command.split(maxsplit=1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        
        if cmd == "run":
            self._run_pipeline(args)
        elif cmd == "ask":
            self._ask_question(args)
        elif cmd == "summarize":
            self._summarize_learning()
        elif cmd == "help":
            self._show_help()
        else:
            raise ValueError(f"未知命令：{cmd}")
    
    def _run_pipeline(self, source: str):
        if not source:
            raise ValueError("缺少视频 URL 或文件路径")
        
        # 区分在线视频和本地文件
        if source.startswith("http"):
            # 验证 URL 格式
            if not self._is_valid_video_url(source):
                raise ValueError("无效的视频链接，仅支持 YouTube 和 Bilibili")
            pipeline_type = "url"
        else:
            # 验证本地文件
            if not os.path.isfile(source):
                raise ValueError(f"文件不存在：{source}")
            if not self._is_video_file(source):
                raise ValueError(f"不支持的文件格式，仅支持常见视频格式")
            pipeline_type = "file"
        
        # 初始化 Pipeline（延迟加载）
        if self.pipeline is None:
            from framelearn.pipeline import VideoPipeline
            self.pipeline = VideoPipeline()
        
        # 执行视频处理流水线
        if pipeline_type == "url":
            self.pipeline.run_from_url(source)
        else:
            self.pipeline.run_from_file(source)
    
    def _is_video_file(self, path: str) -> bool:
        """检查文件扩展名是否是视频格式"""
        video_exts = ['.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm']
        return any(path.lower().endswith(ext) for ext in video_exts)
    
    def _ask_question(self, question: str):
        if not question:
            raise ValueError("缺少问题内容")
        
        # 初始化 QA Module（延迟加载）
        if self.qa_module is None:
            from framelearn.qa import QAModule
            self.qa_module = QAModule()
        
        # 执行问答
        answer = self.qa_module.ask(question)
        print(answer)
    
    def _summarize_learning(self):
        # 触发 /summarize-learning skill
        # 方案：调用 Claude Code 的 skill API（如果存在）
        # 或直接提示用户手动运行
        print("📝 请运行：/summarize-learning")
    
    def _show_help(self):
        print(HELP_TEXT)
    
    def _is_valid_video_url(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url or "bilibili.com" in url
```

---

### 3. CLI 入口（__main__.py）

```python
def main():
    # 1. 解析命令行参数
    if len(sys.argv) < 2:
        print("用法：framelearn <命令或自然语言描述>")
        print("示例：")
        print('  framelearn "帮我处理这个视频 https://..."')
        print('  framelearn ask "第 3 章讲了什么"')
        sys.exit(1)
    
    user_input = " ".join(sys.argv[1:])
    
    # 2. 初始化 Parser 和 Router
    llm = HelloAgentsLLM(
        provider="deepseek",  # 便宜的文字模型
        model="deepseek-chat"
    )
    parser = CommandParser(llm)
    router = CommandRouter()
    
    # 3. 解析意图
    try:
        command = parser.parse(user_input)
        print(f"[解析意图] → {command}")
    except ValueError as e:
        print(f"❌ 错误：{e}")
        print("\n提示：使用传统命令格式：")
        print('  framelearn run "https://..."')
        print('  framelearn ask "你的问题"')
        sys.exit(1)
    
    # 4. 执行命令
    try:
        router.execute(command)
    except Exception as e:
        print(f"❌ 执行失败：{e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

---

## 数据流

### 场景 1：自然语言处理视频

```
用户输入：
  framelearn "帮我处理这个视频 https://bilibili.com/video/BV1xx..."

     ↓

CommandParser.parse():
  1. 检测不是传统命令
  2. 调用 LLM：
     输入：System Prompt + "帮我处理这个视频 https://..."
     输出："run https://bilibili.com/video/BV1xx..."
  3. 返回："run https://bilibili.com/video/BV1xx..."

     ↓

CommandRouter.execute("run https://..."):
  1. 分割：cmd = "run", args = "https://..."
  2. 检测是 URL（startswith "http"）
  3. 验证 URL 格式（Bilibili / YouTube）
  4. 调用 VideoPipeline.run_from_url(url)

     ↓

[视频处理流水线开始 - 在线视频分支]
```

### 场景 1b：自然语言处理本地文件

```
用户输入：
  framelearn "处理这个本地视频 /Users/iwill/Downloads/tutorial.mp4"

     ↓

CommandParser.parse():
  1. 检测不是传统命令
  2. 调用 LLM
  3. 返回："run /Users/iwill/Downloads/tutorial.mp4"

     ↓

CommandRouter.execute("run /Users/..."):
  1. 分割：cmd = "run", args = "/Users/..."
  2. 检测不是 URL
  3. 验证文件存在（os.path.isfile）
  4. 验证文件格式（.mp4 / .mkv 等）
  5. 调用 VideoPipeline.run_from_file(path)

     ↓

[视频处理流水线开始 - 本地文件分支]
```

### 场景 2：传统命令（向后兼容）

```
用户输入：
  framelearn run "https://youtube.com/watch?v=xxx"

     ↓

CommandParser.parse():
  1. 检测是传统命令（以 "run" 开头）
  2. 直接返回：'run "https://youtube.com/watch?v=xxx"'
  3. 跳过 LLM 调用

     ↓

CommandRouter.execute():
  [同场景 1]
```

### 场景 3：错误处理

```
用户输入：
  framelearn "处理这个视频"（缺少 URL 或路径）

     ↓

CommandParser.parse():
  1. 不是传统命令
  2. 调用 LLM
  3. LLM 输出："error: 缺少视频链接或文件路径"
  4. 抛出 ValueError("缺少视频链接或文件路径")

     ↓

main() 捕获异常：
  ❌ 错误：缺少视频链接或文件路径
  
  提示：使用传统命令格式：
    framelearn run "https://..."
    framelearn run "/path/to/video.mp4"
```

---

## 成本与性能

### LLM 调用开销

| 提供商 | 输入 tokens | 输出 tokens | 成本/次 | 延迟 |
|--------|------------|------------|---------|------|
| DeepSeek | ~500 | ~10 | $0.00007 | 200-300ms |
| Gemini Flash | ~500 | ~10 | $0.000013 | 300-500ms |
| Claude Haiku | ~500 | ~10 | $0.0005 | 400-600ms |

**推荐**：DeepSeek（成本和延迟的最佳平衡）。

### 缓存优化（未来）

如果检测到常见模式，可以跳过 LLM：

```python
COMMON_PATTERNS = {
    r"总结": "summarize",
    r"帮助|help": "help",
}

def parse_with_cache(user_input):
    for pattern, command in COMMON_PATTERNS.items():
        if re.search(pattern, user_input):
            return command
    # 缓存未命中，调用 LLM
    return self._parse_with_llm(user_input)
```

---

## 测试策略

### 单元测试

```python
def test_traditional_command():
    parser = CommandParser(llm)
    assert parser.parse('run "https://..."') == 'run "https://..."'

def test_natural_language_run():
    parser = CommandParser(llm)
    result = parser.parse("帮我处理这个视频 https://...")
    assert result.startswith("run https://")

def test_missing_url_error():
    parser = CommandParser(llm)
    with pytest.raises(ValueError, match="缺少视频链接"):
        parser.parse("处理这个视频")
```

### 集成测试

```python
def test_end_to_end_natural_language():
    # 模拟 CLI 调用
    sys.argv = ["framelearn", "帮我处理", "https://youtube.com/watch?v=xxx"]
    
    with patch("framelearn.pipeline.VideoPipeline.run") as mock_run:
        main()
        mock_run.assert_called_once()
```

---

## 错误恢复

| 错误类型 | 检测方式 | 恢复策略 |
|---------|---------|---------|
| LLM 返回格式错误 | 不以已知命令开头 | 重试一次；仍失败则提示用传统命令 |
| LLM 超时 | 超过 5 秒无响应 | 回退到传统命令提示 |
| LLM API 不可用 | 网络错误 | 提示"意图识别服务不可用，请使用传统命令" |
| 命令参数缺失 | Router 验证失败 | 提示缺少什么参数，给出正确示例 |

---

## 依赖

- `HelloAgentsLLM`（多提供商 LLM 接口）
- 无其他外部依赖（纯 Python 标准库）

---

## 未来扩展

### 多轮对话

```bash
$ framelearn "处理这个视频"
❌ 缺少视频链接，请提供 URL

$ framelearn "https://..."
[检测到上文] → run https://...
```

需要：
- 会话状态管理
- 上下文记忆（最近 N 轮对话）

### 语音输入

```bash
$ framelearn --voice
[录音中...] "帮我处理这个视频 https://..."
[解析意图] → run https://...
```

需要：
- Whisper 语音转文字
- 音频录制接口
