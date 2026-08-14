"""Project path discovery independent of the current working directory."""

from __future__ import annotations

from pathlib import Path


COMPONENT_ROOT = Path(__file__).resolve().parents[1]
CLEAN_GOV_ROOT = COMPONENT_ROOT.parent
# PROJECT_ROOT now means the shared Clean_Gov root.  Keep the explicit
# COMPONENT_ROOT name for files owned by this Python component.
PROJECT_ROOT = CLEAN_GOV_ROOT
CONFIG_DIR = COMPONENT_ROOT / "configs" / "01_text_cleaning"
NATIVE_CONFIG_DIR = COMPONENT_ROOT / "configs" / "native"
LLM_PROVIDER_CONFIG = COMPONENT_ROOT / "configs" / "llm_providers.yaml"
LLM_TAG_LABEL_CONFIG = COMPONENT_ROOT / "configs" / "llm_tag_labels.zh-CN.json"
LLM_ENV_FILE = COMPONENT_ROOT / ".env.local"
DATA_DIR = COMPONENT_ROOT / "data"
INPUT_DIR = DATA_DIR / "input"
RUNS_DIR = DATA_DIR / "runs"
WEB_CORPUS_DIR = (
    CLEAN_GOV_ROOT
    / "text_clean"
    / "data"
    / "output"
    / "gov-webStructure-clean"
)
INTERMEDIATE_DIR = DATA_DIR / "intermediate"
OUTPUT_DIR = DATA_DIR / "output"
QUARANTINE_DIR = DATA_DIR / "quarantine"
REPORT_DIR = DATA_DIR / "reports"
LOG_DIR = DATA_DIR / "logs"

# The prompt named dj-envs, but the host's conda registry contains dj-env.
REQUESTED_PYTHON = Path(r"D:\LZH\Environment\Anaconda3\anaconda3\envs\dj-envs\python.exe")
DISCOVERED_PYTHON = Path(r"D:\LZH\Environment\Anaconda3\anaconda3\envs\dj-env\python.exe")
EXPECTED_DATA_JUICER_VERSION = "1.5.3"

# The attachment parser writes one directory per source document under this
# sibling component.  Keeping the default derived from CLEAN_GOV_ROOT makes the
# native pipeline independent from the current working directory.
DEFAULT_ATTACHMENT_DIR = (
    CLEAN_GOV_ROOT
    / "attachment_clean"
    / "attachment_clean_Company"
    / "data"
    / "documents"
)


def resolve_from_clean_gov(value: str | Path) -> Path:
    """Resolve an explicit path relative to Clean_Gov, never shell cwd."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = CLEAN_GOV_ROOT / path
    return path.resolve()


def ensure_base_directories() -> None:
    """Create only directories owned by this component."""
    for path in (INPUT_DIR, RUNS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def run_paths(run_id: str) -> dict[str, Path]:
    """Return the unified paths for one run without creating them."""
    root = RUNS_DIR / run_id
    return {
        "root": root,
        "intermediate": root / "intermediate",
        "output": root / "output",
        "reports": root / "reports",
        "quarantine": root / "quarantine",
        "logs": root / "logs",
    }


def legacy_run_paths(run_id: str, *, retry: bool = False) -> dict[str, Path]:
    """Return the pre-unified layout so historical runs remain readable."""
    kind = "retries" if retry else "runs"
    return {
        "root": DATA_DIR,
        "intermediate": INTERMEDIATE_DIR / kind / run_id,
        "output": OUTPUT_DIR,
        "reports": REPORT_DIR / kind / run_id,
        "quarantine": QUARANTINE_DIR / kind / run_id,
        "logs": LOG_DIR / kind / run_id,
    }


def resolve_existing_run_paths(run_id: str) -> dict[str, Path]:
    """Locate a new-layout run, then a historical normal or retry run."""
    unified = run_paths(run_id)
    if unified["root"].is_dir():
        return unified
    for retry in (False, True):
        legacy = legacy_run_paths(run_id, retry=retry)
        if any(legacy[key].is_dir() for key in ("intermediate", "reports", "logs")):
            return legacy
    raise FileNotFoundError(f"找不到运行ID对应的目录: {run_id}")


def run_directories(run_id: str) -> dict[str, Path]:
    """Create one self-contained data/runs/<run_id> directory."""
    result = run_paths(run_id)
    result["root"].mkdir(parents=True, exist_ok=False)
    for key, path in result.items():
        if key != "root":
            path.mkdir(parents=False, exist_ok=False)
    return result
