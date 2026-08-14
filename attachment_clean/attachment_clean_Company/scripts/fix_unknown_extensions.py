import argparse
import csv
import mimetypes
import subprocess
import zipfile
from pathlib import Path


def run_file_command(path: Path) -> tuple[str, str]:
    """调用 Linux file 命令获取 MIME 和描述。"""
    try:
        mime = subprocess.check_output(
            ["file", "--brief", "--mime-type", str(path)],
            text=True,
            errors="replace",
        ).strip()

        description = subprocess.check_output(
            ["file", "--brief", str(path)],
            text=True,
            errors="replace",
        ).strip()

        return mime, description
    except Exception as exc:
        return "", f"file命令失败: {exc}"


def detect_zip_office(path: Path) -> str | None:
    """区分 docx、xlsx、pptx。"""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())

            if any(name.startswith("word/") for name in names):
                return ".docx"

            if any(name.startswith("xl/") for name in names):
                return ".xlsx"

            if any(name.startswith("ppt/") for name in names):
                return ".pptx"

            if "mimetype" in names:
                try:
                    value = archive.read("mimetype").decode(
                        "utf-8", errors="ignore"
                    )

                    if "opendocument.text" in value:
                        return ".odt"

                    if "opendocument.spreadsheet" in value:
                        return ".ods"

                    if "opendocument.presentation" in value:
                        return ".odp"
                except Exception:
                    pass

            return ".zip"

    except Exception:
        return None


def detect_extension(path: Path) -> tuple[str | None, str, str]:
    """根据文件头、ZIP结构和MIME识别真实后缀。"""
    try:
        header = path.read_bytes()[:16]
    except Exception as exc:
        return None, "", f"读取失败: {exc}"

    mime, description = run_file_command(path)

    # PDF
    if header.startswith(b"%PDF-"):
        return ".pdf", mime, description

    # RTF
    if header.startswith(b"{\\rtf"):
        return ".rtf", mime, description

    # OOXML / ZIP
    if header.startswith(b"PK\x03\x04"):
        extension = detect_zip_office(path)
        return extension, mime, description

    # 旧版 Office OLE
    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        mapping = {
            "application/msword": ".doc",
            "application/vnd.ms-excel": ".xls",
            "application/vnd.ms-powerpoint": ".ppt",
        }

        if mime in mapping:
            return mapping[mime], mime, description

        description_lower = description.lower()

        if "microsoft word" in description_lower:
            return ".doc", mime, description

        if "microsoft excel" in description_lower:
            return ".xls", mime, description

        if "microsoft powerpoint" in description_lower:
            return ".ppt", mime, description

    mime_mapping = {
        "application/pdf": ".pdf",
        "application/msword": ".doc",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/rtf": ".rtf",
        "text/rtf": ".rtf",
        "application/zip": ".zip",
        "application/x-rar": ".rar",
        "application/vnd.rar": ".rar",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/tiff": ".tif",
    }

    if mime in mime_mapping:
        return mime_mapping[mime], mime, description

    guessed = mimetypes.guess_extension(mime) if mime else None
    return guessed, mime, description


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = Path(args.input_dir).resolve()
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() == ".unknown"
        and path.stat().st_size > 0
    )

    counts = {
        "total": 0,
        "would_rename": 0,
        "renamed": 0,
        "collision": 0,
        "manual_review": 0,
    }

    rows = []

    for source in files:
        counts["total"] += 1

        extension, mime, description = detect_extension(source)

        if not extension or extension == ".unknown":
            action = "manual_review"
            destination = ""
            counts["manual_review"] += 1
        else:
            target = source.with_suffix(extension.lower())
            destination = str(target)

            if target.exists() and target != source:
                action = "collision"
                counts["collision"] += 1
            elif args.apply:
                source.rename(target)
                action = "renamed"
                counts["renamed"] += 1
            else:
                action = "would_rename"
                counts["would_rename"] += 1

        rows.append(
            {
                "source_path": str(source),
                "relative_path": str(source.relative_to(root)),
                "detected_extension": extension or "",
                "mime_type": mime,
                "description": description,
                "action": action,
                "destination_path": destination,
            }
        )

    with report_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_path",
                "relative_path",
                "detected_extension",
                "mime_type",
                "description",
                "action",
                "destination_path",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print("扫描目录：", root)
    print("非零字节 .unknown：", counts["total"])
    print("可修改：", counts["would_rename"])
    print("已修改：", counts["renamed"])
    print("目标文件冲突：", counts["collision"])
    print("需要人工检查：", counts["manual_review"])
    print("报告：", report_path)


if __name__ == "__main__":
    main()
