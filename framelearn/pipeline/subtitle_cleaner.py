"""Subtitle text cleaning based on Bilitato's subtitle processor."""

import re


class SubtitleCleaner:
    """Clean raw subtitle text from ASR."""

    def clean(self, raw_text: str) -> str:
        """Clean raw subtitle text.

        Applies Bilitato-inspired cleaning rules:
        1. Remove bracket content ([音乐], (掌声), etc.)
        2. Full-width to half-width conversion
        3. Merge duplicate consecutive lines
        4. Sentence break optimization

        Args:
            raw_text: Raw subtitle text from ASR

        Returns:
            Cleaned subtitle text
        """
        if not raw_text:
            return ""

        text = raw_text

        # 1. Remove bracket content
        text = re.sub(r'[\[【（\(].*?[\]】）\)]', '', text)

        # 2. Full-width to half-width
        text = text.replace('，', ',')
        text = text.replace('。', '.')
        text = text.replace('！', '!')
        text = text.replace('？', '?')
        text = text.replace('：', ':')
        text = text.replace('；', ';')
        text = text.replace('"', '"')
        text = text.replace('"', '"')
        text = text.replace(''', "'")
        text = text.replace(''', "'")
        text = text.replace('　', ' ')  # full-width space

        # 3. Merge consecutive duplicate lines
        lines = text.split('\n')
        unique_lines = []
        for line in lines:
            line = line.strip()
            if line and (not unique_lines or unique_lines[-1] != line):
                unique_lines.append(line)
        text = '\n'.join(unique_lines)

        # 4. Sentence break optimization (add newline after punctuation)
        text = re.sub(r'([。.!?！？])\s*', r'\1\n', text)

        # 5. Remove excessive whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)  # max 2 newlines
        text = re.sub(r' {2,}', ' ', text)  # max 1 space

        return text.strip()

    @staticmethod
    def strip_timestamps(srt_text: str) -> str:
        """Strip SRT/VTT formatting, return plain text only.

        Removes:
        - Sequence numbers (1, 2, 3...)
        - Timestamp lines (00:00:01,000 --> 00:00:03,000)
        - VTT header (WEBVTT)
        - Empty lines

        Args:
            srt_text: Raw SRT or VTT content

        Returns:
            Plain text with one line per subtitle entry
        """
        lines = []
        for line in srt_text.splitlines():
            line = line.strip()
            # Skip empty lines
            if not line:
                continue
            # Skip WEBVTT header
            if line.startswith("WEBVTT"):
                continue
            # Skip sequence numbers (pure digits)
            if line.isdigit():
                continue
            # Skip SRT timestamp lines (00:00:00,000 --> 00:00:00,000)
            if re.match(r'^\d{1,2}:\d{2}:\d{2}[,\.]\d{3}\s*-->', line):
                continue
            # Skip VTT timestamp lines (00:00.000 --> 00:00.000)
            if re.match(r'^\d{1,2}:\d{2}[,\.]\d{3}\s*-->', line):
                continue
            lines.append(line)
        return "\n".join(lines)
