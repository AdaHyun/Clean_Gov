import argparse
import csv
import subprocess
from pathlib import Path


# 已经正常，或者明确不属于文档解析范围的后缀。
KNOWN_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".csv", ".md", ".rtf",
    ".zip", ".rar", ".7z", ".ceb",
    ".jpg", ".jpeg", ".png", ".tif", ".tiff",
    ".mp4", ".m4v", ".mov",
    ".xml", ".rels", ".bin",
}

MIME_TO_EXTENSION = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
}


def detect_mime(path: Path) -> str:
    try:
        result = subprocess.run(
            ["file", "--brief", "--mime-type", "--", str(path)],
            capture_output=True,
            text=True,
            errors="replace",
            check=True,
        )
        return result.stdout.strip()
    except Exception as exc:
        return f"detect_error:{exc}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = Path(args.input_dir).resolve()
    report = Path(args.report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    counts = {
        "scanned_abnormal": 0,
        "would_rename": 0,
        "renamed": 0,
        "collision": 0,
        "manual_review": 0,
    }

    for source in sorted(root.rglob("*"), key=lambda p: str(p)):
        if not source.is_file():
            continue

        try:
            size = source.stat().st_size
        except OSError:
            continue

        if size <= 0:
            continue

        current_suffix = source.suffix.lower()

        # 正常后缀和已知非文档类型不处理。
        if current_suffix in KNOWN_SUFFIXES:
            continue

        counts["scanned_abnormal"] += 1
        mime = detect_mime(source)
        detected_extension = MIME_TO_EXTENSION.get(mime)

        if not detected_extension:
            action = "manual_review"
            target = None
            counts["manual_review"] += 1
        else:
            # 必须追加后缀，不能使用 with_suffix。
            target = Path(str(source) + detected_extension)

            if target.exists():
                action = "collision"
                counts["collision"] += 1
            elif args.apply:
                source.rename(target)
                action = "renamed"
                counts["renamed"] += 1
            else:
                action = "would_rename"
                counts["would_rename"] += 1

        rows.append({
            "source_path": str(source),
            "current_suffix": current_suffix or "<none>",
            "mime_type": mime,
            "detected_extension": detected_extension or "",
            "target_path": str(target) if target else "",
            "action": action,
        })

    with report.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_path",
                "current_suffix",
                "mime_type",
                "detected_extension",
                "target_path",
                "action",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print("扫描到的异常后缀文件：", counts["scanned_abnormal"])
    print("可自动修改：", counts["would_rename"])
    print("已经修改：", counts["renamed"])
    print("目标冲突：", counts["collision"])
    print("需要人工检查：", counts["manual_review"])
    print("报告：", report)


if __name__ == "__main__":
    main()
