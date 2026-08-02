# 命令解析器接口设计

> 状态：历史接口设计。当前 `CommandParser` 无构造参数，优先使用有效的 `TEXT_*` API 配置，否则使用本地规则；在线下载、教材 RAG 和内部 summarize 均未实现。请以 [`../architecture.md`](../architecture.md) 为准。

## 职责

作为 FrameLearn 的自然语言入口层，将用户的口语化描述转换为标准命令。

这是整个系统的最外层：用户不需要记忆命令格式，直接说"帮我处理这个视频"，CommandParser 理解意图后路由到对应模块。

---

## 设计理念

```
传统 CLI（需要记忆格式）     自然语言 CLI（无需记忆）
────────────────────────      ────────────────────────
framelearn run <URL>          "帮我把这个视频转成文档 <URL>"
framelearn ask <问题>         "第 3 章讲了什么"
framelearn summarize          "总结一下我学到的"

         CommandParser 统一处理，向后兼容
```

---

## 数据结构

### 输入

用户的原始输入（字符串），可能是：
- 自然语言："帮我处理这个视频 https://..."
- 传统命令："run https://..."
- 混合形式："我想问问第 3 章讲了什么"

### 输出

标准命令字符串，格式固定：

```python
# 命令格式
"run <URL>"           # 处理视频
"ask <问题>"          # 问答
"summarize"          # 总结学习
"help"               # 显示帮助

# 错误格式
"error: <原因>"      # 解析失败
```

---

## 接口

```python
class CommandParser:
    def __init__(self, llm: HelloAgentsLLM):
        """初始化解析器，使用文字模型（DeepSeek 等便宜模型）"""
        self.llm = llm

    def parse(self, user_input: str) -> str:
        """
        解析用户输入，返回标准命令。
        
        Args:
            user_input: 用户的原始输入
        
        Returns:
            标准命令字符串（"run <URL>" | "ask <问题>" | "summarize" | "help"）
        
        Raises:
            ValueError: 解析失败（缺少必要信息、意图不明确）
        """

    def is_traditional_command(self, user_input: str) -> bool:
        """
        判断是否是传统命令格式（run / ask / summarize / help 开头）
        如果是，跳过 LLM 解析，直接返回。
        """
```

---

## Prompt 设计

```python
SYSTEM_PROMPT = """
你是 FrameLearn 的命令解析器。
用户会用自然语言描述需求，你需要识别意图并输出标准命令。

支持的命令格式：
1. run <视频URL>
   - 下载视频，生成图文教材
   - URL 必须是完整的 YouTube 或 Bilibili 链接
   - 示例：run https://bilibili.com/video/BV1xx...

2. ask <问题>
   - 询问已生成教材的内容
   - 问题可以是任意自然语言
   - 示例：ask 第 3 章讲了什么

3. summarize
   - 总结最近的学习对话，创建独立笔记
   - 无需参数
   - 示例：summarize

4. help
   - 显示帮助信息
   - 示例：help

输出规则：
- 只输出命令，不要解释，不要添加任何额外文字
- 如果用户意图是处理视频但没提供 URL 或路径，输出：error: 缺少视频链接或文件路径
- 如果提供的本地路径不存在，输出：error: 文件不存在
- 如果意图完全不明确，输出：error: 无法理解意图，请明确说明需求
- 保留用户输入的原始 URL 或路径（不要修改或补全）
- 保留用户问题的原始措辞（不要改写）

示例：
输入：帮我把这个视频转成文档 https://bilibili.com/video/BV1xx...
输出：run https://bilibili.com/video/BV1xx...

输入：处理这个本地视频 /Users/iwill/Downloads/tutorial.mp4
输出：run /Users/iwill/Downloads/tutorial.mp4

输入：我想看看第 3 章为什么要用虚拟环境
输出：ask 第 3 章为什么要用虚拟环境

输入：总结一下我刚才学到的知识
输出：summarize

输入：处理这个视频
输出：error: 缺少视频链接或文件路径

输入：帮我做个饭
输出：error: 无法理解意图，请明确说明需求
"""
```

---

## 命令路由

CommandParser 只负责解析，不执行。执行由 CommandRouter 负责：

```python
class CommandRouter:
    def __init__(self):
        self.pipeline = None  # 延迟初始化
        self.qa_module = None
    
    def execute(self, command: str):
        """
        根据命令类型路由到对应模块。
        
        命令格式：
          - "run <URL或路径>" → 启动视频处理流水线
          - "ask <问题>" → 调用问答模块
          - "summarize" → 触发学习笔记总结
          - "help" → 显示帮助信息
        """
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
        """
        处理视频（URL 或本地文件）
        
        Args:
            source: 视频 URL（YouTube/Bilibili）或本地文件路径
        """
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
                raise ValueError(f"不支持的文件格式：{source}")
            pipeline_type = "file"
        
        # 延迟初始化 Pipeline
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
```

