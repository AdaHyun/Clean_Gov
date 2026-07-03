from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_NOISE_PATTERNS = [
    "长者版", "无障碍", "首页", "当前位置", "打印本页", "关闭窗口", "上一篇", "下一篇",
    "分享到", "责任编辑", "网站地图", "主办单位", "版权所有", "ICP备案", "English", "网站支持IPv6",
]


@dataclass
class NoiseRemovalResult:
    text: str
    removed_lines: list[str] = field(default_factory=list)
    residual_noise: list[str] = field(default_factory=list)


def detect_noise_hits(text: str, patterns: list[str] | None = None) -> list[str]:
    patterns = patterns or DEFAULT_NOISE_PATTERNS
    return [p for p in patterns if p in (text or "")]


def remove_noise_lines(text: str, patterns: list[str] | None = None, max_noise_line_len: int = 60) -> NoiseRemovalResult:
    patterns = patterns or DEFAULT_NOISE_PATTERNS
    kept, removed = [], []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if any(p in s and len(s) <= max_noise_line_len for p in patterns):
            removed.append(s)
        else:
            kept.append(s)
    cleaned = "\n".join(kept).strip()
    return NoiseRemovalResult(text=cleaned, removed_lines=removed, residual_noise=detect_noise_hits(cleaned, patterns))


# Backward-compatible alias.
noise_hits = detect_noise_hits
