import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


DOCLING_ROOT = Path(__file__).resolve().parent
DEFAULT_WORK_DIR = DOCLING_ROOT / "data" / "pptx_rebuild"


def resolve_project_path(path: Path) -> Path:
    return (path if path.is_absolute() else DOCLING_ROOT / path).resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="将解包后的 PPTX 目录重建为 .pptx 文件。")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_WORK_DIR / "input.pptx.bin",
        help="PPTX 解包目录。",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_WORK_DIR / "output_rebuilt.pptx",
        help="重建后的 PPTX 文件。",
    )
    args = parser.parse_args()
    source_dir = resolve_project_path(args.source_dir)
    output_file = resolve_project_path(args.output_file)

    if not source_dir.is_dir():
        raise NotADirectoryError(f"源路径不是目录：{source_dir}")

    required_items = [
        source_dir / "[Content_Types].xml",
        source_dir / "_rels" / ".rels",
        source_dir / "ppt" / "presentation.xml",
    ]

    missing = [str(path) for path in required_items if not path.exists()]

    if missing:
        raise FileNotFoundError(
            "目录不是完整的 PPTX 解包结构，缺少：\n"
            + "\n".join(missing)
        )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    if output_file.exists():
        output_file.unlink()

    file_count = 0

    with ZipFile(
        output_file,
        mode="w",
        compression=ZIP_DEFLATED,
    ) as archive:
        for path in source_dir.rglob("*"):
            if not path.is_file():
                continue

            # 压缩包内必须从 [Content_Types].xml、ppt/ 等开始，
            # 不能把外层目录名称一起打包进去。
            archive_path = path.relative_to(source_dir).as_posix()
            archive.write(path, archive_path)
            file_count += 1

    print("PPTX 重建完成")
    print(f"文件数量：{file_count}")
    print(f"输出路径：{output_file}")
    print(f"文件大小：{output_file.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
