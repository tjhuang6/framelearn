# Codex App-Server 模式复刻指南

> 目标：帮助你在自己的程序中实现与 Hermes Agent `codex_app_server` 模式相同的架构：由宿主程序负责 UI、会话、审批与持久化，把单轮 Agent 执行交给 `codex app-server` 子进程。
>
> 本文基于本机 Hermes Agent 源码提交 `3572d4bca14307dabbfa04a5ab9326f4ef957b4f`，以及本机 `codex-cli 0.145.0`。Hermes 源码仓库：<https://github.com/NousResearch/hermes-agent>。

## 1. 先说结论：代码在哪里

本机 Hermes 源码根目录：

```text
/Users/iwill/.hermes/hermes-agent
```

App-server 模式最关键的实现文件如下。

### 1.1 核心文件

| 文件 | 作用 |
|---|---|
| `agent/transports/codex_app_server.py` | 最底层 JSON-RPC stdio 客户端；启动 `codex app-server`，收发请求、响应、通知和 stderr |
| `agent/transports/codex_app_server_session.py` | 会话适配层；一个 Hermes 会话对应一个 Codex thread；执行 `thread/start`、`turn/start`、审批、取消、超时、压缩 |
| `agent/transports/codex_event_projector.py` | 把 Codex 的 `item/*` 事件投影成宿主程序可持久化的 user/assistant/tool 消息 |
| `agent/codex_runtime.py` | Hermes 与 Codex 会话适配器之间的编排桥；UI 流式事件、消息落库、token 统计、后台记忆审查 |
| `agent/conversation_loop.py` | 运行时入口；当 `api_mode == "codex_app_server"` 时提前返回，绕过 Hermes 默认工具循环 |

对应的绝对路径：

```text
/Users/iwill/.hermes/hermes-agent/agent/transports/codex_app_server.py
/Users/iwill/.hermes/hermes-agent/agent/transports/codex_app_server_session.py
/Users/iwill/.hermes/hermes-agent/agent/transports/codex_event_projector.py
/Users/iwill/.hermes/hermes-agent/agent/codex_runtime.py
/Users/iwill/.hermes/hermes-agent/agent/conversation_loop.py
```

### 1.2 配置、启用与工具迁移

| 文件 | 作用 |
|---|---|
| `hermes_cli/runtime_provider.py` | 将 OpenAI/OpenAI-Codex Provider 的 `api_mode` 改写成 `codex_app_server` |
| `hermes_cli/codex_runtime_switch.py` | `/codex-runtime` 开关、Codex CLI 版本检查、配置持久化 |
| `hermes_cli/codex_runtime_plugin_migration.py` | 将 MCP server、Codex 插件和 Hermes 工具回调写入 `~/.codex/config.toml` |
| `agent/transports/hermes_tools_mcp_server.py` | 将部分 Hermes 工具作为 stdio MCP server 暴露给 Codex |

### 1.3 会话数据库

| 文件 | 作用 |
|---|---|
| `hermes_state.py` | `SessionDB`、`append_message()`、消息查询和 FTS 搜索 |
| `hermes_state_common.py` | `state.db` 表结构 |
| `tests/agent/test_codex_app_server_persist.py` | 验证用户消息与 Codex 投影消息恰好写入一次 |

### 1.4 推荐先看的测试

```text
tests/agent/transports/test_codex_app_server_session.py
tests/agent/transports/test_codex_app_server_runtime.py
tests/agent/transports/test_codex_event_projector.py
tests/agent/test_codex_app_server_persist.py
tests/agent/test_codex_app_server_event_bridge.py
tests/run_agent/test_codex_app_server_integration.py
tests/run_agent/test_codex_app_server_compaction.py
```

测试比主代码更适合快速理解协议输入、事件格式和边界行为。

---

## 2. App-server 模式的本质

它不是“你的程序实现一个 OpenAI API server”，也不是“把 Hermes 变成 HTTP server”。

它的本质是：

1. 你的程序启动本地子进程：

   ```bash
   codex app-server
   ```

2. 你的程序通过子进程的 stdin/stdout 使用“每行一个 JSON 对象”的 JSON-RPC 协议通信。
3. Codex app-server 自己负责：
   - 调用模型；
   - 维护 Agent thread；
   - 模型工具循环；
   - shell 命令；
   - 文件修改；
   - 沙箱；
   - MCP 工具调用；
   - 上下文压缩。
4. 你的程序负责：
   - UI；
   - 用户输入；
   - 会话列表；
   - 本地聊天记录；
   - 审批提示；
   - 将 Codex 事件转换为自己的消息格式；
   - 中断、超时、错误恢复；
   - 可选的 MCP 工具回调。

架构图：

