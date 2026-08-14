"""Generate version-correct Data-Juicer configs and run them safely."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import yaml


@dataclass(frozen=True)
class DataJuicerRun:
    command: list[str]
    return_code: int
    stdout_path: Path
    stderr_path: Path
    output_path: Path
    duration_seconds: float
    input_document_count: int | None
    output_document_count: int | None


PROGRESS_RE = re.compile(
    r"^(?P<operation>[^\r\n:][^\r\n]*?):\s*"
    r"(?P<percent>\d{1,3})%\|.*?\|\s*"
    r"(?P<done>\d+)/(?P<total>\d+)"
)


def parse_data_juicer_progress(line: str) -> tuple[str, int, int] | None:
    """Parse a Hugging Face/Data-Juicer tqdm record."""
    match = PROGRESS_RE.search(line.strip())
    if not match:
        return None
    total = int(match.group("total"))
    done = int(match.group("done"))
    if total <= 0:
        return None
    operation = match.group("operation").strip().removesuffix("_process")
    return operation, done, total


class ConsoleProgress:
    """Render one compact, stage-aware progress bar in CMD."""

    def __init__(self, stage_label: str, expected_total: int | None = None) -> None:
        self.stage_label = stage_label
        self.expected_total = expected_total
        self.stream = sys.stdout
        self.interactive = self.stream.isatty()
        self._lock = threading.Lock()
        self._operation = ""
        self._last_width = 0
        self._last_bucket = -1
        self._has_rendered = False

    def start(self) -> None:
        total = f"，输入 {self.expected_total} 条" if self.expected_total is not None else ""
        print(f"\n[{self.stage_label}] 开始{total}", flush=True)

    def update_from_line(self, line: str) -> None:
        parsed = parse_data_juicer_progress(line)
        if parsed is None:
            return
        operation, done, total = parsed
        ratio = min(max(done / total, 0.0), 1.0)
        bucket = int(ratio * 20)
        with self._lock:
            operation_changed = operation != self._operation
            if not self.interactive and not operation_changed and bucket == self._last_bucket:
                return
            if self.interactive and operation_changed and self._has_rendered:
                self.stream.write("\n")
            self._operation = operation
            self._last_bucket = bucket
            remaining = max(total - done, 0)
            filled = int(ratio * 24)
            bar = "#" * filled + "-" * (24 - filled)
            rendered = (
                f"[{self.stage_label} | {operation}] "
                f"[{bar}] {ratio * 100:6.2f}%  "
                f"已处理 {done}/{total}，剩余 {remaining}"
            )
            if self.interactive:
                self.stream.write("\r" + rendered.ljust(self._last_width))
                self.stream.flush()
            else:
                print(rendered, flush=True)
            self._last_width = max(self._last_width, len(rendered))
            self._has_rendered = True

    def finish(
        self,
        *,
        return_code: int,
        output_count: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        with self._lock:
            if self.interactive and self._has_rendered:
                self.stream.write("\n")
            duration = (
                f"，耗时 {duration_seconds:.2f} 秒"
                if duration_seconds is not None
                else ""
            )
            if return_code == 0:
                suffix = f"，输出 {output_count} 条" if output_count is not None else ""
                if duration_seconds and self.expected_total is not None:
                    suffix += f"，吞吐 {self.expected_total / duration_seconds:.2f} 条/秒"
                print(f"[{self.stage_label}] 完成{suffix}{duration}", flush=True)
            else:
                print(
                    f"[{self.stage_label}] 失败，返回码 {return_code}{duration}",
                    flush=True,
                )


def _pump_process_stream(
    stream: TextIO,
    log_handle: TextIO,
    progress: ConsoleProgress | None,
) -> None:
    """Tee a child stream to disk and split tqdm records on CR or LF."""
    pending = ""
    log_buffer = ""
    while True:
        character = stream.read(1)
        if character == "":
            break
        log_buffer += character
        if character in "\r\n" or len(log_buffer) >= 4096:
            log_handle.write(log_buffer)
            log_handle.flush()
            log_buffer = ""
        if progress is not None:
            if character in "\r\n":
                if pending:
                    progress.update_from_line(pending)
                    pending = ""
            else:
                pending += character
    if log_buffer:
        log_handle.write(log_buffer)
        log_handle.flush()
    if progress is not None and pending:
        progress.update_from_line(pending)


def build_config(input_path: Path, output_path: Path, work_dir: Path, threshold: int) -> dict[str, Any]:
    """Build a config verified against py-data-juicer 1.5.3 source."""
    return {
        "project_name": f"stage01_{input_path.stem}",
        "executor_type": "default",
        # On Windows, 1.5.3 feeds dataset_path through POSIX shlex and strips
        # backslashes. The structured local dataset config avoids that bug.
        "dataset_path": "",
        "dataset": {
            "configs": [
                {"type": "local", "path": str(input_path.resolve()), "weight": 1.0}
            ]
        },
        "export_path": str(output_path.resolve()),
        "export_type": "jsonl",
        "export_shard_size": 0,
        "export_in_parallel": False,
        "work_dir": str(work_dir.resolve()),
        "text_keys": "text",
        "np": 1,
        "use_cache": False,
        "skip_op_error": False,
        "keep_hashes_in_res_ds": False,
        "process": [
            {
                "document_line_deduplicator": {
                    "frequency_threshold": threshold,
                    "lowercase": False,
                    "ignore_special_character": False,
                    "min_line_length": 2,
                    "skip_brackets": True,
                    "skip_markdown_headers": True,
                    "skip_latex_env": True,
                    "skip_html_tags": True,
                }
            }
        ],
    }


def build_native_config(
    template_path: Path,
    input_path: Path,
    output_path: Path,
    work_dir: Path,
    *,
    project_name: str,
    num_proc: int = 1,
    line_frequency_threshold: int | None = None,
    operator_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Load an operator-only template and inject safe local runtime paths."""
    raw = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("process"), list):
        raise ValueError(f"原生配置缺少 process 列表: {template_path}")
    if num_proc < 1:
        raise ValueError("num_proc 必须是正整数")
    config: dict[str, Any] = dict(raw)
    config.update(
        {
            "project_name": project_name,
            "executor_type": "default",
            "dataset_path": "",
            "dataset": {
                "configs": [
                    {"type": "local", "path": str(input_path.resolve()), "weight": 1.0}
                ]
            },
            "export_path": str(output_path.resolve()),
            "export_type": "jsonl",
            "export_shard_size": 0,
            "export_in_parallel": False,
            "work_dir": str(work_dir.resolve()),
            "text_keys": "text",
            "np": num_proc,
            "use_cache": False,
            "skip_op_error": False,
            # Native tagging ops such as extract_tables_from_html_mapper write
            # into __dj__meta__.  Keep that field in the lane output so the
            # final merger can retain extracted table structure.
            "keep_stats_in_res_ds": True,
            "keep_hashes_in_res_ds": False,
        }
    )
    if line_frequency_threshold is not None:
        if line_frequency_threshold < 1:
            raise ValueError("line_frequency_threshold 必须是正整数")
        for step in config["process"]:
            if isinstance(step, dict) and "document_line_deduplicator" in step:
                step["document_line_deduplicator"]["frequency_threshold"] = line_frequency_threshold
    if operator_overrides:
        found: set[str] = set()
        for step in config["process"]:
            if not isinstance(step, dict):
                continue
            for operator_name, values in operator_overrides.items():
                if operator_name not in step:
                    continue
                if not isinstance(step[operator_name], dict):
                    raise ValueError(f"算子 {operator_name} 的配置不是对象")
                step[operator_name].update(values)
                found.add(operator_name)
        missing = set(operator_overrides) - found
        if missing:
            raise ValueError(f"模板中找不到待覆盖算子: {sorted(missing)}")
    return config


