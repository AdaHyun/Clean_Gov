import json
from pathlib import Path

from src.batch_submit import run_batch
from src.client import ApiResult, CompanyApiError
from src.reporting import iter_tasks
from src.storage import save_callback

from .helpers import make_settings, minimal_pdf, write_ooxml


class FakeClient:
    def __init__(self, fail_name: str = ""):
        self.fail_name = fail_name
        self.calls = []
        self.request_ids = []

    def submit_file(
        self,
        path: Path,
        callback_url: str,
        request_id: str | None = None,
        validation=None,
    ):
        self.calls.append(path.name)
        self.request_ids.append(request_id)
        if path.name == self.fail_name:
            raise CompanyApiError("simulated", request_id or "", error_type="connection_error")
        payload = {
            "RequestId": request_id,
            "UrlType": 2,
            "FileUrl": "data:application/pdf;base64,AAAA",
            "FileType": path.suffix.lstrip("."),
            "FileName": path.name,
        }
        return ApiResult(request_id or "", {"RequestId": request_id, "Status": 200, "Message": "accepted"}, 200, 0), payload

    @staticmethod
    def payload_summary(payload):
        value = dict(payload)
        value["FileUrl"] = "<BASE64省略>"
        return value


class CallbackBeforeResponseClient(FakeClient):
    def __init__(self, output_root):
        super().__init__()
        self.output_root = output_root

    def submit_file(self, path, callback_url, request_id=None, validation=None):
        save_callback(self.output_root, {
            "RequestId": request_id,
            "Code": 0,
            "Result": {"md_content": "fast callback"},
        })
        return super().submit_file(path, callback_url, request_id, validation)


def test_batch_continues_after_one_file_failure(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "a.pdf").write_bytes(minimal_pdf("a"))
    (input_dir / "b.pdf").write_bytes(minimal_pdf("b"))
    settings = make_settings(tmp_path)
    client = FakeClient("a.pdf")
    summary = run_batch(
        settings, input_dir, callback_url=settings.callback_url,
        recursive=True, interval=0, client=client,
    )
    assert client.calls == ["a.pdf", "b.pdf"]
    assert summary["submit_failed"] == 1
    assert summary["waiting_callback"] == 1
    tasks = list(iter_tasks(settings.output_root, summary["batch_id"]))
    assert {task["status"] for task in tasks} == {"submit_failed", "waiting_callback"}


def test_batch_dry_run_and_recursive_relative_paths(tmp_path):
    input_dir = tmp_path / "input"
    nested = input_dir / "机构" / "栏目"
    nested.mkdir(parents=True)
    write_ooxml(nested / "文件.docx")
    settings = make_settings(tmp_path)
    summary = run_batch(settings, input_dir, recursive=True, dry_run=True, output_layout="mirror")
    assert summary["dry_run"] == 1
    task = next(iter(iter_tasks(settings.output_root, summary["batch_id"])))
    assert task["source_relative_path"] == "机构/栏目/文件.docx"
    assert task["output_layout"] == "mirror"


def test_default_scan_records_unknown_extension_as_unsupported(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "image.tif").write_bytes(b"II-test")
    settings = make_settings(tmp_path)
    summary = run_batch(settings, input_dir, recursive=True, dry_run=True)
    assert summary["total_files"] == 1
    assert summary["unsupported"] == 1
    assert summary["by_extension"][".tif"]["failed"] == 1


def test_submission_json_does_not_store_base64_or_token(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "a.pdf").write_bytes(minimal_pdf("a"))
    settings = make_settings(tmp_path)
    client = FakeClient()
    summary = run_batch(settings, input_dir, callback_url=settings.callback_url, interval=0, client=client)
    task = next(iter(iter_tasks(settings.output_root, summary["batch_id"])))
    submission = json.loads((settings.output_root / "requests" / task["request_id"] / "submission.json").read_text(encoding="utf-8"))
    serialized = json.dumps(submission, ensure_ascii=False)
    assert "AAAA" not in serialized
    assert settings.callback_token not in serialized


def test_success_sha_is_skipped_unless_force(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "a.pdf").write_bytes(minimal_pdf("same"))
    settings = make_settings(tmp_path)

    first_client = FakeClient()
    first = run_batch(settings, input_dir, callback_url=settings.callback_url, interval=0, client=first_client)
    first_task = next(iter(iter_tasks(settings.output_root, first["batch_id"])))
    save_callback(settings.output_root, {
        "RequestId": first_task["request_id"], "Code": 0,
        "Result": {"md_content": "success"},
    })

    skipped_client = FakeClient()
    skipped = run_batch(
        settings,
        input_dir,
        callback_url=settings.callback_url,
        interval=0,
        client=skipped_client,
        output_layout="mirror",
    )
    assert skipped["skipped_duplicate"] == 1
    assert skipped_client.calls == []
    skipped_task = next(iter(iter_tasks(settings.output_root, skipped["batch_id"])))
    duplicate_dir = settings.output_root / "requests" / skipped_task["request_id"]
    assert (duplicate_dir / "content.md").read_text(encoding="utf-8") == "success\n"
    assert (duplicate_dir / "duplicate_reference.json").is_file()
    mirror_dir = settings.output_root / skipped_task["mirror_document_dir"]
    assert (mirror_dir / "content.md").read_text(encoding="utf-8") == "success\n"
    assert (mirror_dir / "duplicate_of_request_id.txt").is_file()

    force_client = FakeClient()
    forced = run_batch(
        settings, input_dir, callback_url=settings.callback_url,
        interval=0, client=force_client, force=True,
    )
    assert forced["waiting_callback"] == 1
    assert force_client.calls == ["a.pdf"]