```text
┌────────────────────────────────────────────────────────────┐
│ 你的宿主程序                                                │
│                                                            │
│ UI / CLI / WebSocket / 会话数据库 / 审批 / 日志 / 恢复       │
│                                                            │
│  RuntimeAdapter                                             │
│       │                                                     │
│       ▼                                                     │
│  AppServerSession                                           │
│       │ thread/start、turn/start、turn/interrupt              │
│       ▼                                                     │
│  JsonRpcStdioClient                                         │
└───────┬────────────────────────────────────────────────────┘
        │ stdin/stdout：newline-delimited JSON-RPC
        ▼
┌────────────────────────────────────────────────────────────┐
│ codex app-server 子进程                                     │
│                                                            │
│ 模型调用 / thread / turn / shell / apply_patch / sandbox     │
│                         │                                  │
│                         ▼                                  │
│                    MCP clients                             │
└─────────────────────────┬──────────────────────────────────┘
                          │ stdio / HTTP MCP
                          ▼
                 你自己的 MCP 工具服务
```

---

## 3. 分层设计

不要把所有逻辑写在一个类里。建议至少拆成下面五层。

```text
src/
  app_server/
    jsonrpc_client.py       # 只处理进程和 JSON-RPC
    session.py              # thread/turn 生命周期
    projector.py            # Codex 事件 -> 本地消息
    runtime.py              # 与你的应用核心衔接
    approvals.py            # 审批策略和 UI
    persistence.py          # 会话和消息数据库
  mcp/
    host_tools_server.py    # 可选：把宿主工具提供给 Codex
```

### 3.1 `JsonRpcStdioClient`

只做以下事情：

- 启动 `codex app-server`；
- 写 stdin；
- 读取 stdout；
- 单独读取 stderr；
- 根据消息形状分流：
  - 请求响应；
  - server 主动请求；
  - notification；
- 管理 request id 和超时；
- 关闭或杀死子进程。

这一层不要处理：会话数据库、UI、工具消息投影、审批业务策略。

### 3.2 `AppServerSession`

这一层负责一个逻辑会话：

- 第一次使用时启动客户端；
- `initialize` 握手；
- `thread/start`；
- 多次复用同一个 thread 执行 `turn/start`；
- 消费通知流；
- 回应 Codex 发来的审批请求；
- 处理 `turn/interrupt`；
- 检查子进程是否死亡；
- 处理 turn 总超时和工具后静默超时；
- 返回标准化的 `TurnResult`。

### 3.3 `EventProjector`

这一层把 Codex 事件转换成你的内部聊天记录格式。

Codex 的流式 delta 主要用于 UI，不应该每个 delta 都落库。Hermes 只在 `item/completed` 时生成可持久化消息。

### 3.4 `RuntimeAdapter`

把 App-server 路径接入你的主程序：

- 用户消息先写数据库；
- 调用 `session.run_turn()`；
- 把投影后的 assistant/tool 消息写数据库；
- 更新 token、模型调用次数、会话标题等；
- 返回与你其他模型运行时一致的结果结构。

### 3.5 `HostToolsMcpServer`

Codex 已经有 shell、文件操作、patch 等内置能力，不要重复提供。

只把 Codex 本身没有的能力暴露成 MCP，例如：

- 浏览器自动化；
- 企业内部 API；
- 数据库查询；
- 搜索服务；
- 日历、邮件；
- 你程序自己的业务工具。

---

## 4. JSON-RPC stdio 传输

### 4.1 启动子进程

Hermes 的核心方式等价于：

```python
proc = subprocess.Popen(
    ["codex", "app-server"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    bufsize=0,
    env=spawn_env,
)
```

重要要求：

1. stdin、stdout、stderr 必须全部使用 pipe。
2. stdout 是协议线路，不能混入普通日志。
3. stderr 单独读取并保留末尾若干行，便于错误诊断。
4. 不要盲目把所有宿主程序密钥传给 Codex 子进程。
5. `HOME` 一般保持用户真实 HOME，否则 `git`、`gh`、`npm`、`aws` 等命令可能找不到用户配置。
6. 如需隔离 Codex 状态，只设置 `CODEX_HOME`，不要重写 `HOME`。

### 4.2 消息编码

Hermes 使用 newline-delimited JSON：

```python
payload = json.dumps(obj, ensure_ascii=False) + "\n"
proc.stdin.write(payload.encode("utf-8"))
proc.stdin.flush()
```

读取：

```python
for raw_line in iter(proc.stdout.readline, b""):
    line = raw_line.strip()
    if not line:
        continue
    message = json.loads(line)
    dispatch(message)
```

### 4.3 三类入站消息

#### A. 对宿主请求的响应

形状：有 `id`，有 `result` 或 `error`，没有 `method`。

```json
{"id": 2, "result": {"thread": {"id": "thread-123"}}}
```

做法：根据 `id` 找到 pending request，把响应放入对应 Future/Queue。

#### B. Codex 主动向宿主发起的请求

形状：有 `id` 和 `method`。

```json
{
  "id": "approval-1",
  "method": "item/commandExecution/requestApproval",
  "params": {"command": "rm file", "cwd": "/workspace"}
}
```

