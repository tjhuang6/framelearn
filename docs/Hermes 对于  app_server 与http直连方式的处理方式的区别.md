
可以把 Hermes 的这几种方式分成两层来理解：

1. “Hermes 从哪里接收用户输入”
2. “Hermes 如何连接模型服务”

你说的 CLI、Desktop App、App Store 版本，主要属于第一层；app_server、HTTP 直连、Chat Completions、Responses API，属于第二层。它们不是同一组概念。

一、Hermes 的总体结构

大致是：

text
CLI / TUI / Desktop App / Telegram / Web Dashboard
                    │
                    ▼
             Hermes Agent Core
                    │
       ┌────────────┼────────────┐
       │            │            │
   HTTP API      Codex API    app-server
 Chat Completions Responses   子进程协议
       │            │            │
       ▼            ▼            ▼
   模型服务       模型服务     本地 codex CLI


所以：

- CLI 不等于 HTTP 模式
- Desktop App 不等于 App Store 模式
- App Store 版本不等于某一种模型 API
- app_server 不是普通 HTTP API 的别名
- Hermes 的“界面”可以和“模型连接方式”独立组合

二、正常 API / HTTP 直连模式

这是最传统的模式：

text
Hermes → HTTP 请求 → 模型服务


通常是 Hermes 自己负责：

1. 保存对话历史
2. 构造 system prompt
3. 注入工具定义
4. 发送 messages、tools 等请求
5. 解析模型返回的 tool call
6. Hermes 自己执行工具
7. 把工具结果再次发给模型
8. 循环直到模型输出最终回答

典型 API 模式包括：

yaml
model:
  api_mode: chat_completions


或者：

yaml
model:
  api_mode: codex_responses


这里的 codex_responses 仍然是 HTTP API，只是请求格式从传统的：

text
POST /v1/chat/completions


变成了类似：

text
POST /v1/responses


也就是说，codex_responses 不是 app_server。

HTTP 直连模式的核心特点是：

- Hermes 掌握完整的 agent loop
- Hermes 决定什么时候调用工具
- Hermes 维护真实的消息列表
- Hermes 负责上下文压缩
- Hermes 负责权限确认
- Hermes 负责把工具调用映射成内部工具
- Hermes 直接使用 API key 访问 base_url
- 每一轮通常都会重新构造并发送上下文

例如当前配置中的：

yaml
model:
  default: deepseek-v4-flash
  provider: deepseek
  base_url: https://api.deepseek.com
  api_mode: codex_responses


本质上就是：

text
Hermes → https://api.deepseek.com → DeepSeek


如果改成 OpenAI-compatible 服务，一般也是同样的模型调用思路：

yaml
model:
  provider: custom
  base_url: https://example.com/v1
  api_mode: chat_completions


三、app_server 模式是什么

app_server 是另一种架构：

text
Hermes → 本地 codex app-server 子进程 → 模型服务


Hermes 不再直接负责完整的模型工具循环，而是启动本地的：

bash
codex app-server


然后通过 Codex 的 JSON-RPC/stdio 协议通信。

实际执行过程大概是：

text
1. Hermes 启动 codex app-server
2. Hermes 与 app-server 建立 JSON-RPC 通道
3. 创建或恢复 Codex thread
4. Hermes 把用户输入交给 Codex
5. Codex 自己决定是否调用 shell、文件修改、MCP、搜索等工具
6. Codex app-server 持续发送事件
7. Hermes 接收并投影这些事件
8. Hermes 把最终回答显示给用户


配置通常类似：

yaml
model:
  provider: openai-codex
  api_mode: codex_app_server
  openai_runtime: codex_app_server


你当前环境中已经验证过的配置是：

yaml
model:
  aliases:
    codex: openai-codex/gpt-5.6-sol
  openai_runtime: codex_app_server


启动时使用：

bash
hermes


然后：

text
/model codex


或者：

bash
hermes chat --provider openai-codex -m gpt-5.6-sol


但需要注意：你当前环境里，Codex 这一条链路实际由本地 codex app-server 子进程负责连接和认证，而不是 Hermes 自己拿 API key 对 chatgpt.com 发 HTTP 请求。

四、app_server 和 HTTP 直连的关键区别

项目: 模型请求发起者
HTTP 直连: Hermes
app_server: 本地 Codex app-server
────────────────────────────────────────
项目: Hermes 是否直接请求模型 API
HTTP 直连: 是
app_server: 通常不是
────────────────────────────────────────
项目: Hermes 是否自己运行 tool loop
HTTP 直连: 是
app_server: 主要由 Codex 运行
────────────────────────────────────────
项目: 工具执行控制者
HTTP 直连: Hermes
app_server: Codex app-server
────────────────────────────────────────
项目: 对话 thread 所有者
HTTP 直连: Hermes
app_server: Codex
────────────────────────────────────────
项目: 上下文压缩
HTTP 直连: Hermes 负责
app_server: Codex thread 负责
────────────────────────────────────────
项目: Hermes 的消息历史
HTTP 直连: 真实主历史
app_server: 对 Codex 事件的投影/镜像
────────────────────────────────────────
项目: 权限系统
HTTP 直连: Hermes approvals
app_server: Codex 权限 + Hermes 桥接
────────────────────────────────────────
项目: 认证来源
HTTP 直连: Hermes provider/API key/OAuth
app_server: Codex CLI 自己的配置和认证
────────────────────────────────────────
项目: 适合的模型
HTTP 直连: OpenAI-compatible、Anthropic、DeepSeek 等
app_server: Codex 支持的模型/订阅链路
────────────────────────────────────────
项目: 断开后恢复
HTTP 直连: Hermes 重新发 HTTP 上下文
app_server: 通过 Codex thread 恢复
────────────────────────────────────────
项目: API 兼容性
HTTP 直连: 取决于 HTTP endpoint
app_server: 取决于本地 Codex CLI

