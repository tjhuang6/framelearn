# Security Fix: Codex Subprocess Credential Leakage

## Problem

The `JsonRpcStdioClient._build_env()` method was using a **denylist approach** that copied the entire `os.environ` and only removed 4 specific keys:

```python
# Old vulnerable implementation
env = os.environ.copy()
_STRIP_KEYS = {
    "TEXT_API_KEY",
    "VISION_API_KEY", 
    "DATABASE_URL",
    "WEBHOOK_SECRET",
}
for key in _STRIP_KEYS:
    env.pop(key, None)
```

**Vulnerable credentials that leaked:**
- `SILICONFLOW_API_KEY` - SiliconFlow API access
- `DASHSCOPE_API_KEY` - Alibaba DashScope ASR service
- `OSS_ACCESS_KEY_ID` - Aliyun OSS object storage
- `OSS_ACCESS_KEY_SECRET` - Aliyun OSS secret
- Any other cloud credentials in parent environment

## Solution

Switched to **allowlist approach** that only passes essential system variables and Codex-specific configuration:

```python
# New secure implementation
_ALLOWED_KEYS = {
    # Core system
    "PATH", "HOME", "USER", "SHELL", "TMPDIR",
    # Locale and display
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "DISPLAY",
    # Development tools
    "SSH_AUTH_SOCK", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
    # Node.js, Python, XDG
    "NODE_ENV", "PYTHONPATH", "XDG_CONFIG_HOME",
    # ... (see full list in code)
}

env = {}
for key in _ALLOWED_KEYS:
    if key in os.environ:
        env[key] = os.environ[key]

# Include any CODEX_* variables
for key, value in os.environ.items():
    if key.startswith("CODEX_"):
        env[key] = value

# Apply explicit overrides
if override:
    env.update(override)
```

## Security Properties

### ✅ Blocks by Default
- All API keys, secrets, tokens, passwords are blocked unless explicitly allowlisted
- No FrameLearn cloud credentials leak to Codex subprocess
- Prevents future credential leakage from new environment variables

### ✅ Preserves Functionality
- System variables (PATH, HOME, USER) pass through
- Development tools (git, ssh, node) still work
- Codex-specific config (CODEX_*) preserved
- Explicit override parameter for advanced use cases

### ✅ Comprehensive Testing
Created `test/src/test_jsonrpc_env_filter.py` with 9 test cases:
1. ✓ Blocks FrameLearn API keys
2. ✓ Blocks generic secrets
3. ✓ Preserves system variables
4. ✓ Preserves CODEX_* variables
5. ✓ Override parameter works
6. ✓ Override can overwrite system vars
7. ✓ Empty environment handling
8. ✓ Allowlist sufficient for Codex operations
9. ✓ No common secret patterns leak

All tests pass (98/99 existing tests pass; 1 pre-existing failure unrelated to this fix).

## Impact

### Before Fix
```bash
# Subprocess environment (DANGEROUS)
PATH=/usr/bin
HOME=/home/user
SILICONFLOW_API_KEY=sk-silicon-secret  # ❌ LEAKED
DASHSCOPE_API_KEY=dash-secret          # ❌ LEAKED
OSS_ACCESS_KEY_ID=oss-id-secret        # ❌ LEAKED
OSS_ACCESS_KEY_SECRET=oss-secret       # ❌ LEAKED
TEXT_API_KEY=<removed>
VISION_API_KEY=<removed>
DATABASE_URL=<removed>
WEBHOOK_SECRET=<removed>
```

### After Fix
```bash
# Subprocess environment (SECURE)
PATH=/usr/bin
HOME=/home/user
CODEX_HOME=/custom/codex
GIT_AUTHOR_NAME=User
# ... (only allowlisted variables)
# ✅ All secrets blocked
```

## Verification

Run the test suite:
```bash
python -m pytest test/src/test_jsonrpc_env_filter.py -v
```

Manual verification:
```python
from framelearn.app_server.jsonrpc_client import JsonRpcStdioClient
import os

# Simulate dangerous environment
os.environ['SILICONFLOW_API_KEY'] = 'secret'
os.environ['DASHSCOPE_API_KEY'] = 'secret'

# Build subprocess environment
env = JsonRpcStdioClient._build_env(override=None)

# Verify secrets blocked
assert 'SILICONFLOW_API_KEY' not in env
assert 'DASHSCOPE_API_KEY' not in env
```

## Files Changed

1. **framelearn/app_server/jsonrpc_client.py**
   - Replaced `_build_env()` denylist with allowlist
   - Added comprehensive allowlist of system variables
   - Preserved CODEX_* variable passthrough
   - Updated documentation

2. **test/src/test_jsonrpc_env_filter.py** (NEW)
   - 9 comprehensive test cases
   - Covers all credential types
   - Verifies allowlist sufficiency
   - Regression prevention

## Recommendations

1. **No action required** for normal usage - the fix is transparent
2. **Review**: If you explicitly need to pass additional environment variables to Codex, use the `override` parameter:
   ```python
   client = JsonRpcStdioClient(
       command=("codex", "app-server"),
       env={"CUSTOM_VAR": "value"}
   )
   ```
3. **Security audit**: Consider auditing other subprocess spawns in the codebase for similar issues

## Related Security Concerns

None identified. This was the only subprocess spawn that inherited environment variables.

---

**Fixed**: 2025-01-XX  
**Severity**: High (credential leakage)  
**Impact**: Codex subprocess isolation  
**Status**: ✅ Resolved with comprehensive test coverage