做法：放入 `server_requests` 队列。宿主必须返回响应，否则 Codex 会一直等待。

#### C. Notification

形状：有 `method`，没有 `id`。

```json
{
  "method": "item/agentMessage/delta",
  "params": {"delta": "你好"}
}
```

做法：放入 `notifications` 队列，由当前 turn 循环消费。

### 4.4 最小客户端骨架

下面是可直接作为起点的简化版；生产版本还需要锁、超时清理、stderr 脱敏、Windows 子进程标志和健壮关闭。

```python
from __future__ import annotations

import json
import queue
import subprocess
import threading


class RpcError(RuntimeError):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(f"RPC {code}: {message}")
        self.code = code
        self.data = data


class JsonRpcStdioClient:
    def __init__(self, command=("codex", "app-server"), env=None):
        self.proc = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=env,
        )
        self.next_id = 1
        self.pending: dict[int, queue.Queue] = {}
        self.pending_lock = threading.Lock()
        self.notifications = queue.Queue()
        self.server_requests = queue.Queue()
        self.stderr_lines: list[str] = []

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _send(self, obj: dict):
        if self.proc.stdin is None:
            raise RuntimeError("stdin unavailable")
        wire = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
        self.proc.stdin.write(wire)
        self.proc.stdin.flush()

    def request(self, method: str, params=None, timeout=30.0):
        with self.pending_lock:
            request_id = self.next_id
            self.next_id += 1
            result_queue = queue.Queue(maxsize=1)
            self.pending[request_id] = result_queue

        self._send({"id": request_id, "method": method, "params": params or {}})
        try:
            response = result_queue.get(timeout=timeout)
        except queue.Empty:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise TimeoutError(f"{method} timed out")

        if "error" in response:
            error = response["error"]
            raise RpcError(error.get("code", -1), error.get("message", ""), error.get("data"))
        return response.get("result", {})

    def notify(self, method: str, params=None):
        self._send({"method": method, "params": params or {}})

    def respond(self, request_id, result: dict):
        self._send({"id": request_id, "result": result})

    def respond_error(self, request_id, code=-32601, message="unsupported"):
        self._send({"id": request_id, "error": {"code": code, "message": message}})

    def _read_stdout(self):
        assert self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
                with self.pending_lock:
                    waiter = self.pending.pop(msg["id"], None)
                if waiter:
                    waiter.put_nowait(msg)
            elif "id" in msg and "method" in msg:
                self.server_requests.put(msg)
            elif "method" in msg:
                self.notifications.put(msg)

    def _read_stderr(self):
        assert self.proc.stderr is not None
        for raw in iter(self.proc.stderr.readline, b""):
            self.stderr_lines.append(raw.decode("utf-8", "replace").rstrip())
            self.stderr_lines = self.stderr_lines[-500:]

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
```

---

## 5. 初始化和 thread 生命周期

### 5.1 初始化握手

启动进程后先发：

```json
{
  "id": 1,
  "method": "initialize",
  "params": {
    "clientInfo": {
      "name": "my-app",
      "title": "My Agent App",
      "version": "0.1.0"
    },
    "capabilities": {}
  }
}
```

收到响应后，再发 notification：

```json
{"method": "initialized", "params": {}}
```

不要重复 initialize 同一个连接。

### 5.2 创建 thread

Hermes 使用：

```json
{
  "id": 2,
  "method": "thread/start",
  "params": {
    "cwd": "/absolute/path/to/workspace"
  }
}
```

不同 Codex 版本可能在不同字段返回 thread id。Hermes 做了兼容读取：

```python
thread_obj = result.get("thread") or {}
thread_id = (
    thread_obj.get("id")
    or thread_obj.get("sessionId")
    or result.get("sessionId")
    or result.get("threadId")
)
```

### 5.3 一个应用会话对应一个 Codex thread

建议的数据关系：

```text
application_session_id 1 ─── 1 codex_thread_id
codex_thread_id        1 ─── N codex_turn_id
```

不要每条用户消息都重新 `thread/start`，否则 Codex 会丢失自己的 thread 上下文。

Hermes 的行为是：

- 第一次 turn 时懒启动 app-server；
- 创建一个 thread；
- 后续 turn 复用同一个进程和 thread；
- 进程异常、OAuth 失败、超时或静默卡死时，关闭并废弃会话适配器；
- 下一轮重新启动干净的子进程和 thread。

注意：如果你重启自己的程序，只存储 thread id 并不一定足以恢复内存中的 app-server 连接。完整恢复需要查阅当前 Codex app-server 协议是否支持 thread resume/load，并按当前版本实现；不要假设 `thread/start` 能接收旧 thread id。

---

## 6. 执行一个 turn

### 6.1 发起 turn

Hermes 当前常规文本路径：

```json
{
  "id": 3,
  "method": "turn/start",
  "params": {
    "threadId": "thread-123",
    "input": [
      {"type": "text", "text": "请检查项目并修复测试"}
    ]
  }
}
```

