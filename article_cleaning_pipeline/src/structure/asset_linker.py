from __future__ import annotations

from pathlib import Path


def mark_attachment_references(attachments: list[dict], text: str) -> tuple[list[dict], int]:
    linked, matched = [], 0
    for asset in attachments or []:
        item = dict(asset)
        name = item.get("name") or Path(str(item.get("local_path", ""))).name
        item["referenced_in_text"] = bool(name and name[:20] in (text or "")) or "附件" in (text or "")
        matched += int(item["referenced_in_text"])
        linked.append(item)
    return linked, matched


def classify_image_role(image: dict) -> str:
    url = (image or {}).get("url", "").lower()
    if any(x in url for x in ["logo", "icon", "copy", "wx", "qrcode"]):
        return "decorative"
    return "body_image"


def mark_image_roles(images: list[dict]) -> list[dict]:
    output = []
    for image in images or []:
        item = dict(image)
        item["image_role"] = item.get("image_role") or classify_image_role(item)
        output.append(item)
    return output
