from data_juicer_runner import ConsoleProgress, parse_data_juicer_progress


def test_parse_data_juicer_tqdm_progress():
    assert parse_data_juicer_progress(
        "fix_unicode_mapper_process:  39%|###8| 3000/7742 [00:04<00:07, 658.60 examples/s]"
    ) == ("fix_unicode_mapper", 3000, 7742)


def test_ignore_non_progress_log_lines():
    assert parse_data_juicer_progress(
        "2026-08-05 16:13:27 | INFO | Processing data with DAG monitoring"
    ) is None


def test_console_progress_finish_prints_duration_and_throughput(capsys):
    progress = ConsoleProgress("Stage 01 | web_normal", expected_total=100)
    progress.start()
    progress.finish(return_code=0, output_count=90, duration_seconds=4.0)

    output = capsys.readouterr().out
    assert "耗时 4.00 秒" in output
    assert "吞吐 25.00 条/秒" in output