响应中通常返回：

```json
{"turn": {"id": "turn-456"}}
```

记录这个 turn id，用来：

- 过滤属于当前 turn 的 notification；
- 中断当前 turn；
- 关联日志、token 和数据库记录。

### 6.2 turn 主循环

伪代码：

```python
def run_turn(text):
    ensure_started()
    response = rpc.request("turn/start", {
        "threadId": thread_id,
        "input": [{"type": "text", "text": text}],
    }, timeout=10)
    turn_id = response["turn"]["id"]

    result = TurnResult(thread_id=thread_id, turn_id=turn_id)
    deadline = monotonic() + 600

    while monotonic() < deadline:
        if user_cancelled:
            rpc.request("turn/interrupt", {
                "threadId": thread_id,
                "turnId": turn_id,
            }, timeout=5)
            result.interrupted = True
            break

        if process_is_dead():
            result.error = "app-server exited"
            result.should_retire = True
            break

        server_request = take_server_request_nonblocking()
        if server_request:
            handle_server_request(server_request)
            continue

        event = take_notification(timeout=0.25)
        if not event:
            continue

        if not belongs_to_current_turn(event, thread_id, turn_id):
            continue

        emit_live_ui_event(event)
        capture_token_usage(event)
        projected = projector.project(event)
        result.messages.extend(projected.messages)

        if projected.final_text is not None:
            result.final_text = projected.final_text

        if event["method"] == "turn/completed":
            break

    return result
```

### 6.3 事件归属过滤

必须检查 notification 中的 `threadId` 和 `turnId`。

原因：一个 app-server 连接可能出现父 thread、子 thread、压缩 turn 或旧 turn 的延迟事件。如果不筛选，可能把子任务的消息错误写进主会话。

原则：

- 事件明确携带 thread id，且不等于当前 thread：忽略；
- 事件明确携带 turn id，且不等于当前 turn：忽略；
- 某些全局事件没有 turn id，例如 token 更新，需要按其协议语义单独处理。

### 6.4 完成判定

正常终止条件：

```text
method == turn/completed
```

还应处理：

- `turn.status == completed`：成功；
- `turn.status == interrupted`：中断；
- 其他状态：读取 `turn.error`；
- 某些版本可能输出 `<turn_aborted>` 文本但没有正常 `turn/completed`；
- 收到完整 agentMessage 后长时间收不到 `turn/completed`，可以保留最终文本并记录协议异常；
- 到达总 deadline 后发送 `turn/interrupt`，废弃当前 app-server 会话。

### 6.5 工具后静默 watchdog

Hermes 在工具完成后，如果 Codex 长时间没有后续事件，会主动判定进程卡死。

推荐：

- turn 总超时：例如 600 秒；
- notification poll：例如 250 ms；
- 工具完成后静默超时：例如 90 秒。

触发后：

1. 发送 `turn/interrupt`；
2. 标记当前 turn 为 partial/interrupted；
3. 关闭 app-server 子进程；
4. 下一 turn 重新启动。

---

## 7. 实时 UI 事件与持久化消息要分开

### 7.1 用于 UI 的流式事件

典型映射：

| Codex 事件 | UI 行为 |
|---|---|
| `item/agentMessage/delta` | 增量渲染助手文本 |
| `item/reasoning/delta` | 增量渲染 reasoning |
| `item/reasoning/summaryDelta` | 增量渲染 reasoning 摘要 |
| `item/started` + 工具类型 | 显示工具开始卡片 |
| `item/completed` + 工具类型 | 显示工具完成、耗时和结果 |
| `item/completed` + `agentMessage` | 确认一条完整助手消息 |

UI callback 报错不应该中断 Agent turn。所有显示回调都应该被 try/except 隔离。

### 7.2 用于数据库的消息

只在 `item/completed` 时物化消息。不要把每个 delta 都写入数据库，否则会产生：

- 大量碎片记录；
- 重复文本；
- 恢复会话困难；
- FTS 索引膨胀；
- assistant/tool 消息关联混乱。

---

## 8. Codex 事件投影规则

建议统一成 OpenAI 风格内部消息：

```python
{"role": "user", "content": "..."}
{"role": "assistant", "content": "..."}
{"role": "assistant", "content": None, "tool_calls": [...]}
{"role": "tool", "tool_call_id": "...", "content": "..."}
```

### 8.1 `agentMessage`

输入：

```json
{
  "method": "item/completed",
  "params": {
    "item": {"type": "agentMessage", "id": "a1", "text": "完成了"}
  }
}
```

投影：

```json
{"role": "assistant", "content": "完成了"}
```

并把该文本设为当前 turn 的 `final_text`。同一 turn 有多个 agentMessage 时，以最后一个为最终文本。

### 8.2 `reasoning`