最重要的一点：

HTTP 直连模式下，Hermes 是“驾驶员”。

app_server 模式下，Hermes 更像“驾驶舱和代理层”，而 Codex app-server 是实际掌握模型循环的“发动机”。

五、app_server 并不是普通的 HTTP 代理

很多人容易误以为：

text
app_server = Hermes 使用另一个 HTTP endpoint


实际上更接近：

text
app_server = Hermes 调用本地 Codex 运行时


它中间存在一个本地进程：

text
hermes
  └── codex app-server
        └── Codex 自己处理 OAuth / API / thread / tools


Hermes 的代码中也明确把这一条路径单独处理：

- api_mode == "codex_app_server" 时，直接进入 _run_codex_app_server_turn
- Hermes 创建一个 CodexAppServerSession
- session 会启动并复用一个 Codex app-server 子进程
- Codex 的事件通过 JSON-RPC 返回
- Hermes 将 item/started、item/completed、消息增量、推理增量等事件转成 Hermes UI 的回调
- 最终再把 Codex 产生的工具调用和回答投影到 Hermes 的会话记录中

所以它和普通 chat_completions 路径是两套不同的执行循环。

六、Hermes 如何处理工具

HTTP 直连时：

text
模型返回 tool_call
        │
        ▼
Hermes 解析 tool_call
        │
        ▼
Hermes 执行 terminal/file/browser 等工具
        │
        ▼
Hermes 把 tool result 发回模型


例如模型返回：

json
{
  "tool_calls": [
    {
      "name": "exec_command",
      "arguments": {
        "command": "ls"
      }
    }
  ]
}


Hermes 自己执行 exec_command。

而 app_server 时：

text
Codex app-server 认为需要执行命令
        │
        ▼
Codex 发出 commandExecution 事件
        │
        ▼
Hermes 通过权限桥接决定是否允许
        │
        ▼
Codex 继续执行并产生结果


Hermes 会把 Codex 的事件映射成类似：

text
commandExecution → exec_command
fileChange       → apply_patch
mcpToolCall      → Hermes/MCP 工具
webSearch        → web_search


但这些操作的内部控制权主要在 Codex runtime，而不是 Hermes 的标准 tool loop。

七、权限处理上的差别

HTTP 直连模式：

- Hermes 自己知道要调用哪个工具
- Hermes 可以在工具执行前触发 approval
- approvals.mode 直接控制 Hermes 工具权限
- Hermes 的 terminal backend、工作目录、超时等配置直接生效

app_server 模式：

- Codex 可能自己发起 exec 或 apply_patch 请求
- Hermes 通过 app-server 的 server request 接口接收这些请求
- CLI 有交互界面时，可以桥接到 Hermes 的 approval 流程
- Gateway、cron 等没有交互界面的场景默认倾向于 fail-closed，也就是拒绝需要人工确认的操作
- 如果显式使用 /yolo、--yolo 或 approvals.mode: off，则会允许 Codex 根据自己的 sandbox/profile 执行

因此在自动任务中，两种模式的安全边界也不同。

八、上下文和压缩差异

这是两种模式非常实质性的差别。

HTTP 直连：

text
Hermes messages[]
       │
       ▼
Hermes 计算 token 使用量
       │
       ▼
Hermes 触发 compression
       │
       ▼
Hermes 改写或总结上下文


app_server：

text
Codex thread
       │
       ▼
Codex 自己掌握真实上下文
       │
       ▼
Codex 自己进行 thread compaction


Hermes 这里保留的是一份投影历史，用于：

- 会话显示
- session resume
- memory
- skill review
- 数据库持久化
- UI 展示

但这份 Hermes 历史不一定等于 Codex 内部实际使用的全部上下文。

你当前配置中：

yaml
compression:
  codex_app_server_auto: native


表示优先让 Codex 自己处理 app-server thread 的压缩。

可选语义是：

yaml
compression:
  codex_app_server_auto: native


- native：让 Codex 自己决定何时压缩
- hermes：由 Hermes 的阈值触发 Codex 的压缩动作
- off：Hermes 不主动触发，但 Codex 仍可能自行压缩

HTTP 直连路径不会使用这套 Codex thread compaction 机制，而是走 Hermes 自己的压缩逻辑。

九、App 模式、App Store 模式到底是什么

