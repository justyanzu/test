"""README 预处理：去除图片相关 Markdown/HTML，保留纯文本。"""

from __future__ import annotations

import re

# Markdown 图片：![alt](url) 或 ![alt][ref]
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|!\[[^\]]*\]\[[^\]]*\]")
# HTML 图片与媒体容器
_HTML_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_HTML_PICTURE = re.compile(r"<picture\b[^>]*>.*?</picture>", re.IGNORECASE | re.DOTALL)
_HTML_VIDEO = re.compile(r"<video\b[^>]*>.*?</video>", re.IGNORECASE | re.DOTALL)
# 仅指向图片的独立链接行（常见 badge 保留，只删明显图片扩展名链接）
_MD_IMAGE_LINK_LINE = re.compile(
    r"^\s*\[[^\]]*\]\([^)]+\.(?:png|jpe?g|gif|svg|webp|ico)(?:\?[^)]*)?\)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# HTML 注释
_HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")


def strip_readme_images(markdown: str | None) -> str:
    """从 README 中移除图片与媒体标记，返回纯文本 Markdown。"""
    if not markdown:
        return ""

    text = markdown
    text = _HTML_COMMENT.sub("", text)
    text = _HTML_PICTURE.sub("", text)
    text = _HTML_VIDEO.sub("", text)
    text = _HTML_IMG.sub("", text)
    text = _MD_IMAGE.sub("", text)
    text = _MD_IMAGE_LINK_LINE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate_for_llm(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n…（README 已截断）"
