"""Document generator using Vision API."""

from pathlib import Path
from typing import Literal, Optional

from framelearn.config import get as config_get
from framelearn.pipeline.cache_manifest import (
    create_manifest,
    CacheManifest,
    mark_segment_completed,
    get_completed_segments,
)
from framelearn.pipeline.run_report import get_reporter


# ── 笔记版 prompt ─────────────────────────────────────────────────
_NOTES_PROMPT = """你是一个技术博客作者，负责把编程课视频整理成博客教程风格的学习材料。

字幕是老师讲课的原话，你的任务是：

1. 去掉口水词（"那么"、"就是说"、"大家注意"、"咱们"、"啊"、"嗯"等），但**保留老师的讲解逻辑和节奏**——老师先铺垫什么、后解释什么、用什么例子切入，这个顺序和思路要完整保留
2. 用博客教程的语气写出来——像一个有经验的程序员在给朋友讲东西，有前因后果，读起来自然流畅，不生硬
3. 遇到概念随手解释，加括号备注，不要单独列"名词解释"
4. **不要用 bullet points 堆砌知识点**，改用连贯的段落叙述。知识点要融入行文，而不是列条目
5. 代码片段完整保留，在代码前后用一两句话交代"为什么写这段"和"它做了什么"
6. 在合适位置引用关键帧（格式：![说明文字](src/frame_文件名.jpg)），只在帧内容真正有参考价值时引用
7. 整体读起来像一篇有观点、有温度的技术博文，而不是课堂记录或知识大纲

# 字幕原文

<subtitle>
{subtitle}
</subtitle>

# 关键帧列表

<frames>
{frames_description}
</frames>

# 输出要求

输出 Markdown 格式，用 ## 分节，每节是连贯的段落，不是条目列表。

"""

# ── 教材版 prompt ─────────────────────────────────────────────────
_TEXTBOOK_PROMPT = """你是一个技术图书编辑，负责将编程课视频整理成正式教材。

字幕是老师的原话，你需要：
1. 去掉口水词（"那么"、"就是说"、"大家注意"、"咱们"、"啊"、"嗯"等）
2. **保留老师的讲解逻辑和节奏**——老师先铺垫什么、后解释什么、用什么例子引入，这个顺序要保留
3. 把口语句式改成书面语，但不要改变意思，不要做知识压缩
4. 每个概念要有引入、解释、示例、小结，像教材章节一样完整
5. 代码要完整保留，加注释说明每行的作用
6. 在合适位置引用关键帧（格式：![说明文字](src/frame_001.jpg)）

# 字幕原文

<subtitle>
{subtitle}
</subtitle>

# 关键帧列表

<frames>
{frames_description}
</frames>

# 输出要求

- 用 ## 划分章节，章节标题反映该段的核心内容
- 正文用流畅的书面语段落，不用 bullet points 列知识点
- 代码块完整，有注释
- 关键帧在相关段落后引用
- 输出 Markdown 格式

"""

# ── 顺序讲稿版 prompt ─────────────────────────────────────────────────
_VISUAL_SCRIPT_PROMPT = """你是视频字幕转图文讲稿助手。

**任务**：把视频字幕（ASR 转写）转换为图文 Markdown 讲稿。

**核心原则**：
1. 严格保持老师讲解的时间顺序，不重排内容
2. 不总结、不提炼、不删减教学过程
3. 不补充视频中没有说过的知识
4. 把口语转成自然、完整的书面语（去掉"然后"、"这个"等口头禅）
5. 在时间轴对应位置插入关键帧

# 输入

## 字幕（按时间顺序）

<subtitle>
{subtitle}
</subtitle>

## 关键帧（时间戳 + 路径）

<frames>
{frames_description}
</frames>

# 输出要求

1. **按字幕时间顺序逐段转写**
   - 每段对应讲解的一个自然段落
   - 段落结构：老师说什么 → 你写什么
   - 不要把"先讲 A 再讲 B"重排成"B 的知识点、A 的知识点"

2. **插入关键帧**
   - 在讲到对应时间时插入：`![](src/frame_00h03m45s678ms_scene_001.jpg)`
   - 如果字幕提到"看这张图"、"如图所示"，立即在此处插图
   - 如果附近没有关键帧，可以说明"（讲师展示了画面，但未被抽帧）"

3. **口语书面化**
   - ❌ "那么这个呢就是说我们这个FastAPI啊"
   - ✅ "FastAPI 的路由机制如下"
   - 保留讲解的逻辑顺序，去除冗余口头禅

4. **代码片段**
   - 提取代码，标注语言：```python
   - 如果字幕有逐行讲解，保留讲解内容

5. **格式**
   - 用 `##` 分段（按内容命名，如 `## FastAPI 路由基础`）
   - 不要用 bullet points 列知识点
   - 正文是连贯的段落叙述

6. **图片说明**
   - 每张图后加一句话说明图片内容
   - 例：`![](src/frame_00h03m45s678ms_scene_001.jpg)`
   - *图为 FastAPI 路由代码示例*

直接输出 Markdown，不要解释。
"""