---

## 错误处理

| 情况 | 处理方式 |
|-----|---------|
| 用户输入空字符串 | 提示："请输入命令或描述您的需求" |
| LLM 返回 "error: ..." | 提取错误原因，友好提示用户 |
| LLM 返回格式不符 | 重试一次；仍失败则提示"解析失败，请用传统命令格式" |
| 传统命令格式错误 | 直接提示："命令格式错误，使用 framelearn help 查看帮助" |

---

## 使用示例

### 示例 1：自然语言处理视频

```bash
$ framelearn "帮我处理一下这个 B站视频 https://bilibili.com/video/BV1xx..."

[CommandParser] 解析意图...
[CommandParser] → run https://bilibili.com/video/BV1xx...
[CommandRouter] 启动视频处理流水线（在线视频）
🔄 正在下载视频...
✅ 已获取官方字幕，跳过 Whisper 转写
🔄 规划 Agent 分析视频结构...
```

### 示例 1b：自然语言处理本地视频

```bash
$ framelearn "处理这个本地视频 /Users/iwill/Downloads/tutorial.mp4"

[CommandParser] 解析意图...
[CommandParser] → run /Users/iwill/Downloads/tutorial.mp4
[CommandRouter] 启动视频处理流水线（本地文件）
✅ 跳过下载，使用本地文件
🔄 检查同目录字幕文件...
⚠️  未找到字幕，使用 Whisper 转写音频
🔄 规划 Agent 分析视频结构...
```

### 示例 2：自然语言提问

```bash
$ framelearn "为什么第 2 章要用虚拟环境"

[CommandParser] 解析意图...
[CommandParser] → ask 为什么第 2 章要用虚拟环境
[CommandRouter] 调用问答模块
🔍 正在检索相关内容...
📖 找到 2 段相关内容

在第二章（00:08:20）中讲解了虚拟环境的作用...
```

### 示例 3：总结学习

```bash
$ framelearn "总结一下我刚才的学习过程"

[CommandParser] 解析意图...
[CommandParser] → summarize
[CommandRouter] 触发学习笔记总结
📝 正在分析学习对话...
✅ 笔记已创建：notes/虚拟环境的作用.md
✅ 已在 tutorial.md 第 45 行插入双链
```

### 示例 4：传统命令（向后兼容）

```bash
$ framelearn run "https://youtube.com/watch?v=xxx"

[CommandParser] 检测到传统命令格式，跳过解析
[CommandRouter] 启动视频处理流水线
🔄 正在下载视频...
```

### 示例 5：错误处理

```bash
$ framelearn "帮我处理这个视频"

[CommandParser] 解析意图...
[CommandParser] → error: 缺少视频链接或文件路径
❌ 错误：缺少视频链接或文件路径
提示：请提供完整的 YouTube / Bilibili 视频链接，或本地视频文件路径

示例：
  framelearn "处理这个视频 https://bilibili.com/video/BV1xx..."
  framelearn run "https://youtube.com/watch?v=xxx"
  framelearn run "/path/to/video.mp4"
```

### 示例 5b：本地文件不存在

```bash
$ framelearn run "/Users/iwill/nonexistent.mp4"

[CommandParser] 检测到传统命令格式，跳过解析
[CommandRouter] 验证视频来源...
❌ 错误：文件不存在：/Users/iwill/nonexistent.mp4
提示：请检查文件路径是否正确
```

---

## 成本分析

每次解析调用 LLM 的开销：

```
使用 DeepSeek（推荐）：
  输入：~500 tokens（System Prompt + 用户输入）
  输出：~10 tokens（命令）
  成本：(500 + 10) × $0.14 / 1M ≈ $0.00007

使用 Claude Haiku：
  成本：(500 + 10) × $1.00 / 1M ≈ $0.0005

结论：成本极低，可以忽略
```

---

## 优化建议

### 缓存常见命令（未来优化）

如果检测到用户输入与某些常见模式匹配，直接返回，跳过 LLM：

```python
COMMON_PATTERNS = [
    (r"总结", "summarize"),
    (r"帮助|help", "help"),
    (r"(处理|转换|生成).*(https?://\S+)", r"run \2"),
]

def parse_with_cache(user_input: str) -> str:
    for pattern, command in COMMON_PATTERNS:
        if re.match(pattern, user_input):
            return command
    # 缓存未命中，调用 LLM
    return self.parse(user_input)
```

### 多轮对话支持（未来扩展）

```bash
$ framelearn "处理这个视频"
❌ 缺少视频链接

$ framelearn "https://bilibili.com/video/BV1xx..."
[检测到上文是 "处理视频"] → run https://...
```

需要维护会话状态，MVP 阶段不做。

---

## 文件位置

```
framelearn/
├── command_parser.py    # CommandParser 类
├── router.py            # CommandRouter 类
└── __main__.py          # CLI 入口，调用 parser + router
```