这里需要区分几种叫法。

1. Hermes CLI 模式

bash
hermes


这是交互界面。它本身不决定使用 HTTP 还是 app_server。

2. Hermes Desktop App 模式

bash
hermes desktop


这是桌面客户端。桌面客户端仍然连接 Hermes Agent Core。Agent Core 后面可以使用：

- HTTP API
- OAuth provider
- Codex app-server
- Nous Portal
- 其他 provider

所以 Desktop App 只是界面变化，不是传输协议变化。

3. App Store 版本

如果你说的是某个通过 App Store 安装的客户端，那么通常要看它是：

- 一个 Hermes 的桌面前端
- 一个远程 gateway 客户端
- 一个使用厂商订阅的独立客户端
- 还是一个本地运行 Hermes Core 的封装

不能仅凭“App Store 安装”判断它是 HTTP 直连还是 app_server。

App Store 版本可能使用：

text
App Store App → Hermes Gateway → Hermes Core → HTTP API


也可能是：

text
App Store App → 本地 Hermes Core → Codex app-server


也可能是：

text
App Store App → 厂商自己的云端 API


这要看具体 App 的实现。

十、Nous Portal / OAuth 订阅和 API key 的区别

从 Hermes 的角度，OAuth/订阅和 API key 是“认证方式”；HTTP 和 app_server 是“运行时/传输方式”。

例如：

text
OAuth + HTTP


和：

text
OAuth + app_server


是可能分别存在的两种组合。

可以这样理解：

text
API key：
Hermes 读取 API key
    → Hermes 直接调用 provider HTTP API

OAuth provider：
Hermes 读取 OAuth token
    → Hermes 使用 provider 的 HTTP API

Codex app_server：
Codex CLI 读取自己的 OAuth/API 配置
    → Codex app-server 负责后续模型访问


因此，使用某个订阅并不自动意味着一定走 app_server；使用 API key 也不自动意味着一定是普通 Chat Completions。最终还要看 provider 的 runtime 和 api_mode。

十一、什么时候应该用哪一种

适合 HTTP 直连：

- 使用 DeepSeek、Kimi、MiniMax、GLM、OpenRouter 等
- 自己有 API key
- 使用自建 OpenAI-compatible endpoint
- 希望 Hermes 完全控制工具调用
- 需要明确控制 base URL、代理、超时和请求格式
- 需要调试原始 HTTP 请求

适合 app_server：

- 使用 OpenAI Codex 订阅/OAuth
- 希望复用 Codex CLI 的能力
- 希望使用 Codex 原生 thread、工具循环和权限模型
- 不希望 Hermes 直接访问 ChatGPT API
- 已经在 codex CLI 中配置好了认证和模型
- 需要 Codex 的原生 app-server 协议能力

十二、你当前配置的实际含义

你现在的配置：

yaml
model:
  default: deepseek-v4-flash
  provider: deepseek
  base_url: https://api.deepseek.com
  api_mode: codex_responses
  openai_runtime: codex_app_server
  aliases:
    codex: openai-codex/gpt-5.6-sol


这里需要特别注意：openai_runtime: codex_app_server 是一个运行时配置，但并不意味着所有 provider 都会自动变成 app_server。

实际应该按最终解析出的 route 判断：

默认 DeepSeek 路线大致是：

text
provider = deepseek
model = deepseek-v4-flash
base_url = https://api.deepseek.com
api_mode = codex_responses


也就是 HTTP Responses 风格路径。

切换到：

text
/model codex


后，路线变成：

text
provider = openai-codex
model = gpt-5.6-sol
api_mode = codex_app_server


于是才会启动：

text
codex app-server


也就是说：

- deepseek：走 Hermes 的 HTTP 模型适配路径
- codex：走本地 Codex app-server 路径
- 两条路线共用 Hermes 的 UI、会话、记忆和部分权限桥接
- 但底层的 agent loop、上下文所有权、工具执行方式不同

可以使用下面的命令检查当前实际路由：

bash
hermes config get model
hermes model
hermes status


如果要明确切回普通 HTTP 路径，不要仅仅改 openai_runtime，而应选择对应的 provider/model，并确认 api_mode：

bash
hermes config set model.provider deepseek
hermes config set model.default deepseek-v4-flash
hermes config set model.api_mode codex_responses


如果要使用 Codex app-server：

bash
hermes auth add openai-codex
hermes config set model.provider openai-codex
hermes config set model.default gpt-5.6-sol
hermes config set model.api_mode codex_app_server
hermes config set model.openai_runtime codex_app_server


不过你的现有 alias 已经配置好了，最简单的是：

text
/model codex


一句话总结：

text
HTTP 直连：
Hermes 自己调用模型 API，Hermes 自己运行工具循环。

app_server：
Hermes 调用本地 codex app-server，Codex 负责模型调用、thread、工具循环和原生压缩，Hermes 负责界面、权限桥接、会话投影和外围能力。

CLI/Desktop/App Store：
只是 Hermes 的运行界面或分发方式，本身不能决定底层到底是 HTTP 还是 app_server。


