"""One-off migration: convert caption/text_representation under images in
existing blog.md files to blockquote annotations.

Boundary detection is exact: the caption and text_representation rendered
into blog.md came from the same FrameEvaluation objects that produced
srt_picture.md, so for each image filename we take the (caption,
text_representation) pair from srt_picture.md and require a verbatim line
match in blog.md before rewriting. Anything that does not match exactly is
left untouched and reported.

Usage:
    python scripts/migrate_blog_blockquotes.py <output_dir> [...]

Writes blog.md.new next to blog.md for review; pass --write to replace.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

IMG_RE = re.compile(r"^!\[图\]\((src/[^)]+)\)\s*$")


def parse_srt_annotations(srt_path: Path) -> dict[str, tuple[str, list[str]]]:
    """filename -> (caption, text_representation lines) from srt_picture.md."""
    lines = srt_path.read_text(encoding="utf-8").split("\n")
    result: dict[str, tuple[str, list[str]]] = {}
    i = 0
    while i < len(lines):
        m = IMG_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fname = m.group(1)
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        caption = ""
        # Caption line is the italic rendering of the exact caption string;
        # the caption itself may contain '*' (e.g. "4*4矩阵"), so match by
        # surrounding markers only, then strip them.
        if j < len(lines) and re.fullmatch(r"\*[\s\S]+\*", lines[j].strip()):
            caption = lines[j].strip()[1:-1]
            j += 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        text_lines: list[str] = []
        while j < len(lines) and not lines[j].startswith(">") and not (
            lines[j].startswith("![图](")
        ):
            text_lines.append(lines[j])
            j += 1
        text_lines = text_lines[:-1] if text_lines and not text_lines[-1].strip() else text_lines
        result[fname] = (caption, text_lines)
        i = j
    return result


def migrate_blog(blog_path: Path, srt_path: Path) -> tuple[str, list[str]]:
    """Return (new_content, report_lines)."""
    annotations = parse_srt_annotations(srt_path)
    lines = blog_path.read_text(encoding="utf-8").split("\n")
    out: list[str] = []
    report: list[str] = []
    i = 0
    converted = 0
    while i < len(lines):
        m = IMG_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        fname = m.group(1)
        out.append(lines[i])
        i += 1

        caption, text_lines = annotations.get(fname, ("", []))
        # Locate caption line (verbatim "*caption*") after optional blanks.
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        cap_found = False
        if caption and j < len(lines) and lines[j].strip() == f"*{caption}*":
            cap_found = True
        elif not caption and fname not in annotations:
            report.append(f"SKIP {fname}: no annotation in srt_picture.md")

        if not cap_found:
            if caption:
                report.append(
                    f"SKIP {fname}: caption line mismatch "
                    f"(found {lines[j].strip()[:40]!r} after image)"
                )
            elif fname in annotations and not text_lines:
                # caption empty and no text_representation: nothing to quote
                pass
            elif fname in annotations and text_lines:
                report.append(f"SKIP {fname}: caption empty but text_representation exists")
            continue

        # Build the quoted block from the verified caption + text lines.
        quote: list[str] = [f"> *{caption}*"]
        rep = [l for l in text_lines]
        while rep and not rep[0].strip():
            rep.pop(0)
        while rep and not rep[-1].strip():
            rep.pop()
        if rep:
            quote.append(">")
            for l in rep:
                quote.append(f"> {l}" if l.strip() else ">")

        # Verify text_representation matches blog verbatim, line by line.
        k = j + 1
        while k < len(lines) and not lines[k].strip():
            k += 1
        end = k
        ok = True
        for l in rep:
            if end >= len(lines) or lines[end] != l:
                ok = False
                break
            end += 1
        if not ok:
            # Quote the caption only; leave the rest untouched.
            report.append(f"PARTIAL {fname}: text_representation mismatch, quoting caption only")
            out.append("")
            out.extend(f"> *{caption}*")
            i = j + 1  # after caption line
            continue

        out.append("")
        out.extend(quote)
        i = end
        converted += 1
    report.append(f"OK converted={converted} images with quoted annotations")
    return "\n".join(out), report


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--write"]
    do_write = "--write" in argv[1:]
    if not args:
        print(__doc__)
        return 2
    rc = 0
    for d in args:
        out_dir = Path(d)
        blog = out_dir / "blog.md"
        srt = out_dir / "srt_picture.md"
        if not blog.exists() or not srt.exists():
            print(f"[missing] {out_dir}: blog.md or srt_picture.md not found")
            rc = 1
            continue
        new_content, report = migrate_blog(blog, srt)
        print(f"== {out_dir}")
        for line in report:
            print("  ", line)
        if do_write:
            blog.write_text(new_content, encoding="utf-8")
            print("   written:", blog)
        else:
            target = out_dir / "blog.md.new"
            target.write_text(new_content, encoding="utf-8")
            print("   preview:", target)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
