# CLI 失败可能仍返回成功退出码修复 - 技术报告

## 问题描述（antivibe-technical-report.md 第 202 行）

**问题**：`VideoPipeline` 的业务失败通常通过 `PipelineResult.error` 返回，Router 只打印错误；未实现的在线下载同样是打印提示后正常返回。因此 `_run_once()` 没有看到异常，最终返回 0。审计实测 `framelearn run https://youtube.com/watch?v=x` 明确打印"在线下载尚未实现"，shell 退出码仍为 0。

**风险**：会误导 shell 脚本、批处理和 CI：日志显示失败，但自动化系统将任务标为成功。

**建议**：让 Router handler 返回明确状态，或在不可完成的 `run` 路径抛出领域异常；`_run_once()` 应把失败统一映射为非零退出码，并增加 CLI exit-code 测试。

## pi-agent 执行结果

✅ **已完成** - pi-agent 采用"领域异常 + 显式返回码"双重方案彻底修复

### 修复方案

#### 1. 新增领域异常层级（`framelearn/errors.py`，新文件）

```python
class FrameLearnError(Exception):
    """基类"""

class PipelineExecutionError(FrameLearnError):
    """VideoPipeline.run() 的业务失败"""

class FeatureNotAvailableError(FrameLearnError):
    """未实现的功能路径（如在线下载）"""
```

#### 2. Router 层修复（`framelearn/router.py`）

- `CommandRouter.execute()` 及所有 handler 现在**返回明确的 `int` 状态码**（成功为 `0`），而不是 `None`。这样"没有抛异常"这条路径本身也变得明确无歧义。
- **在线视频下载路径**：不再是"打印提示后正常返回"，改为直接抛出 `FeatureNotAvailableError`。
- **`_run_pipeline`**：当 `PipelineResult.error` 被设置时，改为抛出 `PipelineExecutionError(result.error)`，而不是仅打印错误信息。

#### 3. CLI 入口修复（`framelearn/__main__.py`）

- `_run_once()` 成功时直接返回 `router.execute(...)` 的状态码
- 显式捕获 `FrameLearnError`（在原有 `ValueError` 之外），映射为退出码 `1`

### 修复前后对比

**修复前**：
```bash
$ framelearn run https://youtube.com/watch?v=x
在线下载尚未实现
$ echo $?
0    # ❌ 误导 CI/脚本，认为任务成功
```

**修复后**：
```bash
$ framelearn run https://youtube.com/watch?v=x
Error: FeatureNotAvailableError: 在线下载尚未实现
$ echo $?
1    # ✅ 正确反映失败
```

## 测试验证

### 更新的测试
`test_router.py` 更新为基于"抛异常"的新契约：
- 在线视频下载 → 断言抛出 `FeatureNotAvailableError`
- pipeline 失败 → 断言抛出 `PipelineExecutionError`

### 新增测试
`test/src/test_cli_exit_codes.py`（新文件），覆盖：
- `_run_once()` 单元级退出码验证
- 完整 `python -m framelearn` 子进程退出码验证

覆盖的回归场景：
- 在线视频下载（未实现路径）
- 缺少输入源
- 非法 URL
- 文件不存在
- pipeline 业务失败
- 各成功路径（确保正常场景仍返回 0）

### 测试结果

**110 个相关测试全部通过**。

唯一失败项：`test_agent_keyframe.py` 中的既有无关测试（关键帧命名断言，因并行任务改了命名格式导致，与本次修复无关）。pi-agent 明确验证方式：**将本次改动 stash 后复现同一失败**，确认该失败独立于本次修复。

## 相关文件

### 新增的文件
- `framelearn/errors.py` - 领域异常层级
- `test/src/test_cli_exit_codes.py` - CLI 退出码测试

### 修改的文件
- `framelearn/router.py` - handler 返回显式状态码，未实现路径抛异常
- `framelearn/__main__.py` - `_run_once()` 捕获 `FrameLearnError` 映射退出码
- `test/src/test_router.py` - 更新为基于异常的新契约

## 总结

pi-agent 用"领域异常 + 显式状态码"双保险方式彻底解决了 CLI 静默失败问题：

1. ✅ 新建异常层级区分"业务失败"和"功能未实现"两类场景
2. ✅ Router 层不再依赖隐式的"打印后返回"，改为显式抛异常或返回状态码
3. ✅ `_run_once()` 显式捕获领域异常并映射为非零退出码
4. ✅ 完整测试覆盖：单元级 + 子进程级 exit code 测试
5. ✅ 严谨验证：通过 stash 对比确认既有失败与本次改动无关

现在 `framelearn run <未实现的URL>` 等失败场景会正确返回非零退出码，不会再误导 shell 脚本、批处理或 CI 系统。

---

**状态**: ✅ 已完成并测试通过（110/110 相关测试）  
**修复人**: pi-agent (OpenAI Codex)  
**验证严谨性**: 通过 git stash 对比排除既有问题干扰