不要单独生成聊天消息。暂存其 `summary` 和 `content`，附加到下一条 assistant 消息的内部 `reasoning` 字段，然后清空暂存。

是否将 reasoning 持久化、展示给用户，应由你的产品隐私策略决定。

### 8.3 `commandExecution`

投影为两条消息：

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [{
    "id": "codex_exec_<item-id>",
    "type": "function",
    "function": {
      "name": "exec_command",
      "arguments": "{\"command\":\"...\",\"cwd\":\"...\"}"
    }
  }]
}
```

```json
{
  "role": "tool",
  "tool_call_id": "codex_exec_<item-id>",
  "content": "命令输出"
}
```

非零退出码应写入结果，例如：

```text
[exit 1]
<output>
```

### 8.4 `fileChange`

投影为 `apply_patch` tool call + tool result。

数据库里建议只记录：

- add/update/delete；
- 文件路径；
- 状态；
- 变更数量。

不要默认把完整文件内容或巨大 patch 塞进聊天记录。

### 8.5 `mcpToolCall`

投影名称建议带 server namespace：

```text
mcp.<server>.<tool>
```

例如：

```text
mcp.github.search_issues
```

结果过大时限制长度，完整结果可单独存 artifact 表或文件。

### 8.6 `dynamicToolCall`

投影为普通 tool call，工具名使用 Codex 事件中的 `tool`。

### 8.7 未知 item 类型

不要假装它是已知工具。可记录为普通 assistant note：

```text
[codex <itemType>] <截断后的 JSON>
```

这样既不丢失行为痕迹，也不会伪造错误的 tool-call 结构。

### 8.8 稳定的 tool call id

同一个 item 必须始终生成相同 tool call id。Hermes 直接使用 Codex item id：

```python
def stable_call_id(kind: str, item_id: str) -> str:
    if item_id:
        return f"codex_{kind}_{item_id}"
    # 没有 item id 时才使用确定性的 hash fallback
```

不要使用随机 UUID。否则：

- UI 工具卡与恢复后的历史对不上；
- assistant tool call 与 tool result 无法稳定关联；
- prompt cache/replay 可能不稳定。

---

## 9. 审批桥接

Codex 会向宿主发送 server-initiated JSON-RPC request。常见方法：

```text
item/commandExecution/requestApproval
item/fileChange/requestApproval
item/permissions/requestApproval
mcpServer/elicitation/request
```

### 9.1 命令审批

收到：

```json
{
  "id": "approval-1",
  "method": "item/commandExecution/requestApproval",
  "params": {
    "command": "...",
    "cwd": "/workspace",
    "reason": "..."
  }
}
```

返回：

```json
{"id": "approval-1", "result": {"decision": "accept"}}
```

Codex decision 值：

| 你的 UI 选择 | Codex wire 值 |
|---|---|
| 仅本次允许 | `accept` |
| 本会话允许 | `acceptForSession` |
| 拒绝 | `decline` |

### 9.2 文件修改审批

`fileChange` 的审批请求不一定带完整 changeset。Hermes 的做法：

1. 在 `item/started` 且 item.type 为 `fileChange` 时缓存变更摘要；
2. 以 item id 为键；
3. 收到 `item/fileChange/requestApproval` 时查摘要并展示；
4. `item/completed` 后删除缓存。

### 9.3 权限升级审批

Hermes 默认拒绝 `item/permissions/requestApproval`，避免模型在 turn 中意外扩大权限。权限 profile 由用户在 Codex 配置里预先选择。

### 9.4 默认必须 fail closed

如果你的程序没有交互式 UI，或者审批 callback 不可用，默认返回 `decline`，不要自动允许。

只有用户明确开启类似 `--yolo` / approvals off 的模式时，才考虑自动接受，并仍让 Codex 沙箱作为最后一道边界。

### 9.5 未知 server request

一定要返回 JSON-RPC error，而不是忽略，否则 Codex 会永久等待：

```json
{
  "id": "unknown-1",
  "error": {
    "code": -32601,
    "message": "Unsupported method"
  }
}
```

---

## 10. 中断与运行中 steer

### 10.1 中断

```json
{
  "id": 10,
  "method": "turn/interrupt",
  "params": {
    "threadId": "thread-123",
    "turnId": "turn-456"
  }
}
```

中断必须幂等。用户连续按多次 Ctrl+C 时，不应产生状态崩溃。

### 10.2 运行中追加指导

Hermes 支持：

```json
{
  "id": 11,
  "method": "turn/steer",
  "params": {
    "threadId": "thread-123",
    "expectedTurnId": "turn-456",
    "input": [{"type": "text", "text": "不要修改数据库，只修测试"}]
  }
}
```

只有返回的 turn id 与当前 turn 匹配时才视为成功。

---

## 11. Token 使用量与上下文压缩

### 11.1 Token usage

Codex app-server 不一定把 usage 放在 `turn/completed` 中。Hermes监听：

```text
thread/tokenUsage/updated
```

典型数据包含：

```text
tokenUsage.last
tokenUsage.total
tokenUsage.modelContextWindow
```

字段可能包括：

```text
totalTokens
inputTokens
cachedInputTokens
outputTokens
reasoningOutputTokens
```

你的计费和上下文统计层应把它标准化成内部字段。

### 11.2 原生 thread 压缩

因为真实上下文由 Codex thread 持有，仅压缩你自己的聊天数据库并不能缩小 Codex 的模型上下文。

Hermes 会使用：

```json
{
  "id": 12,
  "method": "thread/compact/start",
  "params": {"threadId": "thread-123"}
}
```

之后继续消费 turn/item notification，直到相应的 `turn/completed`。

需要识别：

- `thread/compacted`；
- `item/started` 或 `item/completed` 且 `item.type == contextCompaction`。

---

## 12. 会话持久化：如何避免重复消息

这是最容易出错的部分。

### 12.1 推荐写入顺序

```text
1. 用户输入到达
2. 立即把 user 消息写入本地数据库
3. 调用 app-server turn/start
4. 消费流式事件，只更新 UI
5. item/completed 时生成 projected messages
6. turn 结束后把新增 assistant/tool 消息批量写入数据库
7. 标记这些内存消息已持久化
```

### 12.2 恰好一次写入

Hermes 曾经专门修复过两类问题：

- App-server 提前返回，导致 assistant/tool 投影消息没有写入数据库；
- Agent 层写了一次，gateway 又写一次，导致 user 消息重复。

你的系统应明确指定一个“唯一持久化责任方”。推荐：RuntimeAdapter 负责写入，并向上层返回：

```python
{"persisted": True}
```

上层看到它后不得再次写入同一 turn。

更稳妥的数据库设计：

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    provider_item_id TEXT,
    timestamp REAL NOT NULL,
    UNIQUE(session_id, provider_item_id, role)
);
```

