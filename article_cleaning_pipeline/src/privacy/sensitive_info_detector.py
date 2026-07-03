from __future__ import annotations

import re

SENSITIVE_RULES = {
    "id_card": re.compile(r"\b\d{17}[\dXx]\b"),
    "mobile": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "email": re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"0\d{2,3}-?\d{7,8}"),
    "patient_case": re.compile(r"(患者|病例|个案|密切接触者|活动轨迹|行程轨迹)"),
    "address": re.compile(r"(地址|邮寄地址|通讯地址)[:：].{5,80}"),
}


def detect_sensitive_info(text: str) -> tuple[list[dict], str, str]:
    hits = []
    for name, pattern in SENSITIVE_RULES.items():
        for match in pattern.finditer(text or ""):
            hits.append({"type": name, "span": [match.start(), match.end()], "sample": match.group(0)[:40]})
    risk, action = "low", "mark_only"
    if any(h["type"] in ["id_card", "patient_case"] for h in hits):
        risk, action = "high", "manual_review"
    elif any(h["type"] in ["mobile", "address"] for h in hits):
        risk, action = "medium", "mask_recommended"
    return hits, risk, action