DocMode = Literal["notes", "textbook", "visual_script"]


def _fmt_ts(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class DocumentGenerator:
    """Generate markdown tutorial from keyframes + subtitle."""

    def __init__(self):
        self.vision_mode = config_get("runtime.vision_mode", "appserver")
        self.text_mode = config_get("runtime.text_mode", "appserver")

    def generate(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        video_title: str,
        mode: DocMode = "visual_script",
        srt_text: str | None = None,
        output_dir: Path | None = None,
        video_path: Path | None = None,
        subtitle_path: Path | None = None,
        asr_provider: str = "unknown",
        asr_model: str = "unknown",
    ) -> str:
        """Generate markdown tutorial.

        For long videos, automatically splits into segments and generates each
        one independently, then merges the results. Supports resume from cached segments.

        Args:
            keyframes: List of (frame_path, timestamp_seconds) tuples
            subtitle: Cleaned subtitle text
            video_title: Title of the video
            mode: "visual_script" / "notes" / "textbook"
            srt_text: Raw SRT content for precise time-based splitting
            output_dir: Output directory for segment caching (optional)
            video_path: Original video file path for manifest
            subtitle_path: External subtitle file path for manifest
            asr_provider: ASR provider name for manifest
            asr_model: ASR model name for manifest

        Returns:
            Generated markdown content
        """
        from framelearn.pipeline.segment_splitter import split_segments

        segment_duration = config_get("doc_generation.segment_duration", 90)
        max_kf_per_seg = config_get("doc_generation.max_keyframes_per_segment", 10)

        # Decide whether to use segmented generation
        # Use segments when subtitle is long or there are many keyframes
        use_segments = len(subtitle) > 8000 or len(keyframes) > 20

        if use_segments:
            segments = split_segments(
                subtitle=subtitle,
                keyframes=keyframes,
                segment_duration=float(segment_duration),
                max_keyframes_per_segment=int(max_kf_per_seg),
                srt_text=srt_text,
            )
            print(f"   📐 切分为 {len(segments)} 段生成...")

            # Setup segment cache directory (separate by mode)
            segments_dir = None
            manifest_path = None
            completed_segments = set()
            
            if output_dir:
                segments_dir = output_dir / f"segments_{mode}"
                segments_dir.mkdir(exist_ok=True)
                manifest_path = segments_dir / "manifest.json"
                
                # Load or create manifest
                if manifest_path.exists():
                    manifest = CacheManifest.load(manifest_path)
                    if manifest and video_path:
                        # Validate manifest
                        if manifest.validate(
                            video_path=video_path,
                            subtitle_path=subtitle_path,
                            config_get_fn=config_get,
                            mode=mode,
                            asr_provider=asr_provider,
                            asr_model=asr_model,
                        ):
                            completed_segments = get_completed_segments(manifest_path)
                            print(f"   ✅ Manifest 有效，已完成 {len(completed_segments)}/{len(segments)} 段")
                        else:
                            print("   ⚠️  Manifest 失效（输入或配置已变更），重新生成")
                            get_reporter().record_fallback(
                                "doc_generator.segment_manifest",
                                f"段缓存 manifest 失效（{mode}），已清除并重新生成所有段",
                                detail={"mode": mode},
                            )
                            # Clear old cache
                            for f in segments_dir.glob("seg_*.md"):
                                f.unlink()
                            manifest_path.unlink()
                else:
                    # Create new manifest
                    if video_path:
                        manifest = create_manifest(
                            video_path=video_path,
                            subtitle_path=subtitle_path,
                            config_get_fn=config_get,
                            mode=mode,
                            asr_provider=asr_provider,
                            asr_model=asr_model,
                            segments_total=len(segments),
                        )
                        manifest.save(manifest_path)
                        print(f"   ✅ 创建新 manifest")

            quality_review = config_get("agent.quality_review", False)
            parts = []
            for seg in segments:
                seg_num = seg.index + 1

                # Check cache (with manifest validation)
                if segments_dir and seg.index in completed_segments:
                    cache_file = segments_dir / f"seg_{seg_num:03d}.md"
                    if cache_file.exists():
                        print(f"   ⏭️  第 {seg_num}/{len(segments)} 段已缓存，跳过...")
                        get_reporter().record_cache_hit(
                            "doc_generator.segment_cache",
                            f"第 {seg_num}/{len(segments)} 段（{mode}）命中缓存",
                            detail={"segment_index": seg.index, "mode": mode},
                        )
                        parts.append(cache_file.read_text(encoding="utf-8"))
                        continue

                print(f"   ⚙️  生成第 {seg_num}/{len(segments)} 段"
                      f"（{_fmt_ts(seg.start_time)}~{_fmt_ts(seg.end_time)}）...")

                # Auto-retry on timeout/network errors (up to 3 attempts)
                import time as _time
                last_err = None
                for attempt in range(3):
                    try:
                        if quality_review:
                            part = self._generate_with_review(seg.keyframes, seg.subtitle, mode)
                        else:
                            part = self._generate_single(seg.keyframes, seg.subtitle, mode)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        wait = 15 * (attempt + 1)
                        print(f"   ⚠️  第 {seg_num} 段第 {attempt + 1} 次失败（{e}），{wait}s 后重试...")
                        _time.sleep(wait)

                if last_err:
                    # Mark segment as failed in manifest
                    if manifest_path:
                        mark_segment_completed(manifest_path, seg.index, error=str(last_err))
                    get_reporter().record_failed_segment(
                        "doc_generator",
                        seg_num,
                        str(last_err),
                        detail={"mode": mode, "start_time": seg.start_time, "end_time": seg.end_time},
                    )
                    raise RuntimeError(f"段 {seg_num} 重试 3 次均失败：{last_err}")

                # Save to cache
                if segments_dir:
                    cache_file = segments_dir / f"seg_{seg_num:03d}.md"
                    cache_file.write_text(part, encoding="utf-8")
                    
                    # Mark segment as completed in manifest
                    if manifest_path:
                        mark_segment_completed(manifest_path, seg.index)

                parts.append(part)
            return f"# {video_title}\n\n" + "\n\n---\n\n".join(parts)
        else:
            quality_review = config_get("agent.quality_review", False)
            try:
                if quality_review:
                    return self._generate_with_review(keyframes, subtitle, mode)
                return self._generate_single(keyframes, subtitle, mode)
            except Exception as e:
                raise RuntimeError(f"Document generation failed: {e}") from e

    def _generate_single(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        mode: DocMode,
        model_override: str | None = None,
    ) -> str:
        """Generate markdown for a single segment."""
        if self.vision_mode == "appserver":
            return self._generate_via_appserver(keyframes, subtitle, mode)
        else:
            return self._generate_via_api(keyframes, subtitle, mode, model_override=model_override)

    def _review_segment(self, draft: str, subtitle: str) -> dict:
        """LLM reviews generated segment quality.

        Returns dict with keys: ok (bool), issues (list[str])
        """
        # Fast heuristic checks first (no LLM cost)
        issues = []
        if len(draft.strip()) < 100:
            issues.append("内容过短（< 100 字）")
        filler_words = ["那么", "就是说", "大家", "咱们", "然后呢", "这个吧"]
        for w in filler_words:
            if draft.count(w) > 3:
                issues.append(f"口水词未清理（'{w}' 出现 {draft.count(w)} 次）")
                break
        # Check missing image reference when subtitle mentions visuals
        visual_hints = ["如图", "看图", "可以看到", "演示", "代码"]
        has_visual_hint = any(h in subtitle for h in visual_hints)
        has_image_ref = "![](" in draft or "![" in draft
        if has_visual_hint and not has_image_ref:
            issues.append("字幕提到画面但未插入关键帧")

        return {"ok": len(issues) == 0, "issues": issues}

    def _generate_with_review(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        mode: DocMode,
    ) -> str:
        """Generate a segment with quality review and retry logic.

        Retry strategy:
          Attempt 1: normal model
          Attempt 2: same model, stronger prompt hint
          Attempt 3: upgrade model (review_model → upgrade_model config)
          Fallback: return raw subtitle text (never lose content)
        """
        upgrade_model = config_get("agent.upgrade_model", None)

        for attempt in range(3):
            if attempt == 0:
                draft = self._generate_single(keyframes, subtitle, mode)
            elif attempt == 1:
                # Add review hint to subtitle
                hint = "\n\n[注意：请确保去除所有口水词，并在提到画面时插入对应关键帧]"
                draft = self._generate_single(keyframes, subtitle + hint, mode)
            else:
                # Upgrade model
                draft = self._generate_single(
                    keyframes, subtitle, mode,
                    model_override=upgrade_model,
                )

            review = self._review_segment(draft, subtitle)
            if review["ok"]:
                return draft
            print(f"   ⚠️  质量评审不通过（第 {attempt + 1} 次）：{', '.join(review['issues'])}")

        # Final fallback: preserve subtitle as-is
        print("   ⚠️  3 次重试后仍不通过，降级保存原始字幕")
        get_reporter().record_fallback(
            "doc_generator.quality_review",
            "3 次质量评审均未通过，降级保存原始字幕（未生成润色文档）",
            detail={"mode": mode, "subtitle_preview": subtitle[:80]},
        )
        return subtitle

    def _build_prompt(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        mode: DocMode,
    ) -> str:
        def format_timestamp(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            if h > 0:
                return f"{h:02d}:{m:02d}:{s:02d}"
            else:
                return f"{m:02d}:{s:02d}"

        frames_desc = "\n".join(
            f"关键帧 {i+1} ({format_timestamp(ts)}): {frame.name}"
            for i, (frame, ts) in enumerate(keyframes[:20])
        )

        if mode == "visual_script":
            template = _VISUAL_SCRIPT_PROMPT
        elif mode == "notes":
            template = _NOTES_PROMPT
        else:
            template = _TEXTBOOK_PROMPT

        return template.format(
            subtitle=subtitle,  # no truncation — caller (generate) handles segmentation
            frames_description=frames_desc,
        )

    def _build_multimodal_inputs(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        mode: DocMode,
    ) -> list[dict]:
        """Build structured turn inputs with text + localImage for app-server."""
        def format_timestamp(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            if h > 0:
                return f"{h:02d}:{m:02d}:{s:02d}"
            else:
                return f"{m:02d}:{s:02d}"

        # Select template based on mode
        if mode == "visual_script":
            template = _VISUAL_SCRIPT_PROMPT
        elif mode == "notes":
            template = _NOTES_PROMPT
        else:
            template = _TEXTBOOK_PROMPT

        # Build instruction with subtitle but WITHOUT frame file names
        # (actual frames will be sent as localImage)
        instruction = template.format(
            subtitle=subtitle,
            frames_description="(关键帧将以图片形式提供)",
        )

        inputs: list[dict] = [{"type": "text", "text": instruction}]

        # Add each keyframe with timestamp and localImage
        for i, (frame_path, ts) in enumerate(keyframes[:20]):
            timestamp = format_timestamp(ts)
            inputs.append({
                "type": "text",
                "text": f"\n关键帧 {i+1} [{timestamp}]:",
            })
            # Send absolute path as localImage
            inputs.append({
                "type": "localImage",
                "path": str(frame_path.resolve()),
            })

        return inputs

    def _generate_via_appserver(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        mode: DocMode,
    ) -> str:
        """Generate via codex app-server with multimodal input."""
        from framelearn.app_server.session import AppServerSession

        # Build structured multimodal inputs
        inputs = self._build_multimodal_inputs(keyframes, subtitle, mode)

        session = AppServerSession(workspace=".")
        result = session.run_turn(inputs=inputs)
        session.close()

        if result.error:
            raise RuntimeError(f"Document generation failed: {result.error}")

        # Codex writes the content to a file and returns a summary in final_text.
        # Prefer reading the actual written .md file over the summary message.
        for path in result.written_files:
            if path.endswith(".md"):
                try:
                    return Path(path).read_text(encoding="utf-8")
                except Exception:
                    continue

        # Fallback: return whatever final_text we got
        get_reporter().record_fallback(
            "doc_generator.appserver",
            "未找到写入的 .md 文件，回退为使用 final_text 作为文档内容",
            detail={"mode": mode, "written_files": list(result.written_files)},
        )
        return result.final_text or ""

    def _generate_via_api(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        mode: DocMode,
        model_override: str | None = None,
    ) -> str:
        """Generate via provider_adapter (Vision API)."""
        from framelearn.provider_adapter import call_llm, ProviderConfig, PROVIDERS

        # Build text prompt
        text_prompt = self._build_prompt(keyframes, subtitle, mode)

        # Collect valid keyframe paths (as strings for encode_image)
        image_paths: list[str] = []
        for frame_path, _ in keyframes:
            if not frame_path.exists():
                continue
            image_paths.append(str(frame_path))

        # Build config from settings.toml (overrides env vars)
        provider_key = config_get("runtime.vision_provider", "siliconflow")
        model = model_override or config_get("runtime.vision_model", "Qwen/Qwen2.5-VL-72B-Instruct")
        provider_def = PROVIDERS.get(provider_key)
        if not provider_def:
            raise ValueError(f"Unknown vision_provider: '{provider_key}'")

        import os
        if provider_key == "siliconflow":
            api_key = os.getenv("SILICONFLOW_API_KEY", "")
            base_url = os.getenv("SILICONFLOW_BASE_URL", provider_def["base_url"])
        else:
            api_key = os.getenv("VISION_API_KEY", "")
            base_url = os.getenv("VISION_BASE_URL", provider_def["base_url"])

        config = ProviderConfig(
            provider=provider_key,
            api_key=api_key,
            model=model,
            base_url=base_url,
        )

        try:
            return call_llm(text_prompt, config, images=image_paths, max_tokens=8192, timeout=300)
        except Exception as e:
            raise RuntimeError(f"Vision API 调用失败：{e}")