Hermes 当前 `append_message` 本身是 raw INSERT，因此在内存消息上使用已持久化 marker 避免重复。你自己的程序最好再增加数据库唯一约束或幂等 key。

### 12.3 建议保存的关联 ID

每个 turn 最少保存：

```text
application_session_id
codex_thread_id
codex_turn_id
provider_item_id
tool_call_id
message role
content / tool_calls / tool result
created_at
```

### 12.4 Reasoning 的隐私

Hermes 的 schema 可以存 `reasoning`。你的程序应明确决定：

- 是否保存 reasoning；
- 是否仅保存 summary；
- 是否加密；
- 是否允许用户关闭；
- 导出/删除会话时是否一并删除。

---

## 13. MCP 工具回调

### 13.1 为什么需要 MCP

App-server 模式中，Codex 自己拥有工具循环，所以它不会自动看到你宿主程序里的函数。

正确做法是：把宿主工具暴露成 MCP server，然后写入 `~/.codex/config.toml`。

### 13.2 Codex 配置示例

```toml
[mcp_servers.my-host-tools]
command = "/absolute/path/to/python"
args = ["-m", "my_app.host_tools_mcp_server"]
startup_timeout_sec = 30.0
tool_timeout_sec = 600.0

[mcp_servers.my-host-tools.env]
MY_APP_HOME = "/path/to/app-state"
QUIET = "1"
```

注意：stdio MCP 的 stdout 也是协议线路，日志必须写 stderr。

### 13.3 工具暴露原则

应暴露：

- Codex 没有的业务能力；
- 可以无状态调用的函数；
- 权限边界明确的服务。

不应重复暴露：

- shell；
- 文件读写；
- patch；
- 目录搜索。

因为 Codex 已原生支持这些能力，重复提供会造成：

- 工具选择混乱；
- 两套审批策略；
- 两套沙箱边界；
- 记录格式不一致。

### 13.4 有状态 Agent 工具的限制

Hermes 不通过无状态 MCP callback 暴露下面几类工具：

```text
delegate_task
memory
session_search
todo
```

原因不是 MCP 做不到调用，而是这些工具依赖正在运行的宿主 Agent 内部状态。若你的工具也依赖当前 turn 的对象、锁、上下文或回调，不能只启动一个无状态 MCP 子进程然后直接调用。

可选解决方案：

1. 将状态放进数据库/服务，通过 session id 显式访问；
2. MCP server 通过本地 socket/HTTP 回调正在运行的主进程；
3. 给每个 app-server turn 注入一次性 capability token；
4. 不在 app-server 模式开放该工具。

---

## 14. 配置迁移和安全写文件

Hermes 启用 app-server 时会把以下内容写入 `~/.codex/config.toml`：

- 默认权限 profile；
- 用户 MCP servers；
- 宿主工具 MCP server；
- 已安装的 Codex 原生插件。

建议只管理一个明确标记的区块：

```toml
# managed by my-app — regenerated automatically
# ...
# end my-app managed section
```

原则：

