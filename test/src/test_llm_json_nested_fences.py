"""Regression tests for parse_json_object when the model embeds an inner
code fence inside a JSON string value.

Before the fix, ``parse_json_object`` used ``re.search(r"```(json)?\s*(.*?)```")``
which is a non-greedy match. When the model's response had:

  ```json
  { "blog_markdown": "... ```python\nimport torch\n...``` ..." }
  ```

the outer fence regex closed at the inner ``\`\`\`python`` token and
truncated the JSON. The result was an "Unterminated string" error,
which the BlogGenerator surfaced as ``schema_mismatch`` and the whole
chunk failed.

After the fix we locate the outermost ``{ ... }`` by brace-balance,
skipping string literals, which is robust against any number of
nested code fences.
"""

import json

from framelearn.pipeline.llm_json import parse_json_object


def test_outer_json_with_inner_fence_parses():
    raw = (
        '```json\n'
        '{\n'
        '  "blog_markdown": "hello ```python\\nimport torch\\nprint(1)``` world",\n'
        '  "frame_requests": []\n'
        '}\n'
        '```\n'
    )
    parsed = parse_json_object(raw)
    assert parsed is not None
    assert parsed["blog_markdown"].startswith("hello ")
    assert parsed["blog_markdown"].endswith(" world")
    assert parsed["frame_requests"] == []


def test_chunk_17_repro_from_real_dump():
    """Use the actual dump from the failed chunk-17 run."""
    with open(
        "output/第四节分类任务(1)/temp/raw_responses.jsonl", encoding="utf-8"
    ) as f:
        for line in f:
            entry = json.loads(line)
            if (
                entry.get("chunk_index") == 17
                and entry.get("attempt") == 0
            ):
                raw = entry["response"]
                break
        else:
            import pytest

            pytest.skip(
                "output/第四节分类任务(1)/temp/raw_responses.jsonl not "
                "present (chunk 17 only fails when repro'd)"
            )

    parsed = parse_json_object(raw)
    assert parsed is not None
    assert "blog_markdown" in parsed
    assert "frame_requests" in parsed
    assert len(parsed["frame_requests"]) == 2


def test_bare_object_still_works():
    raw = '{"blog_markdown": "x", "frame_requests": []}'
    assert parse_json_object(raw) == {
        "blog_markdown": "x",
        "frame_requests": [],
    }


def test_no_object_returns_none():
    assert parse_json_object("just some prose") is None
    assert parse_json_object("") is None
    assert parse_json_object("```\nnot json\n```") is None


def test_unterminated_object_returns_none():
    """Opening brace never closes: brace scanner returns None cleanly."""
    raw = '{"blog_markdown": "x"'
    assert parse_json_object(raw) is None


def test_brace_inside_string_does_not_break_balance():
    raw = '{"blog_markdown": "let {x = 1}", "frame_requests": []}'
    parsed = parse_json_object(raw)
    assert parsed is not None
    assert parsed["blog_markdown"] == "let {x = 1}"


def test_multiple_inner_fences_all_skipped():
    raw = (
        '```json\n'
        "{\n"
        '  "a": "first ```python\\nx=1``` block",\n'
        '  "b": "second ```javascript\\nlet y=2``` block",\n'
        '  "c": [1, 2, {"d": "third ```sql\\nSELECT 1``` end"}]\n'
        "}\n"
        "```\n"
    )
    parsed = parse_json_object(raw)
    assert parsed is not None
    assert "first " in parsed["a"]
    assert parsed["c"][2]["d"].endswith(" end")