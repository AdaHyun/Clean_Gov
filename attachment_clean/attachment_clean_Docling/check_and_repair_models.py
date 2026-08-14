import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from safetensors import safe_open


DOCLING_ROOT = Path(__file__).resolve().parent
ROOT = DOCLING_ROOT / "models"

MODEL_SPECS = {
    "layout": {
        "name": "Heron 布局模型",
        "folder": "docling-project--docling-layout-heron",
        "modelscope_id": "ds4sd/docling-layout-heron",
        "files": [
            ("config.json", 100, "json"),
            ("preprocessor_config.json", 100, "json"),
            # 官方权重约 172 MB
            ("model.safetensors", 160_000_000, "safetensors"),
        ],
    },
    "tableformer": {
        "name": "TableFormer 表格模型",
        "folder": "docling-project--docling-models",
        "modelscope_id": "ds4sd/docling-models",
        "files": [
            (
                "model_artifacts/tableformer/fast/tm_config.json",
                100,
                "json",
            ),
            (
                "model_artifacts/tableformer/fast/"
                "tableformer_fast.safetensors",
                140_000_000,
                "safetensors",
            ),
            (
                "model_artifacts/tableformer/accurate/tm_config.json",
                100,
                "json",
            ),
            (
                "model_artifacts/tableformer/accurate/"
                "tableformer_accurate.safetensors",
                200_000_000,
                "safetensors",
            ),
        ],
    },
    "code_formula": {
        "name": "CodeFormulaV2 代码公式模型",
        "folder": "docling-project--CodeFormulaV2",
        "modelscope_id": None,
        "files": [
            ("config.json", 100, "json"),
            # 官方文件大小为 630,993,616 字节
            ("model.safetensors", 600_000_000, "safetensors"),
        ],
    },
    "picture_classifier": {
        "name": "DocumentFigureClassifier 图片分类模型",
        "folder": "docling-project--DocumentFigureClassifier-v2.5",
        "modelscope_id": None,
        "files": [
            ("config.json", 100, "json"),
            ("model.safetensors", 15_000_000, "safetensors"),
            ("model.onnx", 15_000_000, "binary"),
        ],
    },
}


def readable_size(size: int) -> str:
    return f"{size / 1024 / 1024:.2f} MB"


def validate_file(
    path: Path,
    minimum_size: int,
    file_type: str,
) -> tuple[bool, str]:
    if not path.exists():
        return False, "文件缺失"

    size = path.stat().st_size

    if size < minimum_size:
        return (
            False,
            f"文件过小：{readable_size(size)}，"
            f"最低要求 {readable_size(minimum_size)}",
        )

    try:
        if file_type == "json":
            with path.open("r", encoding="utf-8") as file:
                json.load(file)

        elif file_type == "safetensors":
            # 只读取模型头部，不会把完整模型加载进内存
            with safe_open(
                str(path),
                framework="pt",
                device="cpu",
            ) as model_file:
                keys = list(model_file.keys())

            if not keys:
                return False, "Safetensors 中没有任何张量"

    except Exception as exc:
        return False, f"文件无法正常读取：{exc}"

    return True, readable_size(size)


def check_model(model_key: str, show_details: bool = True) -> bool:
    spec = MODEL_SPECS[model_key]
    folder = ROOT / spec["folder"]

    if show_details:
        print(f"\n【{spec['name']}】")
        print(f"目录：{folder}")

    if not folder.exists():
        if show_details:
            print("结论：目录不存在")
        return False

    all_ok = True

    for relative_path, minimum_size, file_type in spec["files"]:
        path = folder / relative_path

        ok, message = validate_file(
            path=path,
            minimum_size=minimum_size,
            file_type=file_type,
        )

        if show_details:
            status = "正常" if ok else "异常"
            print(f"  [{status}] {relative_path}")
            print(f"         {message}")

        if not ok:
            all_ok = False

    if show_details:
        print(
            "结论："
            + ("模型基本完整" if all_ok else "模型缺失或未下载完整")
        )

    return all_ok


def repair_from_modelscope(model_key: str) -> bool:
    spec = MODEL_SPECS[model_key]
    model_id = spec["modelscope_id"]

    if not model_id:
        print(f"\n{spec['name']} 没有配置魔塔直接镜像，跳过。")
        return False

    try:
        from modelscope import snapshot_download
    except ImportError:
        print(
            "\n缺少 modelscope，请先执行：\n"
            "python -m pip install modelscope"
        )
        return False

    target = ROOT / spec["folder"]

    # 不直接在残缺目录上混合下载，先备份旧目录
    if target.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = ROOT / f"{target.name}_broken_{timestamp}"

        print(f"\n备份旧目录：\n{target}\n→ {backup}")
        target.rename(backup)

    target.mkdir(parents=True, exist_ok=True)

    print(f"\n开始从魔塔下载：{model_id}")
    print(f"目标目录：{target}")

    try:
        snapshot_download(
            model_id=model_id,
            local_dir=str(target),
        )
    except Exception as exc:
        print(f"下载失败：{exc}")
        return False

    print("下载结束，重新检查模型。")
    return check_model(model_key)


def main() -> None:
    global ROOT
    parser = argparse.ArgumentParser(description="检查并修复 Docling 本地模型。")
    parser.add_argument(
        "--models-root",
        type=Path,
        default=ROOT,
        help="模型根目录；相对路径以 attachment_clean_Docling 为基准。",
    )
    args = parser.parse_args()
    ROOT = args.models_root if args.models_root.is_absolute() else DOCLING_ROOT / args.models_root
    ROOT = ROOT.resolve()
    ROOT.mkdir(parents=True, exist_ok=True)

    print(f"模型根目录：{ROOT}")

    results = {}

    for model_key in MODEL_SPECS:
        results[model_key] = check_model(model_key)

    repairable = [
        key
        for key in ("layout", "tableformer")
        if not results[key]
    ]

    print("\n================ 检查汇总 ================")

    for model_key, ok in results.items():
        name = MODEL_SPECS[model_key]["name"]
        print(f"{name}：{'完整' if ok else '不完整'}")

    if not repairable:
        print("\n布局模型和表格模型都完整，不需要从魔塔补下载。")
        return

    print("\n需要从魔塔重新下载：")

    for model_key in repairable:
        print(f"- {MODEL_SPECS[model_key]['name']}")

    answer = input(
        "\n是否现在备份残缺目录并从魔塔重新下载？"
        "输入 y 继续："
    ).strip().lower()

    if answer != "y":
        print("已取消下载。")
        return

    for model_key in repairable:
        repair_from_modelscope(model_key)


if __name__ == "__main__":
    main()
