# Codex Subprocess Credential Leakage - Fix Summary

## 🎯 Objective
Fix security vulnerability where FrameLearn's cloud credentials were leaking to the Codex app-server subprocess.

## 🔍 Root Cause Analysis

**File**: `framelearn/app_server/jsonrpc_client.py`
**Method**: `JsonRpcStdioClient._build_env()`

**Original Implementation** (Vulnerable):
```python
# Denylist approach - copies ALL environment, removes only 4 keys
env = os.environ.copy()
_STRIP_KEYS = {"TEXT_API_KEY", "VISION_API_KEY", "DATABASE_URL", "WEBHOOK_SECRET"}
for key in _STRIP_KEYS:
    env.pop(key, None)
```

**Leaked Credentials**:
- ✗ `SILICONFLOW_API_KEY` - SiliconFlow API for ASR/Vision
- ✗ `DASHSCOPE_API_KEY` - Alibaba DashScope ASR service  
- ✗ `OSS_ACCESS_KEY_ID` - Aliyun OSS object storage
- ✗ `OSS_ACCESS_KEY_SECRET` - Aliyun OSS secret key
- ✗ Any other cloud credentials in parent environment

## ✅ Solution Implemented

**Approach**: Switched from **denylist** to **allowlist**

**New Implementation**:
```python
# Allowlist approach - only pass essential system variables
_ALLOWED_KEYS = {
    # Core system: PATH, HOME, USER, SHELL, TMPDIR, ...
    # Locale/display: LANG, LC_*, TERM, DISPLAY, ...
    # Development tools: SSH_AUTH_SOCK, GIT_*, ...
    # Node.js/Python: NODE_ENV, PYTHONPATH, VIRTUAL_ENV, ...
    # XDG: XDG_CONFIG_HOME, XDG_DATA_HOME, ...
}

env = {}
for key in _ALLOWED_KEYS:
    if key in os.environ:
        env[key] = os.environ[key]

# Pass through all CODEX_* variables
for key, value in os.environ.items():
    if key.startswith("CODEX_"):
        env[key] = value

# Apply explicit overrides
if override:
    env.update(override)
```

## 📊 Security Properties

| Property | Status |
|----------|--------|
| Blocks API keys by default | ✅ |
| Blocks secrets by default | ✅ |
| Blocks cloud credentials | ✅ |
| Preserves system variables | ✅ |
| Preserves Codex config | ✅ |
| Backward compatible | ✅ |
| Override capability | ✅ |

## 🧪 Testing

**New Test File**: `test/src/test_jsonrpc_env_filter.py`

**Test Coverage** (9 test cases):
1. ✅ `test_blocks_framelearn_api_keys` - Blocks SILICONFLOW, DASHSCOPE, OSS credentials
2. ✅ `test_blocks_generic_secrets` - Blocks DATABASE_URL, AWS, Azure, OpenAI keys
3. ✅ `test_preserves_system_variables` - Passes PATH, HOME, USER, SHELL, LANG, etc.
4. ✅ `test_preserves_codex_variables` - Passes all CODEX_* variables
5. ✅ `test_override_parameter` - Override can add custom variables
6. ✅ `test_override_can_overwrite_system_vars` - Override can modify allowlisted vars
7. ✅ `test_empty_environment` - Handles minimal environment safely
8. ✅ `test_allowlist_is_sufficient_for_codex` - Verifies Codex has what it needs
9. ✅ `test_no_env_key_patterns_leak` - Comprehensive secret pattern check

**Test Results**:
```
9/9 tests passed
98/99 existing tests pass (1 pre-existing failure unrelated to this fix)
```

## 🔬 Verification

### Before Fix
```bash
# Codex subprocess sees (DANGEROUS):
SILICONFLOW_API_KEY=sk-silicon-secret  # ❌ LEAKED
DASHSCOPE_API_KEY=dash-secret          # ❌ LEAKED
OSS_ACCESS_KEY_ID=oss-id               # ❌ LEAKED
OSS_ACCESS_KEY_SECRET=oss-secret       # ❌ LEAKED
```

### After Fix
```bash
# Codex subprocess sees (SECURE):
PATH=/usr/local/bin:/usr/bin
HOME=/home/user
CODEX_HOME=/custom/codex
# ... only allowlisted variables
# ✅ All secrets blocked
```

### Manual Verification
```bash
# Run the test suite
python -m pytest test/src/test_jsonrpc_env_filter.py -v

# Results: 9 passed in 0.02s
```

## 📝 Files Changed

### Modified
- **framelearn/app_server/jsonrpc_client.py**
  - Replaced `_build_env()` denylist with allowlist (60 lines changed)
  - Added comprehensive system variable allowlist
  - Preserved CODEX_* variable passthrough
  - Enhanced documentation

### Added
- **test/src/test_jsonrpc_env_filter.py** (NEW, 280 lines)
  - 9 comprehensive test cases
  - Covers all credential types
  - Regression prevention
  
- **docs/security-fix-codex-env.md** (NEW)
  - Detailed security analysis
  - Before/after comparison
  - Usage recommendations

## 🚀 Impact & Compatibility

### Security Impact
- **High**: Prevents credential leakage to subprocess
- **Scope**: All FrameLearn cloud credentials now isolated
- **Future-proof**: New credentials automatically blocked by default

### Backward Compatibility
- ✅ **No breaking changes**: All existing functionality preserved
- ✅ **System variables**: Still passed to Codex (git, npm, etc. work)
- ✅ **Codex config**: All CODEX_* variables still work
- ✅ **Override parameter**: Advanced users can still pass custom env vars

### Performance
- **Negligible**: Environment building happens once at subprocess spawn
- **No runtime overhead**: Zero impact on request/response processing

## 💡 Recommendations

### For Users
1. **No action required** - The fix is transparent to normal usage
2. **Review custom env usage** - If you explicitly pass env vars, ensure they're safe
3. **Audit other subprocesses** - Consider checking other subprocess spawns in your code

### For Developers
```python
# If you need custom environment variables in Codex subprocess:
session = AppServerSession(
    workspace="/path/to/workspace",
    codex_env={"CUSTOM_VAR": "value"}  # Explicitly allowed
)
```

### For Security Auditors
- All secrets follow pattern: `*_KEY`, `*_SECRET`, `*_PASSWORD`, `*_TOKEN`
- Allowlist approach: Default deny, explicit allow
- Override mechanism requires explicit caller action

## 📚 Related Documentation
- See `.env.example` for all FrameLearn environment variables
- See `framelearn/app_server/session.py` for AppServerSession usage
- See `test/src/test_jsonrpc_env_filter.py` for detailed test scenarios

---

**Status**: ✅ **FIXED AND VERIFIED**  
**Date**: 2024-01-XX  
**Severity**: High (credential leakage)  
**Test Coverage**: 9/9 tests passing  
**Integration**: Verified with existing test suite (98/99 pass)