1. 重新生成时只替换自己的 managed block；
2. 保留用户在 managed block 外的内容；
3. 使用同目录临时文件 + rename 原子替换；
4. 不要直接覆盖用户整个 `config.toml`；
5. 不要在多个位置生成重复 TOML table；
6. 写入前最好解析或至少验证 TOML；
7. MCP server 的凭据尽量通过受控环境或独立 secret store 注入。

---

## 15. 子进程环境与安全边界

### 15.1 凭据最小化

Codex 子进程需要模型认证，但不应该继承宿主的全部 secrets。

建立 allowlist，而不是把 `os.environ.copy()` 原样交给它。至少重新评估：

```text
机器人 token
网关 token
数据库密码
云基础设施 token
后台辅助模型 key
内部 webhook secret
```

### 15.2 HOME 与 CODEX_HOME

推荐：

```text
HOME       = 用户真实 HOME
CODEX_HOME = Codex 状态目录，可选择隔离
```

不要为了隔离 Codex 而重写 HOME，否则其 shell 中的工具可能失去用户配置。

### 15.3 沙箱

优先使用 workspace-write/read-only 等受限模式，不要默认使用完全无沙箱模式。

如果必须让 Codex 写工作区外的某个目录，只增加特定 writable root，不要关闭整个沙箱。

### 15.4 stderr 脱敏

错误回显给用户前，应过滤：

- Authorization header；
- Bearer token；
- API key；
- cookie；
- OAuth refresh token；
- 内部 endpoint 中的凭据。

---

## 16. 错误恢复策略

将错误分成两类。

### 16.1 当前 turn 失败但进程可继续复用

例如：

- 普通模型错误；
- 某个工具返回失败；
- 用户拒绝审批。

保留 session，下一 turn 继续。

### 16.2 必须 retire 当前 app-server session

例如：

- 子进程退出；
- `initialize`/`thread/start` 超时；
- `turn/start` 卡死；
- OAuth refresh 失败；
- turn 达到总 deadline；
- 工具完成后长期静默；
- 协议线路损坏；
- stdout 出现无法恢复的非 JSON 数据。

处理：

```text
interrupt（如果可能）
close stdin
terminate
等待短时间
kill（如仍未退出）
清空 client/thread 状态
下一 turn 懒启动新进程
```

不要复用已被判定 wedged 的客户端。

---

## 17. 最小可行产品实施顺序

### 阶段 1：只做文本聊天

实现：

- 启动 `codex app-server`；
- initialize/initialized；
- thread/start；
- turn/start；
- 显示 `item/agentMessage/delta`；
- 从 `item/completed(agentMessage)` 取得最终文本；
- 等待 `turn/completed`；
- Ctrl+C -> turn/interrupt。

验收：同一个 thread 连续对话两轮，第二轮能利用第一轮上下文。

### 阶段 2：工具事件和审批

实现：

- commandExecution；
- fileChange；
- item/started 和 item/completed UI；
- exec/apply_patch 审批；
- 默认 fail closed；
- stderr 诊断。

验收：让模型创建文件；批准后成功，拒绝后不修改。

### 阶段 3：会话数据库

实现：

- user 消息先落库；
- completed item 投影；
- assistant/tool 恰好一次落库；
- thread/turn/item id 关联；
- 会话恢复 UI。

验收：重启程序后聊天记录完整，无重复 user 消息。

### 阶段 4：MCP 工具

实现：

- 一个最小 stdio MCP server；
- 写入 Codex 配置 managed block；
- Codex 能调用你的自定义工具；
- MCP 调用投影到数据库。

验收：模型调用一个宿主业务工具并显示结果。

### 阶段 5：生产可靠性

实现：

- token usage；
- native compaction；
- watchdog；
- OAuth 错误分类；
- 子进程 retire/restart；
- 事件 thread/turn 过滤；
- 原子配置写入；
- secret allowlist；
- Windows/macOS/Linux 兼容。

---

## 18. 必须写的测试

### 18.1 JSON-RPC client

- request id 正确关联乱序响应；
- response、server request、notification 正确分流；
- 请求超时会清理 pending map；
- stderr 不污染 stdout；
- broken pipe 正确报错；
- close/terminate/kill 幂等。

### 18.2 Session

- initialize 和 thread/start 只执行一次；
- 同一 thread 多 turn 复用；
- turn/completed 正常退出；
- 外部 thread/turn 事件被忽略；
- interrupt 正确发送；
- 死进程快速失败；
- 工具后静默触发 retire；
- 未知 server request 返回 -32601；
- 没有审批 UI 时默认拒绝。

### 18.3 Projector

- delta 不落库；
- agentMessage 生成 assistant；
- commandExecution 生成 assistant tool call + tool result；
- tool_call_id 两侧一致；
- fileChange 不内联大文件；
- MCP 错误可见；
- reasoning 只附到下一条 assistant；
- 未知 item 不伪造工具；
- 稳定 item id 产生稳定 tool call id。

### 18.4 Persistence