def write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


def run_data_juicer(
    config_path: Path,
    output_path: Path,
    log_dir: Path,
    *,
    environment_overrides: dict[str, str] | None = None,
    progress_label: str | None = None,
    expected_total: int | None = None,
) -> DataJuicerRun:
    """Run through sys.executable, tee logs, and relay native progress."""
    compatibility_entry = Path(__file__).resolve().parents[1] / "scripts" / "data_juicer_windows_entry.py"
    command = [sys.executable, str(compatibility_entry), "--config", str(config_path.resolve())]
    environment = os.environ.copy()
    if environment_overrides:
        environment.update(environment_overrides)
    environment["DATA_JUICER_USE_STDLIB_JSON"] = "1"
    # Keep this path short: datasets embeds the cache path into lock names on
    # Windows and deeply nested per-run paths can exceed legacy path limits.
    cache_dir = config_path.parents[5] / ".hfc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    environment["HF_DATASETS_CACHE"] = str(cache_dir.resolve())
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{config_path.stem}.stdout.log"
    stderr_path = log_dir / f"{config_path.stem}.stderr.log"
    progress = ConsoleProgress(progress_label, expected_total) if progress_label else None
    started = time.perf_counter()
    if progress:
        progress.start()
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_handle,
        stderr_path.open("w", encoding="utf-8") as stderr_handle,
    ):
        process = subprocess.Popen(
            command,
            cwd=str(config_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            bufsize=1,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_thread = threading.Thread(
            target=_pump_process_stream,
            args=(process.stdout, stdout_handle, progress),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_pump_process_stream,
            args=(process.stderr, stderr_handle, progress),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            return_code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            raise
        finally:
            stdout_thread.join()
            stderr_thread.join()
    duration_seconds = round(time.perf_counter() - started, 3)
    output_count = None
    if return_code == 0 and output_path.is_file():
        with output_path.open("r", encoding="utf-8") as output_handle:
            output_count = sum(1 for _ in output_handle)
    if progress:
        progress.finish(
            return_code=return_code,
            output_count=output_count,
            duration_seconds=duration_seconds,
        )
    return DataJuicerRun(
        command,
        return_code,
        stdout_path,
        stderr_path,
        output_path,
        duration_seconds,
        expected_total,
        output_count,
    )
