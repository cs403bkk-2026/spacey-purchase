import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOB = "".join(p.read_text() for p in sorted((ROOT / "deploy").glob("*.nomad.hcl")))


def test_job_renders_the_token_from_the_runtime_variable():
    assert re.search(
        r"^PURCHASE_TOKEN=\{\{ with nomadVar .+\.purchase_token\b", JOB, re.M
    )


def test_job_stops_on_a_missing_item():
    assert re.search(r"error_on_missing_key\s*=\s*true", JOB)


def test_job_restarts_on_a_failing_health_check():
    assert re.search(r"check_restart\s*\{", JOB)


def test_job_never_turns_on_test_only_settings():
    assert JOB
    assert "PURCHASE_TEST_RESET" not in JOB and "SEED_STARTER_SPACE" not in JOB


def test_service_runs_one_thread_per_worker():
    assert JOB
    assert not re.search(r"^\s*(command|args)\s*=", JOB, re.M)
    assert "--threads" not in JOB and "GUNICORN_CMD_ARGS" not in JOB
    assert "threads" not in (ROOT / "gunicorn.conf.py").read_text()
