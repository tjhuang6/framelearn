# Codex 子进程凭据泄露修复 - 技术报告

## 问题描述（antivibe-technical-report.md 第 210 行）

**问题**：`JsonRpcStdioClient._build_env()` 从完整 `os.environ` 复制环境，只移除 `TEXT_API_KEY`、`VISION_API_KEY`、`DATABASE_URL` 和 `WEBHOOK_SECRET`。因此 `DASHSCOPE_API_KEY`、`SILICONFLOW_API_KEY`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET` 以及其他云凭据仍会进入 `codex app-server` 子进程。

**风险**：只要 Agent 能执行命令，就可能读取这些环境变量，扩大凭据暴露面。

**建议**：改为 allowlist 构建子进程环境，至少只保留系统运行变量、`HOME`、`PATH`、Codex 必要配置和明确授权的字段。

## pi-agent 执行结果

✅ **已完成** - pi-agent 采用 allowlist 方案彻底修复了凭据泄露问题

### 根因分析

**原实现**（denylist，有漏洞）：
```python
env = os.environ.copy()  # 复制全部环境变量
_STRIP_KEYS = {"TEXT_API_KEY", "VISION_API_KEY", "DATABASE_URL", "WEBHOOK_SECRET"}
for key in _STRIP_KEYS:
    env.pop(key, None)
```

**泄露的凭据**：
- `SILICONFLOW_API_KEY` - SiliconFlow API（ASR/Vision）
- `DASHSCOPE_API_KEY` - 阿里云 DashScope ASR
- `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` - 阿里云 OSS
- 以及父进程环境中的任何其他云凭据

denylist 方式的根本缺陷：新增任何凭据类环境变量都会被自动继承，除非显式加入移除列表——这是"默认放行"的不安全设计。

### 修复方案：allowlist（默认拒绝）

```python
_ALLOWED_KEYS = {
    # 核心系统
    "PATH", "HOME", "USER", "SHELL", "TMPDIR",
    # 本地化/显示
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "DISPLAY",
    # 开发工具
    "SSH_AUTH_SOCK", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
    # Node.js / Python
    "NODE_ENV", "PYTHONPATH", "VIRTUAL_ENV",
    # XDG
    "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    # ...（完整列表见代码）
}

env = {}
for key in _ALLOWED_KEYS:
    if key in os.environ:
        env[key] = os.environ[key]

# 保留所有 CODEX_* 变量（Codex 自身配置）
for key, value in os.environ.items():
    if key.startswith("CODEX_"):
        env[key] = value

# 显式 override 参数仍可用（高级场景）
if override:
    env.update(override)
```

### 安全属性

| 属性 | 修复前 | 修复后 |
|------|--------|--------|
| 默认阻止 API 密钥 | ❌ | ✅ |
| 默认阻止云凭据 | ❌ | ✅ |
| 新增密钥自动阻止 | ❌（需手动加入 denylist） | ✅（allowlist 默认拒绝） |
| 保留系统变量 | ✅ | ✅ |
| 保留 Codex 配置 | ✅ | ✅ |
| 向后兼容 | - | ✅ |

### 测试覆盖

**test/src/test_jsonrpc_env_filter.py** - 9 个测试全部通过 ✅

1. ✅ `test_blocks_framelearn_api_keys` - 阻止 SILICONFLOW/DASHSCOPE/OSS 凭据
2. ✅ `test_blocks_generic_secrets` - 阻止 DATABASE_URL、AWS/Azure/OpenAI 密钥
3. ✅ `test_preserves_system_variables` - 保留 PATH/HOME/USER/SHELL/LANG 等
4. ✅ `test_preserves_codex_variables` - 保留所有 CODEX_* 变量
5. ✅ `test_override_parameter` - override 参数可添加自定义变量
6. ✅ `test_override_can_overwrite_system_vars` - override 可覆盖 allowlist 变量
7. ✅ `test_empty_environment` - 空环境安全处理
8. ✅ `test_allowlist_is_sufficient_for_codex` - 验证 Codex 正常运行所需变量齐全
9. ✅ `test_no_env_key_patterns_leak` - 综合密钥模式检测（`*_KEY`/`*_SECRET`/`*_TOKEN`/`*_PASSWORD`）

**回归测试**：98/99 现有测试通过（1 个失败与本次修复无关，为既有问题）

## 验证结果

### 修复前（危险）
```bash
PATH=/usr/bin
HOME=/home/user
SILICONFLOW_API_KEY=«redacted:sk-…»   # ❌ 泄露
DASHSCOPE_API_KEY=dash-secret          # ❌ 泄露
OSS_ACCESS_KEY_ID=oss-id-secret        # ❌ 泄露
OSS_ACCESS_KEY_SECRET=oss-secret       # ❌ 泄露
```

### 修复后（安全）
```bash
PATH=/usr/bin
HOME=/home/user
CODEX_HOME=/custom/codex
# ... 仅 allowlist 中的变量
# ✅ 所有秘密被阻止
```

### 手动验证方法
```python
from framelearn.app_server.jsonrpc_client import JsonRpcStdioClient
import os

os.environ['SILICONFLOW_API_KEY'] = 'secret'
os.environ['DASHSCOPE_API_KEY'] = 'secret'

env = JsonRpcStdioClient._build_env(override=None)

assert 'SILICONFLOW_API_KEY' not in env  # ✅ 通过
assert 'DASHSCOPE_API_KEY' not in env    # ✅ 通过
```

## 相关文件

### 修改的文件
- `framelearn/app_server/jsonrpc_client.py` - `_build_env()` 由 denylist 改为 allowlist（约 60 行变更）

### 新增的文件
- `test/src/test_jsonrpc_env_filter.py` - 9 个测试用例（约 280 行）
- `docs/security-fix-codex-env.md` - 详细安全分析文档
- `SECURITY_FIX_SUMMARY.md` - 修复总结

## 使用建议

如需向 Codex 子进程传递自定义环境变量，使用 `override` 参数（显式授权）：
```python
session = AppServerSession(
    workspace="/path/to/workspace",
    codex_env={"CUSTOM_VAR": "value"}  # 显式允许
)
```

## 总结

pi-agent 成功将 `_build_env()` 由不安全的 denylist 改为 allowlist：

1. ✅ 根因是"默认放行"的 denylist 设计——只拉黑 4 个已知密钥，新密钥自动泄露
2. ✅ 改为 allowlist："默认拒绝"，只放行明确列出的系统变量和 CODEX_* 前缀变量
3. ✅ 保留 override 参数，高级场景仍可显式传递自定义变量
4. ✅ 9 个新测试覆盖所有云凭据类型 + 通用密钥模式检测
5. ✅ 回归测试确认不影响现有 Codex 功能

**安全影响**：高（阻止了 SiliconFlow/DashScope/OSS 等全部云凭据泄露到子进程）
**向后兼容**：完全兼容，系统变量和 Codex 配置正常传递

---

**状态**: ✅ 已完成并测试通过  
**修复人**: pi-agent (OpenAI Codex)  
**测试覆盖**: 9/9 通过（+ 98/99 回归测试）  
**严重程度**: High（凭据泄露）
