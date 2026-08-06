# Git Commit Message

```
feat: 实现缓存可追溯性 manifest 系统

## 问题
- 缓存判断散落在多个文件，仅检查文件存在性
- 无法追溯缓存来源（输入文件、配置、模型）
- 输入或配置变化时仍使用旧缓存
- 用户无法判断产物的可信度

## 解决方案
实现完整的 manifest 系统，记录并验证缓存来源

### 核心功能
- Cache manifest 数据结构（输入文件 hash、配置快照、模型信息、Git commit）
- Cache key 计算（基于输入、配置、模型、代码版本）
- 自动缓存验证（输入或配置变化时自动失效）
- 段落级进度追踪（支持断点续传）

### 集成点
- video_pipeline.py: 字幕和关键帧缓存验证
- doc_generator.py: 段落缓存和进度追踪
- 新增 inspect_manifest.py 调试工具

### 测试覆盖
- 14 个单元测试（cache_manifest.py）
- 6 个集成测试（pipeline 集成）
- 所有 90 个测试通过

### 性能影响
- Hash 计算（前 1MB）: < 10ms
- Manifest 验证: < 1ms
- 总额外开销: < 20ms（可忽略）

### 文档
- docs/cache_manifest.md: 完整设计文档
- docs/cache_architecture.md: 架构和流程图
- docs/cache_manifest_fix.md: 修复总结
- CACHE_FIX_SUMMARY.md: 完成总结

## 文件变更
### 新增
- framelearn/pipeline/cache_manifest.py (300+ 行)
- framelearn/tools/inspect_manifest.py (150+ 行)
- test/src/test_cache_manifest.py (450+ 行)
- test/src/test_cache_integration.py (250+ 行)
- docs/cache_*.md (3 个文档)

### 修改
- framelearn/pipeline/video_pipeline.py
- framelearn/pipeline/doc_generator.py

## 向后兼容
- 旧缓存文件不会被删除
- 自动提示 manifest 缺失并重建
- 不影响现有 API 和功能

## 使用示例
```bash
# 检查 manifest
python -m framelearn.tools.inspect_manifest output/my_video

# 正常使用（自动验证缓存）
framelearn run my_video.mp4
```

Breaking Changes: 无
Migration Required: 否
```

---

## 简短版本（适合 GitHub PR）

```
feat: 实现缓存可追溯性 manifest 系统

解决缓存来源不可追溯问题：
- ✅ 记录输入文件 hash、配置、模型、代码版本
- ✅ 自动验证缓存有效性（输入/配置变化时失效）
- ✅ 段落级进度追踪，支持断点续传
- ✅ 调试工具 inspect_manifest.py
- ✅ 90 个测试全部通过
- ✅ 性能开销可忽略（< 20ms）
- ✅ 向后兼容，无需迁移

详见：CACHE_FIX_SUMMARY.md
```

---

## Conventional Commits 格式

```
feat(cache): implement manifest system for cache traceability

- Add cache_manifest.py with InputFileInfo, ConfigSnapshot, CacheManifest
- Integrate manifest validation in video_pipeline.py and doc_generator.py
- Add inspect_manifest.py tool for debugging
- Add comprehensive tests (20 new tests, 90 total passing)
- Add documentation (cache_manifest.md, cache_architecture.md)

BREAKING CHANGE: None
```