def test_batch_preflight_blocks_html_before_api_submission(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fake.pdf").write_text(
        "<!DOCTYPE html><html><title>DSpace</title></html>",
        encoding="utf-8",
    )
    settings = make_settings(tmp_path)
    client = FakeClient()
    summary = run_batch(
        settings,
        input_dir,
        callback_url=settings.callback_url,
        interval=0,
        client=client,
    )
    assert client.calls == []
    assert summary["preflight_failed"] == 1
    task = next(iter(iter_tasks(settings.output_root, summary["batch_id"])))
    assert task["status"] == "preflight_failed"
    assert task["error_type"] == "html_disguised_as_document"


def test_max_in_flight_one_waits_for_callback_before_next_submit(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "a.pdf").write_bytes(minimal_pdf("a"))
    (input_dir / "b.pdf").write_bytes(minimal_pdf("b"))
    settings = make_settings(tmp_path)
    client = FakeClient()
    sleep_calls = []

    def release_first_slot(seconds):
        sleep_calls.append(seconds)
        if len(client.request_ids) == 1:
            save_callback(settings.output_root, {
                "RequestId": client.request_ids[0],
                "Code": 0,
                "Result": {"md_content": "first complete"},
            })

    summary = run_batch(
        settings,
        input_dir,
        callback_url=settings.callback_url,
        interval=0,
        max_in_flight=1,
        slot_poll_interval=0.01,
        client=client,
        sleep=release_first_slot,
    )
    assert client.calls == ["a.pdf", "b.pdf"]
    assert sleep_calls
    assert summary["callback_success"] == 1
    assert summary["waiting_callback"] == 1


def test_same_stem_different_extensions_have_distinct_mirror_directories(tmp_path):
    input_dir = tmp_path / "input"
    article = input_dir / "机构" / "栏目" / "文章"
    article.mkdir(parents=True)
    (article / "附件.pdf").write_bytes(minimal_pdf("pdf"))
    write_ooxml(article / "附件.docx")
    settings = make_settings(tmp_path)
    summary = run_batch(
        settings,
        input_dir,
        recursive=True,
        dry_run=True,
        output_layout="mirror",
    )
    assert summary["preflight_failed"] == 0
    assert summary["dry_run"] == 2
    tasks = list(iter_tasks(settings.output_root, summary["batch_id"]))
    assert {task["status"] for task in tasks} == {"dry_run"}


def test_callback_before_submit_response_cannot_regress_success(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fast.pdf").write_bytes(minimal_pdf("fast"))
    settings = make_settings(tmp_path)
    summary = run_batch(
        settings,
        input_dir,
        callback_url=settings.callback_url,
        interval=0,
        client=CallbackBeforeResponseClient(settings.output_root),
    )
    task = next(iter(iter_tasks(settings.output_root, summary["batch_id"])))
    assert task["status"] == "callback_success"
    assert (settings.output_root / "requests" / task["request_id"] / "content.md").is_file()


def test_explicit_max_in_flight_is_saved_with_parser_backend(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "a.pdf").write_bytes(minimal_pdf("a"))
    settings = make_settings(tmp_path, parser_pool_id="h200")
    summary = run_batch(
        settings,
        input_dir,
        callback_url=settings.callback_url,
        interval=0,
        max_in_flight=5,
        client=FakeClient(),
    )
    batch = json.loads(
        (settings.output_root / "batches" / summary["batch_id"] / "batch.json").read_text(
            encoding="utf-8"
        )
    )
    task = next(iter(iter_tasks(settings.output_root, summary["batch_id"])))
    assert batch["max_in_flight"] == batch["batch_max_in_flight"] == 5
    assert batch["parser_pool_id"] == task["parser_pool_id"] == "h200"
    assert batch["parser_api_url"] == task["parser_api_url"] == settings.api_url


def test_sha256_is_reused_for_unchanged_path_size_and_mtime(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "same.pdf").write_bytes(minimal_pdf("same"))
    settings = make_settings(tmp_path)
    run_batch(
        settings, input_dir, callback_url=settings.callback_url,
        interval=0, client=FakeClient(),
    )

    def should_not_hash(path):
        raise AssertionError("unchanged file should reuse cached SHA256")

    monkeypatch.setattr("src.batch_submit.sha256_file", should_not_hash)
    summary = run_batch(
        settings, input_dir, callback_url=settings.callback_url,
        interval=0, client=FakeClient(),
    )
    assert summary["waiting_callback"] == 1