- user 消息一次；
- assistant 消息一次；
- tool 消息一次；
- gateway/上层不重复写；
- turn 中断后 partial 消息策略明确；
- FTS 能搜索 app-server 生成的消息。

### 18.5 Live test

至少保留一个由环境变量显式启用的真实 Codex 测试：

```text
检查 codex --version
启动 app-server
执行简单 turn
执行 shell 工具
收到 agentMessage
收到 turn/completed
关闭进程
```

不要让真实模型测试默认在每次单元测试中运行。

---

## 19. 不建议直接逐行复制的部分

如果你的程序不是 Hermes，不要照抄这些 Hermes 特有逻辑：

- `AIAgent` 的 memory/skill nudge counter；
- gateway 的 `agent_persisted` 约定；
- Hermes 的工具 schema 注册系统；
- Hermes 的 `state.db` 完整 schema；
- Kanban 环境变量和 writable root；
- Hermes display callback 名称；
- Hermes Provider credential pool。

应该复制的是设计模式：

```text
JSON-RPC transport
session/thread/turn adapter
server-request approval bridge
event projector
exactly-once persistence
MCP callback
watchdog + retire
```

---

## 20. 版本兼容策略

Codex app-server 是随 Codex CLI 演进的协议面，不能把某一版本的所有字段当作永恒不变。

建议：

1. 启动前运行 `codex --version`；
2. 设定最低支持版本；
3. 保存服务端 initialize 返回的信息；
4. 对 thread id 做多字段兼容读取；
5. 未知 notification 忽略并 debug log；
6. 未知 server request 必须明确返回错误；
7. item 字段读取使用 `.get()`，不要大量硬索引；
8. 用真实事件 fixture 锁定已验证版本；
9. 记录 thread id、turn id、method 和 item type，便于排障；
10. 每次升级 Codex CLI 运行 live compatibility test。

本文对应本机：

```text
codex-cli 0.145.0
```

Hermes 源码里声明的最低测试版本为 `0.125.0`，但你应按自己的实际兼容测试结果确定最低版本。

---

## 21. 开源许可注意事项

本机 Hermes Agent 仓库许可证为 MIT。

如果你直接复制 Hermes 源代码，而不是仅参考架构：

- 保留 MIT 许可证文本；
- 保留原始版权声明；
- 在你的 NOTICE/第三方许可文件中注明来源；
- 记录你基于的提交 SHA；
- 检查你另外复制或打包的 Codex CLI 代码/二进制各自许可证。

本文不是法律意见。若产品要商业发布，请让法务检查依赖、分发方式和 OAuth/服务条款。

---

## 22. 推荐阅读顺序

按下面顺序阅读源码最快：

1. `agent/transports/codex_app_server.py`
2. `tests/agent/transports/test_codex_app_server_session.py`
3. `agent/transports/codex_app_server_session.py`
4. `tests/agent/transports/test_codex_event_projector.py`
5. `agent/transports/codex_event_projector.py`
6. `agent/codex_runtime.py` 的 `make_codex_app_server_event_bridge()`
7. `agent/codex_runtime.py` 的 `run_codex_app_server_turn()`
8. `agent/conversation_loop.py` 的 app-server early return
9. `tests/agent/test_codex_app_server_persist.py`
10. `agent/transports/hermes_tools_mcp_server.py`
11. `hermes_cli/codex_runtime_plugin_migration.py`

官方协议入口：

- Codex app-server README：<https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md>
- Hermes App-server 文档：`website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/features/codex-app-server-runtime.md`

---

## 23. 最终实现检查表

```text
[ ] codex CLI 存在且版本符合要求
[ ] app-server stdout 只走 JSON-RPC
[ ] stderr 独立读取、限长、脱敏
[ ] initialize + initialized 完成
[ ] 一个应用会话复用一个 Codex thread
[ ] turn/start 返回 turn id
[ ] notification 按 thread/turn 过滤
[ ] delta 只进 UI，不逐片落库
[ ] item/completed 进入 projector
[ ] tool call id 稳定
[ ] exec/file patch 审批默认 fail closed
[ ] 未知 server request 返回 -32601
[ ] Ctrl+C 使用 turn/interrupt
[ ] 总超时和 post-tool watchdog 存在
[ ] wedged client 会 retire，不继续复用
[ ] tokenUsage/updated 被统计
[ ] Codex thread 使用原生 compaction
[ ] user/assistant/tool 消息恰好写一次
[ ] 数据库保存 thread/turn/item 关联
[ ] MCP server stdout 不打印日志
[ ] managed config block 原子更新
[ ] 子进程只继承必要凭据
[ ] HOME 不被错误重写
[ ] 真实 Codex live test 可选执行
```

完成以上项目后，你实现的就不只是一个“能发消息给 Codex 的脚本”，而是一个与 Hermes App-server 模式同类、可恢复、可审计、可扩展且具备生产可靠性的宿主运行时。