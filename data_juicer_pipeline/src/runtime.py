"""Runtime diagnostics tied to the interpreter that launched the script."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from paths import (
    COMPONENT_ROOT,
    DISCOVERED_PYTHON,
    EXPECTED_DATA_JUICER_VERSION,
    INPUT_DIR,
    RUNS_DIR,
    PROJECT_ROOT,
    REQUESTED_PYTHON,
    WEB_CORPUS_DIR,
)


def _writable_directory(path: Path) -> bool:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def collect_environment(*, test_cli: bool = True) -> dict[str, Any]:
    """Collect environment facts without relying on shell activation or PATH."""
    result: dict[str, Any] = {
        "sys_executable": sys.executable,
        "python_version": sys.version,
        "pip_version": "",
        "py_data_juicer_version": "",
        "data_juicer_path": "",
        "document_line_deduplicator_importable": False,
        "document_line_deduplicator_source": "",
        "operator_registry_name": "document_line_deduplicator",
        "operator_signature": "",
        "console_scripts": {},
        "dj_process_exe": "",
        "dj_process_exists": False,
        "cli_command": [sys.executable, "-m", "data_juicer.tools.process_data", "--config", "<config.yaml>"],
        "cli_runnable": False,
        "cli_return_code": None,
        "cli_stderr": "",
        "windows_compatible": os.name == "nt",
        "current_working_directory": str(Path.cwd()),
        "project_root": str(PROJECT_ROOT),
        "component_root": str(COMPONENT_ROOT),
        "default_web_corpus_directory": str(WEB_CORPUS_DIR),
        "default_web_corpus_directory_exists": WEB_CORPUS_DIR.is_dir(),
        "requested_python": str(REQUESTED_PYTHON),
        "requested_python_exists": REQUESTED_PYTHON.is_file(),
        "discovered_python": str(DISCOVERED_PYTHON),
        "running_discovered_python": Path(sys.executable).resolve() == DISCOVERED_PYTHON.resolve(),
    }
    try:
        result["pip_version"] = importlib.metadata.version("pip")
    except importlib.metadata.PackageNotFoundError:
        result["pip_version"] = "not installed"
    try:
        result["py_data_juicer_version"] = importlib.metadata.version("py-data-juicer")
    except importlib.metadata.PackageNotFoundError:
        result["py_data_juicer_version"] = "not installed"
    spec = importlib.util.find_spec("data_juicer")
    result["data_juicer_path"] = spec.origin if spec and spec.origin else ""
    try:
        from data_juicer.ops.deduplicator.document_line_deduplicator import DocumentLineDeduplicator

        result["document_line_deduplicator_importable"] = True
        result["document_line_deduplicator_source"] = inspect.getsourcefile(DocumentLineDeduplicator) or ""
        result["operator_signature"] = str(inspect.signature(DocumentLineDeduplicator.__init__))
    except Exception as exc:  # diagnostic must report the import error
        result["operator_import_error"] = repr(exc)
    entries = {
        item.name: item.value
        for item in importlib.metadata.entry_points(group="console_scripts")
        if item.name.startswith("dj-")
    }
    result["console_scripts"] = entries
    scripts = Path(sys.executable).resolve().parent / "Scripts"
    dj_process = scripts / "dj-process.exe"
    result["dj_process_exe"] = str(dj_process)
    result["dj_process_exists"] = dj_process.is_file()
    usage = shutil.disk_usage(PROJECT_ROOT)
    result["disk_space"] = {"total": usage.total, "used": usage.used, "free": usage.free}
    result["input_directory_writable"] = _writable_directory(INPUT_DIR)
    result["runs_directory_writable"] = _writable_directory(RUNS_DIR)
    if test_cli:
        process = subprocess.run(
            [sys.executable, "-m", "data_juicer.tools.process_data", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        result["cli_return_code"] = process.returncode
        result["cli_runnable"] = process.returncode == 0 and "--config" in process.stdout
        result["cli_stderr"] = process.stderr[-2000:]
    result["ok"] = bool(
        result["running_discovered_python"]
        and result["py_data_juicer_version"] == EXPECTED_DATA_JUICER_VERSION
        and result["document_line_deduplicator_importable"]
        and (result["cli_runnable"] if test_cli else True)
    )
    return result


def write_environment(path: Path, *, test_cli: bool = True) -> dict[str, Any]:
    environment = collect_environment(test_cli=test_cli)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8")
    return environment
