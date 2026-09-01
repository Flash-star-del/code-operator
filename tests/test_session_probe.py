from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from evals import run_session_probe
from evals.run_golden import CommandResult, EvalInfrastructureError
from code_operator.config import ProviderConfig
from code_operator.client import ProviderError, ProviderProtocolError
from code_operator import session as session_module
from code_operator.journal import UndoResult
from code_operator.models import AssistantTurn, ToolCall, Usage
from tests.fakes import FakeModelClient
from evals.run_session_probe import (
    ALLOWED_CHANGED_PATHS,
    AttemptReservation,
    EVAL_ROOT,
    FIXTURE_ROOT,
    FixtureSummary,
    FrozenProbeMetadata,
    O1B_PLANNED_ATTEMPTS,
    O1B_PROTOCOL_VERSION,
    O1B_REPORT_PATHS,
    O1B_RESERVATION_PATHS,
    PROBE_ID,
    TURN1_PATH,
    TURN2_PATH,
    ProbeReport,
    validate_fixture,
)


HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def test_o1b_constants_and_reservation_schema_are_frozen() -> None:
    assert O1B_PROTOCOL_VERSION == "o1b-v1"
    assert O1B_PLANNED_ATTEMPTS == 3
    assert tuple(O1B_REPORT_PATHS) == (1, 2, 3)
    assert tuple(O1B_RESERVATION_PATHS) == (1, 2, 3)
    assert O1B_REPORT_PATHS[1].as_posix() == "docs/evidence/o1b-session-probe-01.json"
    assert O1B_RESERVATION_PATHS[1].as_posix() == (
        "docs/evidence/o1b-session-probe-01.reservation.json"
    )


def test_o1b_reservation_is_created_exclusively_before_run_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class StopAfterRunProbe(RuntimeError):
        pass

    reservation = tmp_path / "o1b-session-probe-01.reservation.json"
    events: list[str] = []

    def stop_after_reservation(**_kwargs: object) -> None:
        assert reservation.is_file()
        events.append("run_probe")
        raise StopAfterRunProbe

    monkeypatch.setattr(run_session_probe, "run_probe", stop_after_reservation)

    with pytest.raises(StopAfterRunProbe):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=1,
            evidence_root=tmp_path,
            _test_evidence_root=tmp_path,
            reservation_path=reservation,
            report_path=tmp_path / "o1b-session-probe-01.json",
            config=_probe_config(),
        )

    assert reservation.is_file()
    assert events == ["run_probe"]
    payload = json.loads(reservation.read_text(encoding="utf-8"))
    assert payload["attempt_index"] == 1
    assert "api_key" not in json.dumps(payload).lower()


def test_o1b_reservation_success_does_not_write_report_and_transmits_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = object()
    events: list[str] = []

    def fake_run_probe(**_kwargs: object) -> object:
        assert (tmp_path / "o1b-session-probe-01.reservation.json").is_file()
        events.append("run_probe")
        return expected

    monkeypatch.setattr(run_session_probe, "run_probe", fake_run_probe)
    report = tmp_path / "o1b-session-probe-01.json"
    result = run_session_probe.reserve_then_run_real_attempt(
        attempt_index=1,
        evidence_root=tmp_path,
        _test_evidence_root=tmp_path,
        reservation_path=tmp_path / "o1b-session-probe-01.reservation.json",
        report_path=report,
        config=_probe_config(),
    )

    assert result is expected
    assert events == ["run_probe"]
    assert not report.exists()


def test_o1b_reservation_transmits_frozen_attempt_metadata_to_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_run_probe(**kwargs: object) -> object:
        observed.update(kwargs)
        return run_session_probe._build_report(
            mode="real",
            outcome="INVALID_INFRA",
            failure_code="PROVIDER_ERROR",
            summary=None,
            attempt_index=kwargs["attempt_index"],  # type: ignore[arg-type]
            frozen_metadata=kwargs["frozen_metadata"],  # type: ignore[arg-type]
            started=0.0,
        )

    monkeypatch.setattr(run_session_probe, "run_probe", fake_run_probe)
    reserve_then_run = run_session_probe.reserve_then_run_real_attempt
    report = reserve_then_run(
        attempt_index=1,
        evidence_root=tmp_path,
        _test_evidence_root=tmp_path,
        reservation_path=tmp_path / "o1b-session-probe-01.reservation.json",
        report_path=tmp_path / "o1b-session-probe-01.json",
        config=_probe_config(),
    )

    metadata = observed["frozen_metadata"]
    assert observed["attempt_index"] == 1
    assert isinstance(metadata, FrozenProbeMetadata)
    reservation_metadata = json.loads(
        (tmp_path / "o1b-session-probe-01.reservation.json").read_text(
            encoding="utf-8"
        )
    )["metadata"]
    assert metadata.to_dict() == reservation_metadata
    assert isinstance(report, ProbeReport)
    assert report.protocol_version == O1B_PROTOCOL_VERSION
    assert report.attempt_index == 1
    assert report.production_tree_sha256 == reservation_metadata["production_tree_sha256"]
    assert report.evaluator_protocol_sha256 == reservation_metadata["evaluator_protocol_sha256"]
    assert report.config == reservation_metadata["config"]
    assert "api_key" not in json.dumps(metadata.to_dict()).lower()


@pytest.mark.parametrize(
    ("provider_error", "failure_code"),
    [
        (ProviderError("synthetic provider failure"), "PROVIDER_ERROR"),
        (
            ProviderProtocolError("synthetic provider protocol failure"),
            "PROVIDER_PROTOCOL_ERROR",
        ),
    ],
)
def test_provider_failure_turn1_is_invalid_infra_before_policy(
    provider_error: Exception,
    failure_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _run_probe_with_client(
        monkeypatch, FakeModelClient([provider_error])
    )

    assert (report.outcome, report.failure_code) == ("INVALID_INFRA", failure_code)
    assert report.turn1_ideal_trace is False
    assert report.turn2_ideal_trace is None
    assert report.turn2_exact_value_observed is None


@pytest.mark.parametrize(
    ("provider_error", "failure_code"),
    [
        (ProviderError("synthetic provider failure"), "PROVIDER_ERROR"),
        (
            ProviderProtocolError("synthetic provider protocol failure"),
            "PROVIDER_PROTOCOL_ERROR",
        ),
    ],
)
def test_provider_failure_turn2_is_invalid_infra_before_exact_and_policy(
    provider_error: Exception,
    failure_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_turns = [
        _turn(None, ToolCall("read", "read_file", '{"path":"greeting.py"}')),
        _turn(None, ToolCall("edit", "edit_file", '{"path":"greeting.py","old_text":"return f\\"Hello, {name}!\\"","new_text":"return f\\"你好，{name}！\\""}')),
        _turn(None, ToolCall("cmd", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
        _turn("first"),
    ]
    first_turns.append(provider_error)  # type: ignore[arg-type]
    report = _run_probe_with_client(monkeypatch, FakeModelClient(first_turns))

    assert (report.outcome, report.failure_code) == ("INVALID_INFRA", failure_code)
    assert report.turn1_ideal_trace is True
    assert report.turn2_ideal_trace is False
    assert report.turn2_exact_value_observed is False


def test_o1b_existing_reservation_is_immutable_and_probe_is_not_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reservation = tmp_path / "o1b-session-probe-01.reservation.json"
    reservation.write_bytes(b'{"existing":true}\n')
    before = reservation.read_bytes()
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **_kwargs: pytest.fail("run_probe must not be called"),
    )

    with pytest.raises(EvalInfrastructureError):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=1,
            evidence_root=tmp_path,
            _test_evidence_root=tmp_path,
            reservation_path=reservation,
            report_path=tmp_path / "o1b-session-probe-01.json",
            config=_probe_config(),
        )
    assert reservation.read_bytes() == before


def test_o1b_attempt_two_requires_attempt_one_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **_kwargs: pytest.fail("run_probe must not be called"),
    )
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=2,
            evidence_root=tmp_path,
            _test_evidence_root=tmp_path,
            reservation_path=tmp_path / "o1b-session-probe-02.reservation.json",
            report_path=tmp_path / "o1b-session-probe-02.json",
            config=_probe_config(),
        )


@pytest.mark.parametrize("which", ["reservation", "report"])
def test_o1b_attempt_and_paths_must_match_fixed_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **_kwargs: pytest.fail("run_probe must not be called"),
    )
    reservation = tmp_path / "o1b-session-probe-01.reservation.json"
    report = tmp_path / "o1b-session-probe-01.json"
    if which == "reservation":
        reservation = tmp_path / "o1b-session-probe-02.reservation.json"
    else:
        report = tmp_path / "o1b-session-probe-02.json"
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=1,
            evidence_root=tmp_path,
            _test_evidence_root=tmp_path,
            reservation_path=reservation,
            report_path=report,
            config=_probe_config(),
        )


def test_o1b_reservation_without_result_cannot_be_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reservation = tmp_path / "o1b-session-probe-01.reservation.json"
    reservation.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **_kwargs: pytest.fail("reserved attempt must not be reused"),
    )
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=1,
            evidence_root=tmp_path,
            _test_evidence_root=tmp_path,
            reservation_path=reservation,
            report_path=tmp_path / "o1b-session-probe-01.json",
            config=_probe_config(),
        )


def test_o1b_manifest_hash_is_sensitive_to_content_and_renames(tmp_path: Path) -> None:
    first = tmp_path / "alpha.py"
    first.write_bytes(b"alpha\n")
    original = run_session_probe._manifest_sha256([first], repository_root=tmp_path)
    first.write_bytes(b"beta\n")
    changed_content = run_session_probe._manifest_sha256(
        [first], repository_root=tmp_path
    )
    renamed = tmp_path / "renamed.py"
    first.rename(renamed)
    changed_name = run_session_probe._manifest_sha256(
        [renamed], repository_root=tmp_path
    )
    assert original != changed_content != changed_name


@pytest.mark.parametrize(
    "invalid_kind",
    ["missing", "directory", "duplicate", "outside", "symlink", "reparse"],
)
def test_o1b_manifest_hash_rejects_invalid_inputs(
    tmp_path: Path, invalid_kind: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    ordinary = root / "ordinary.py"
    ordinary.write_bytes(b"ordinary\n")
    if invalid_kind == "missing":
        candidate = root / "missing.py"
        paths = [candidate]
    elif invalid_kind == "directory":
        candidate = root / "directory"
        candidate.mkdir()
        paths = [candidate]
    elif invalid_kind == "duplicate":
        paths = [ordinary, ordinary]
    elif invalid_kind == "outside":
        candidate = tmp_path / "outside.py"
        candidate.write_bytes(b"outside\n")
        paths = [candidate]
    elif invalid_kind == "symlink":
        candidate = root / "link.py"
        _make_symlink_or_skip(candidate, ordinary, target_is_directory=False)
        paths = [candidate]
    else:
        target = tmp_path / "target-directory"
        target.mkdir()
        candidate = root / "reparse-directory"
        _make_junction_or_skip(candidate, target)
        paths = [candidate]

    with pytest.raises(EvalInfrastructureError):
        run_session_probe._manifest_sha256(paths, repository_root=root)


def test_o1b_production_and_evaluator_hashes_are_sensitive(tmp_path: Path) -> None:
    (tmp_path / "code_operator").mkdir()
    (tmp_path / "code_operator" / "config.py").write_text("config\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "run_session_probe.py").write_text("runner\n", encoding="utf-8")
    (tmp_path / "evals" / "run_golden.py").write_text("golden\n", encoding="utf-8")
    (tmp_path / "evals" / "session_probe").mkdir()
    (tmp_path / "evals" / "session_probe" / "project").mkdir()
    (tmp_path / "evals" / "session_probe" / "project" / "greeting.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "evals" / "session_probe" / "project" / "tests").mkdir()
    (tmp_path / "evals" / "session_probe" / "project" / "tests" / "test_greeting.py").write_text("test\n", encoding="utf-8")
    (tmp_path / "evals" / "session_probe" / "turn1.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "evals" / "session_probe" / "turn2.txt").write_text("two\n", encoding="utf-8")
    (tmp_path / "docs" / "superpowers" / "specs").mkdir(parents=True)
    (tmp_path / "docs" / "superpowers" / "specs" / "2026-09-01-o1b-session-replication-design.md").write_text("spec\n", encoding="utf-8")
    production_members = {
        path.relative_to(tmp_path).as_posix()
        for path in run_session_probe._production_manifest_paths(tmp_path)
    }
    assert production_members == {"code_operator/config.py", "requirements.txt"}
    evaluator_members = {
        path.relative_to(tmp_path).as_posix()
        for path in run_session_probe._evaluator_manifest_paths(tmp_path)
    }
    assert evaluator_members == {
        "evals/run_session_probe.py",
        "evals/run_golden.py",
        "evals/session_probe/project/greeting.py",
        "evals/session_probe/project/tests/test_greeting.py",
        "evals/session_probe/turn1.txt",
        "evals/session_probe/turn2.txt",
        "docs/superpowers/specs/2026-09-01-o1b-session-replication-design.md",
    }
    production = run_session_probe._production_tree_sha256(repository_root=tmp_path)
    for relative in sorted(production_members):
        target = tmp_path / relative
        original = target.read_bytes()
        target.write_bytes(original + b"changed")
        assert run_session_probe._production_tree_sha256(repository_root=tmp_path) != production
        target.write_bytes(original)
    evaluator = run_session_probe._evaluator_protocol_sha256(repository_root=tmp_path)
    for relative in sorted(evaluator_members):
        target = tmp_path / relative
        original = target.read_bytes()
        target.write_bytes(original + b"changed")
        assert run_session_probe._evaluator_protocol_sha256(repository_root=tmp_path) != evaluator
        target.write_bytes(original)


def test_o1b_json_helper_canonicalizes_tuples_before_exclusive_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reservation.json"
    run_session_probe._write_json_exclusive_verified(
        path, {"items": ("one", "two")}
    )
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "items": ["one", "two"]
    }


def test_o1b_json_helper_compares_redacted_bearer_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bearer.json"
    run_session_probe._write_json_exclusive_verified(
        path,
        {"authorization": "Bearer synthetic-token"},
        api_key="synthetic-api-key",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["authorization"] == "Bearer <REDACTED>"
    assert "synthetic-token" not in path.read_text(encoding="utf-8")


def test_o1b_manifest_rejects_identity_change_after_stable_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "ordinary.py"
    target.write_bytes(b"before\n")
    original_read = run_session_probe._read_file_stably

    def mutate_after_read(path: Path) -> bytes:
        content = original_read(path)
        if path == target:
            path.write_bytes(b"after\n")
        return content

    monkeypatch.setattr(run_session_probe, "_read_file_stably", mutate_after_read)
    with pytest.raises(EvalInfrastructureError):
        run_session_probe._manifest_sha256([target], repository_root=tmp_path)


def test_o1b_production_evidence_root_requires_exact_trusted_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    evidence = repository / "docs" / "evidence"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **_kwargs: pytest.fail("run_probe must not be called"),
    )
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=1,
            evidence_root=tmp_path,
            reservation_path=tmp_path / "o1b-session-probe-01.reservation.json",
            report_path=tmp_path / "o1b-session-probe-01.json",
            config=_probe_config(),
        )


def test_o1b_evidence_root_symlink_is_rejected_even_with_private_test_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external-evidence"
    external.mkdir()
    linked = tmp_path / "linked-evidence"
    _make_symlink_or_skip(linked, external, target_is_directory=True)
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **_kwargs: pytest.fail("run_probe must not be called"),
    )
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=1,
            evidence_root=linked,
            _test_evidence_root=linked,
            reservation_path=linked / "o1b-session-probe-01.reservation.json",
            report_path=linked / "o1b-session-probe-01.json",
            config=_probe_config(),
        )


def test_o1b_previous_reservation_schema_and_metadata_are_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _probe_config()
    metadata = run_session_probe._freeze_probe_metadata(config)
    previous = AttemptReservation(
        protocol_version=O1B_PROTOCOL_VERSION,
        attempt_index=1,
        created_at="2026-09-01T00:00:00+00:00",
        metadata=metadata,
    )
    previous_path = tmp_path / "o1b-session-probe-01.reservation.json"
    previous_path.write_text(
        json.dumps(asdict(previous), ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **_kwargs: pytest.fail("run_probe must not be called"),
    )
    damaged = json.loads(previous_path.read_text(encoding="utf-8"))
    damaged["metadata"]["config"]["model"] = "drifted-model"
    previous_path.write_text(json.dumps(damaged), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=2,
            evidence_root=tmp_path,
            _test_evidence_root=tmp_path,
            reservation_path=tmp_path / "o1b-session-probe-02.reservation.json",
            report_path=tmp_path / "o1b-session-probe-02.json",
            config=config,
        )
    assert not (tmp_path / "o1b-session-probe-02.reservation.json").exists()


def test_o1b_evidence_component_identity_drift_blocks_reservation_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    backup = tmp_path / "evidence-backup"
    original_verify = run_session_probe._verify_component_guard
    calls = 0

    def replace_after_initial_check(guard: object) -> None:
        nonlocal calls
        original_verify(guard)  # type: ignore[arg-type]
        calls += 1
        if calls == 1:
            evidence.rename(backup)
            evidence.mkdir()

    monkeypatch.setattr(
        run_session_probe, "_verify_component_guard", replace_after_initial_check
    )
    run_called = False

    def fake_run_probe(**_kwargs: object) -> ProbeReport:
        nonlocal run_called
        run_called = True
        raise AssertionError("run_probe must not be called")

    monkeypatch.setattr(run_session_probe, "run_probe", fake_run_probe)
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=1,
            evidence_root=evidence,
            _test_evidence_root=evidence,
            reservation_path=evidence / "o1b-session-probe-01.reservation.json",
            report_path=evidence / "o1b-session-probe-01.json",
            config=_probe_config(),
        )
    assert run_called is False
    assert not (evidence / "o1b-session-probe-01.reservation.json").exists()


def test_o1b_previous_reservation_rejects_replacement_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _probe_config()
    metadata = run_session_probe._freeze_probe_metadata(config)
    previous = AttemptReservation(
        protocol_version=O1B_PROTOCOL_VERSION,
        attempt_index=1,
        created_at="2026-09-01T00:00:00+00:00",
        metadata=metadata,
    )
    previous_path = tmp_path / "o1b-session-probe-01.reservation.json"
    previous_path.write_text(json.dumps(asdict(previous)), encoding="utf-8")
    original_read = run_session_probe._read_file_stably

    def replace_after_read(path: Path) -> bytes:
        content = original_read(path)
        if path == previous_path:
            path.write_bytes(content + b" ")
        return content

    monkeypatch.setattr(run_session_probe, "_read_file_stably", replace_after_read)
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **_kwargs: pytest.fail("run_probe must not be called"),
    )
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.reserve_then_run_real_attempt(
            attempt_index=2,
            evidence_root=tmp_path,
            _test_evidence_root=tmp_path,
            reservation_path=tmp_path / "o1b-session-probe-02.reservation.json",
            report_path=tmp_path / "o1b-session-probe-02.json",
            config=config,
        )


def test_o1b_config_snapshot_is_complete_but_never_contains_key() -> None:
    config = _probe_config()
    snapshot = run_session_probe._config_snapshot(config)
    encoded = json.dumps(snapshot, ensure_ascii=False).lower()
    assert snapshot["base_url"] == config.base_url
    assert snapshot["model"] == config.model
    assert snapshot["context_window"] == config.context_window
    assert snapshot["max_output_tokens"] == config.max_output_tokens
    assert snapshot["max_model_rounds"] == config.max_model_rounds
    assert snapshot["max_tool_calls"] == config.max_tool_calls
    assert snapshot["http_timeout_seconds"] == {
        "connect": 10.0, "read": 60.0, "write": 30.0, "pool": 10.0,
    }
    assert snapshot["test_command"] == ["python", "-m", "pytest", "-q"]
    assert snapshot["test_timeout_seconds"] == 60
    assert snapshot["ask_all"] is True
    assert snapshot["auto_approve_tests"] is False
    assert "api_key" not in encoded
    assert config.api_key not in encoded


def test_o1b_dataclasses_serialize_nested_metadata_without_key() -> None:
    metadata = FrozenProbeMetadata(
        fixture_sha256="a" * 64,
        prompt_sha256="b" * 64,
        target_initial_sha256="c" * 64,
        production_tree_sha256="d" * 64,
        evaluator_protocol_sha256="e" * 64,
        config={"model": "synthetic"},
    )
    reservation = AttemptReservation(
        protocol_version=O1B_PROTOCOL_VERSION,
        attempt_index=1,
        created_at="2026-09-01T00:00:00+08:00",
        metadata=metadata,
    )
    payload = asdict(reservation)
    assert payload["metadata"]["fixture_sha256"] == "a" * 64
    assert "api_key" not in json.dumps(payload).lower()


def test_probe_identity_and_scope_are_frozen() -> None:
    assert PROBE_ID == "e4-session-probe-2026-09-01"
    assert ALLOWED_CHANGED_PATHS == frozenset({"greeting.py"})


def test_validate_fixture_requires_initial_red_and_exact_prompt_files() -> None:
    summary = validate_fixture()

    assert summary.initial_test_returncode == 1
    assert summary.target_relative_path == "greeting.py"
    assert HEX_SHA256.fullmatch(summary.fixture_sha256)
    assert HEX_SHA256.fullmatch(summary.prompt_sha256)
    assert HEX_SHA256.fullmatch(summary.target_initial_sha256)
    assert TURN1_PATH.read_bytes()
    assert TURN2_PATH.read_bytes()


def test_probe_report_contains_no_raw_prompt_or_absolute_workspace() -> None:
    report = ProbeReport.invalid_infra("offline", "AUTH_NOT_RUN").to_dict()
    serialized = json.dumps(report, ensure_ascii=False)

    assert "workspace" not in report
    assert "prompt" not in report
    assert str(Path.cwd()) not in serialized
    assert json.loads(serialized)["turn_statuses"] == []


def test_fixture_rejects_source_runtime_artifacts(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, fixture)
    (fixture / ".pytest_cache").mkdir()
    (fixture / ".pytest_cache" / "CACHEDIR.TAG").write_text("runtime", encoding="utf-8")

    with pytest.raises(EvalInfrastructureError, match="runtime artifact"):
        validate_fixture(fixture_root=fixture)


def test_prompt_hash_changes_when_one_prompt_byte_changes(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, fixture)
    turn1 = tmp_path / "turn1.txt"
    turn2 = tmp_path / "turn2.txt"
    turn1.write_bytes(TURN1_PATH.read_bytes())
    turn2.write_bytes(TURN2_PATH.read_bytes())

    original = validate_fixture(
        fixture_root=fixture,
        turn1_path=turn1,
        turn2_path=turn2,
    )
    turn2.write_bytes(turn2.read_bytes() + b" ")
    changed = validate_fixture(
        fixture_root=fixture,
        turn1_path=turn1,
        turn2_path=turn2,
    )

    assert original.prompt_sha256 != changed.prompt_sha256


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(None, "", "", True),
        CommandResult(0, "", "", False),
        CommandResult(2, "", "", False),
    ],
)
def test_fixture_rejects_timeout_or_non_one_test_result(
    result: CommandResult,
) -> None:
    def fake_runner(*_args: object, **_kwargs: object) -> CommandResult:
        return result

    with pytest.raises(EvalInfrastructureError):
        validate_fixture(runner=fake_runner)


def test_probe_report_tuple_fields_remain_json_serializable() -> None:
    report = ProbeReport.invalid_infra("offline", "PROCESS_FAILURE").to_dict()

    encoded = json.dumps(report, ensure_ascii=False)

    assert json.loads(encoded)["provider_total_tokens"] == []


def test_fixture_and_prompt_paths_are_frozen() -> None:
    assert FIXTURE_ROOT == EVAL_ROOT / "session_probe" / "project"
    assert TURN1_PATH == EVAL_ROOT / "session_probe" / "turn1.txt"
    assert TURN2_PATH == EVAL_ROOT / "session_probe" / "turn2.txt"


def test_fixture_and_prompts_match_approved_bytes() -> None:
    assert (FIXTURE_ROOT / "greeting.py").read_bytes() == (
        "def greeting(name: str) -> str:\n"
        "    return f\"Hello, {name}!\"\n"
    ).encode("utf-8")
    assert (FIXTURE_ROOT / "tests" / "test_greeting.py").read_bytes() == (
        "from greeting import greeting\n"
        "\n"
        "\n"
        "def test_greeting_uses_chinese_salutation() -> None:\n"
        "    assert greeting(\"小明\") == \"你好，小明！\"\n"
    ).encode("utf-8")
    assert TURN1_PATH.read_bytes() == (
        "请修复 greeting.py，使现有测试通过。只允许修改 greeting.py；先读取文件，再进行一次直接编辑，并运行现有测试确认结果。\n"
    ).encode("utf-8")
    assert TURN2_PATH.read_bytes() == (
        "基于刚才完成的修改，说明现在 greeting(\"小明\") 的精确返回值，并运行同一个测试再次确认；不要修改任何文件。\n"
    ).encode("utf-8")


@pytest.mark.parametrize("missing", ["fixture_root", "greeting", "test", "turn1", "turn2"])
def test_missing_fixture_inputs_are_rejected_without_path_leaks(
    tmp_path: Path, missing: str
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, fixture)
    turn1 = tmp_path / "turn1.txt"
    turn2 = tmp_path / "turn2.txt"
    turn1.write_bytes(TURN1_PATH.read_bytes())
    turn2.write_bytes(TURN2_PATH.read_bytes())
    kwargs: dict[str, Path] = {
        "fixture_root": fixture,
        "turn1_path": turn1,
        "turn2_path": turn2,
    }
    if missing == "fixture_root":
        shutil.rmtree(fixture)
    elif missing == "greeting":
        (fixture / "greeting.py").unlink()
    elif missing == "test":
        (fixture / "tests" / "test_greeting.py").unlink()
    elif missing == "turn1":
        turn1.unlink()
    else:
        turn2.unlink()

    with pytest.raises(EvalInfrastructureError) as error:
        validate_fixture(**kwargs)

    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("extra", ["file", "directory"])
def test_fixture_rejects_any_extra_source_tree_entry(
    tmp_path: Path, extra: str
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, fixture)
    if extra == "file":
        (fixture / "unexpected.txt").write_text("synthetic", encoding="utf-8")
    else:
        (fixture / "unexpected-empty-directory").mkdir()

    with pytest.raises(EvalInfrastructureError, match="fixture tree") as error:
        validate_fixture(fixture_root=fixture)

    assert str(tmp_path) not in str(error.value)


def _make_symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (PermissionError, OSError) as error:
        pytest.skip(f"OS does not permit synthetic symlink creation: {error}")


def _make_junction_or_skip(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("NTFS junction test requires Windows")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        shell=False,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"NTFS junction unavailable (mklink returncode={result.returncode})")
    assert link.is_dir()


def test_fixture_rejects_ntfs_junction_without_descending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, fixture)
    outside_tests = tmp_path / "outside-tests"
    shutil.copytree(fixture / "tests", outside_tests)
    shutil.rmtree(fixture / "tests")
    _make_junction_or_skip(fixture / "tests", outside_tests)
    original_scandir = run_session_probe.os.scandir
    scanned_outside = False

    def guarded_scandir(path: object) -> object:
        nonlocal scanned_outside
        if Path(path) == outside_tests:
            scanned_outside = True
            raise AssertionError("fixture validation descended into junction target")
        return original_scandir(path)

    monkeypatch.setattr(run_session_probe.os, "scandir", guarded_scandir)

    with pytest.raises(EvalInfrastructureError, match="symlink") as error:
        validate_fixture(fixture_root=fixture)

    assert str(tmp_path) not in str(error.value)
    assert scanned_outside is False


def test_fixture_rejects_symlinked_root(tmp_path: Path) -> None:
    external = tmp_path / "external-project"
    shutil.copytree(FIXTURE_ROOT, external)
    linked_root = tmp_path / "fixture-link"
    _make_symlink_or_skip(linked_root, external, target_is_directory=True)

    with pytest.raises(EvalInfrastructureError, match="symlink") as error:
        validate_fixture(fixture_root=linked_root)

    assert str(tmp_path) not in str(error.value)


def test_fixture_rejects_descendant_symlink(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, fixture)
    external = tmp_path / "external-source.txt"
    external.write_text("synthetic external content", encoding="utf-8")
    linked_file = fixture / "unexpected-link.txt"
    _make_symlink_or_skip(linked_file, external, target_is_directory=False)

    with pytest.raises(EvalInfrastructureError, match="symlink") as error:
        validate_fixture(fixture_root=fixture)

    assert str(tmp_path) not in str(error.value)


def test_runner_exception_is_fixed_and_redacted(tmp_path: Path) -> None:
    def fake_runner(*_args: object, **_kwargs: object) -> CommandResult:
        raise RuntimeError(
            "prompt片段 Authorization: Bearer synthetic-secret-value " + str(tmp_path)
        )

    with pytest.raises(EvalInfrastructureError) as error:
        validate_fixture(runner=fake_runner)

    assert str(error.value) == "Session 探针测试进程执行失败"
    assert error.value.__cause__ is None
    assert "prompt片段" not in str(error.value)
    assert "synthetic-secret-value" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_runner_eval_infrastructure_error_is_also_fixed_and_redacted(
    tmp_path: Path,
) -> None:
    sensitive = (
        "prompt片段 Authorization: Bearer synthetic-secret-value " + str(tmp_path)
    )

    def fake_runner(*_args: object, **_kwargs: object) -> CommandResult:
        raise EvalInfrastructureError(sensitive)

    with pytest.raises(EvalInfrastructureError) as error:
        validate_fixture(runner=fake_runner)

    assert str(error.value) == "Session 探针测试进程执行失败"
    assert error.value.__cause__ is None
    assert sensitive not in str(error.value)


def test_fixture_copy_oserror_is_fixed_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sensitive = "prompt片段 Authorization: Bearer synthetic-secret-value " + str(tmp_path)

    def failing_copytree(*_args: object, **_kwargs: object) -> None:
        raise OSError(sensitive)

    monkeypatch.setattr(run_session_probe.shutil, "copytree", failing_copytree)

    with pytest.raises(EvalInfrastructureError) as error:
        validate_fixture()

    assert str(error.value) == "Session 探针 fixture 读取失败"
    assert error.value.__cause__ is None
    assert "prompt片段" not in str(error.value)
    assert "synthetic-secret-value" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def _probe_config() -> ProviderConfig:
    return ProviderConfig(
        api_key="synthetic-probe-key",
        base_url="https://probe.invalid/v1",
        model="synthetic-probe-model",
    )


def _turn(
    content: str | None,
    *calls: ToolCall,
    tokens: int = 7,
) -> AssistantTurn:
    return AssistantTurn(
        content=content,
        tool_calls=list(calls),
        usage=Usage(3, 4, tokens),
    )


def test_run_probe_exercises_two_turn_session_and_restores_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces: list[Path] = []
    original_copytree = run_session_probe.shutil.copytree

    def observing_copytree(source: Path, destination: Path, *args: object, **kwargs: object) -> Path:
        workspaces.append(destination)
        return original_copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(run_session_probe.shutil, "copytree", observing_copytree)
    client = FakeModelClient(
        [
            _turn(None, ToolCall("call-1", "read_file", '{"path":"greeting.py"}')),
            _turn(
                None,
                ToolCall(
                    "call-2",
                    "edit_file",
                    '{"path":"greeting.py","old_text":"return f\\\"Hello, {name}!\\\"","new_text":"return f\\\"你好，{name}！\\\""}',
                ),
            ),
            _turn(None, ToolCall("call-3", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("fixed"),
            _turn(None, ToolCall("call-4", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("验证通过，结果是你好，小明！"),
        ]
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"),
        config=_probe_config(),
        client=client,
        mode="offline",
    )

    assert report.outcome == "PASS"
    assert report.turn_statuses == ("COMPLETED", "COMPLETED")
    assert report.changed_paths_after_turn1 == ("greeting.py",)
    assert report.tests_after_turn1_returncode == 0
    assert report.tests_after_turn2_returncode == 0
    assert report.target_after_turn1_sha256 != report.target_initial_sha256
    assert report.undo_ok is True
    assert report.target_after_undo_sha256 == report.target_initial_sha256
    assert report.undo_depth_after == 0
    assert report.reset_undo_depth == 0
    assert report.reset_pending_events == 0
    assert report.reset_history_message_count == 1
    assert report.reset_read_hash_count == 0
    assert report.close_idempotent is True
    assert report.owned_client_close_calls is None
    assert report.baseline_direct_subprocess_count == 0
    assert report.new_residual_direct_subprocess_count == 0
    assert report.session_artifact_count == 0
    assert report.model_rounds == (4, 2)
    assert report.tool_calls == (3, 1)
    assert report.provider_total_tokens == (28, 14)
    assert report.elapsed_seconds >= 0
    assert workspaces and not workspaces[0].parent.exists()

    second_turn_first_request = client.calls[4][0]
    assert [message["role"] for message in second_turn_first_request] == [
        "system", "user", "assistant", "tool", "assistant", "tool", "assistant", "tool", "assistant", "user"
    ]
    assert [message["content"] for message in second_turn_first_request if message["role"] == "user"] == [
        TURN1_PATH.read_text(encoding="utf-8"),
        TURN2_PATH.read_text(encoding="utf-8"),
    ]
    assistant_ids = [
        call["id"]
        for message in second_turn_first_request
        if message["role"] == "assistant" and "tool_calls" in message
        for call in message["tool_calls"]
    ]
    tool_ids = [
        str(message["tool_call_id"])
        for message in second_turn_first_request
        if message["role"] == "tool"
    ]
    assert assistant_ids == tool_ids == ["call-1", "call-2", "call-3"]


def test_probe_reports_reset_history_and_process_observability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    monkeypatch.setattr(run_session_probe, "_direct_subprocess_pids", lambda: frozenset(), raising=False)

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    assert report.reset_history_message_count == 1
    assert report.reset_read_hash_count == 0
    assert report.reset_undo_depth == 0
    assert report.reset_pending_events == 0
    assert report.owned_client_close_calls is None
    assert report.baseline_direct_subprocess_count == 0
    assert report.new_residual_direct_subprocess_count == 0


def test_probe_fails_closed_when_subprocess_scan_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())

    def unavailable() -> int:
        raise OSError("synthetic scanner failure")

    monkeypatch.setattr(run_session_probe, "_direct_subprocess_pids", unavailable, raising=False)
    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    assert (report.outcome, report.failure_code) == ("INVALID_INFRA", "SUBPROCESS_SCAN_FAILED")


def test_preexisting_controller_child_is_not_counted_as_new_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter((frozenset({41}), frozenset({41})))
    monkeypatch.setattr(
        run_session_probe,
        "_direct_subprocess_pids",
        lambda: next(snapshots),
        raising=False,
    )
    report = _run_probe_with_client(monkeypatch, _successful_probe_client())

    assert report.baseline_direct_subprocess_count == 1
    assert report.new_residual_direct_subprocess_count == 0
    assert report.outcome == "PASS"


def test_new_direct_subprocess_is_a_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter((frozenset({41}), frozenset({41, 42})))
    monkeypatch.setattr(
        run_session_probe,
        "_direct_subprocess_pids",
        lambda: next(snapshots),
        raising=False,
    )
    report = _run_probe_with_client(monkeypatch, _successful_probe_client())

    assert report.baseline_direct_subprocess_count == 1
    assert report.new_residual_direct_subprocess_count == 1
    assert (report.outcome, report.failure_code) == (
        "FAIL",
        "NEW_RESIDUAL_DIRECT_SUBPROCESS",
    )


def test_baseline_subprocess_scan_failure_prevents_session_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_session_probe,
        "_direct_subprocess_pids",
        lambda: (_ for _ in ()).throw(OSError("synthetic scanner failure")),
        raising=False,
    )
    monkeypatch.setattr(
        run_session_probe,
        "AgentSession",
        lambda *_args, **_kwargs: pytest.fail("AgentSession must not be created"),
    )

    report = _run_probe_with_client(monkeypatch, _successful_probe_client())

    assert (report.outcome, report.failure_code) == (
        "INVALID_INFRA",
        "SUBPROCESS_SCAN_FAILED",
    )
    assert report.baseline_direct_subprocess_count is None
    assert report.new_residual_direct_subprocess_count is None


def test_final_subprocess_scan_failure_is_invalid_infra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter((frozenset(),))

    def scan() -> frozenset[int]:
        try:
            return next(snapshots)
        except StopIteration:
            raise OSError("synthetic final scanner failure")

    monkeypatch.setattr(run_session_probe, "_direct_subprocess_pids", scan, raising=False)
    report = _run_probe_with_client(monkeypatch, _successful_probe_client())

    assert (report.outcome, report.failure_code) == (
        "INVALID_INFRA",
        "SUBPROCESS_SCAN_FAILED",
    )
    assert report.baseline_direct_subprocess_count == 0
    assert report.new_residual_direct_subprocess_count is None


def test_subprocess_report_contains_counts_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter((frozenset({41}), frozenset({41, 42})))
    monkeypatch.setattr(
        run_session_probe,
        "_direct_subprocess_pids",
        lambda: next(snapshots),
        raising=False,
    )
    report = _run_probe_with_client(monkeypatch, _successful_probe_client())
    serialized = json.dumps(report.to_dict(), ensure_ascii=False)

    assert "active_subprocess_count" not in report.to_dict()
    assert "baseline_direct_subprocess_count" in report.to_dict()
    assert "new_residual_direct_subprocess_count" in report.to_dict()
    assert "41" not in serialized and "42" not in serialized
    assert "pid" not in serialized.lower()
    assert "commandline" not in serialized.lower()


def test_final_subprocess_scan_occurs_after_both_close_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    snapshots = iter((frozenset(), frozenset()))
    original_close = run_session_probe.AgentSession.close

    def scan() -> frozenset[int]:
        events.append("scan")
        return next(snapshots)

    def close(session: object) -> None:
        events.append("close")
        original_close(session)  # type: ignore[arg-type]

    monkeypatch.setattr(run_session_probe, "_direct_subprocess_pids", scan)
    monkeypatch.setattr(run_session_probe.AgentSession, "close", close)
    report = _run_probe_with_client(monkeypatch, _successful_probe_client())

    assert report.outcome == "PASS"
    assert events[:4] == ["scan", "close", "close", "scan"]
    assert events[4:] == ["close"]


def test_provider_invalid_infra_is_not_reclassified_by_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter((frozenset(), frozenset({42})))
    monkeypatch.setattr(
        run_session_probe,
        "_direct_subprocess_pids",
        lambda: next(snapshots),
    )
    report = _run_probe_with_client(
        monkeypatch,
        FakeModelClient([ProviderError("synthetic provider failure")]),
    )

    assert (report.outcome, report.failure_code) == (
        "INVALID_INFRA",
        "PROVIDER_ERROR",
    )
    assert report.baseline_direct_subprocess_count == 0
    assert report.new_residual_direct_subprocess_count == 1


def test_probe_fails_closed_when_reset_internal_state_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    monkeypatch.setattr(run_session_probe, "_direct_subprocess_pids", lambda: frozenset(), raising=False)
    original_reset = run_session_probe.AgentSession.reset

    def reset_without_loop(session: object) -> None:
        original_reset(session)  # type: ignore[arg-type]
        del session._loop  # type: ignore[attr-defined]

    monkeypatch.setattr(run_session_probe.AgentSession, "reset", reset_without_loop)
    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    assert (report.outcome, report.failure_code) == (
        "INVALID_INFRA",
        "RESET_OBSERVABILITY_UNAVAILABLE",
    )


def test_real_mode_owned_client_close_is_observed_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    class OwnedFake:
        def __init__(self, _config: ProviderConfig) -> None:
            self._delegate = _successful_probe_client()
            self.close_calls = 0
            created.append(self)

        def complete(
            self, messages: object, tools: object
        ) -> AssistantTurn:
            return self._delegate.complete(messages, tools)  # type: ignore[arg-type]

        def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    monkeypatch.setattr(run_session_probe, "_direct_subprocess_pids", lambda: frozenset(), raising=False)
    monkeypatch.setattr(session_module, "ModelClient", OwnedFake)
    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=None, mode="real",
    )

    assert report.outcome == "PASS"
    assert report.owned_client_close_calls == 1
    assert len(created) == 1 and created[0].close_calls == 1  # type: ignore[attr-defined]


def test_real_mode_early_failure_reports_owned_client_final_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    class OwnedFake:
        def __init__(self, _config: ProviderConfig) -> None:
            self._delegate = FakeModelClient([_turn(None)])
            self.close_calls = 0
            created.append(self)

        def complete(self, messages: object, tools: object) -> AssistantTurn:
            return self._delegate.complete(messages, tools)  # type: ignore[arg-type]

        def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    monkeypatch.setattr(run_session_probe, "_direct_subprocess_pids", lambda: frozenset(), raising=False)
    monkeypatch.setattr(session_module, "ModelClient", OwnedFake)

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"),
        config=_probe_config(),
        client=None,
        mode="real",
    )

    assert (report.outcome, report.failure_code) == ("FAIL", "TURN1_NOT_COMPLETED")
    assert report.close_idempotent is True
    assert report.owned_client_close_calls == 1
    assert report.baseline_direct_subprocess_count == 0
    assert report.new_residual_direct_subprocess_count == 0
    assert len(created) == 1 and created[0].close_calls == 1  # type: ignore[attr-defined]


def test_real_mode_owned_close_failure_is_counted_and_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    class OwnedFake:
        def __init__(self, _config: ProviderConfig) -> None:
            self._delegate = _successful_probe_client()
            self.close_calls = 0
            created.append(self)

        def complete(self, messages: object, tools: object) -> AssistantTurn:
            return self._delegate.complete(messages, tools)  # type: ignore[arg-type]

        def close(self) -> None:
            self.close_calls += 1
            raise OSError("Bearer synthetic-close-secret")

    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    monkeypatch.setattr(run_session_probe, "_direct_subprocess_pids", lambda: frozenset(), raising=False)
    monkeypatch.setattr(session_module, "ModelClient", OwnedFake)

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"),
        config=_probe_config(),
        client=None,
        mode="real",
    )

    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert (report.outcome, report.failure_code) == ("INVALID_INFRA", "PROBE_RUNTIME_ERROR")
    assert report.close_idempotent is False
    assert report.owned_client_close_calls == 1
    assert len(created) == 1 and created[0].close_calls == 1  # type: ignore[attr-defined]
    assert "synthetic-close-secret" not in serialized


def test_owned_client_constructor_failure_remains_unobservable_but_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    class OwnedFake:
        def __init__(self, _config: ProviderConfig) -> None:
            self.close_calls = 0
            created.append(self)

        def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    monkeypatch.setattr(session_module, "ModelClient", OwnedFake)
    monkeypatch.setattr(
        session_module,
        "JsonlAudit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("Bearer constructor-secret")),
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"),
        config=_probe_config(),
        client=None,
        mode="real",
    )

    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert (report.outcome, report.failure_code) == ("INVALID_INFRA", "PROBE_RUNTIME_ERROR")
    assert report.owned_client_close_calls is None
    assert len(created) == 1 and created[0].close_calls == 1  # type: ignore[attr-defined]
    assert "constructor-secret" not in serialized


def test_probe_requires_first_turn_read_edit_pytest_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    client = FakeModelClient(
        [
            _turn(None, ToolCall("read", "read_file", '{"path":"greeting.py"}')),
            _turn(
                None,
                ToolCall(
                    "write",
                    "write_file",
                    '{"path":"greeting.py","content":"def greeting(name: str) -> str:\\n    return f\\\"你好，{name}！\\\"\\n"}',
                ),
            ),
            _turn(None, ToolCall("pytest-1", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("精确返回值：你好，小明！"),
            _turn(None, ToolCall("pytest-2", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("精确返回值：你好，小明！"),
        ]
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=client, mode="offline",
    )

    assert (report.outcome, report.failure_code) == ("FAIL", "PRIMARY_TOOL_POLICY_FAILED")


@pytest.mark.parametrize(
    ("extra_name", "extra_arguments", "position"),
    [
        ("list_dir", '{"path":"."}', 0),
        ("grep", '{"query":"greeting","path":"."}', 1),
        ("list_dir", '{"path":"."}', 2),
    ],
)
def test_probe_rejects_any_extra_first_turn_trace_event(
    extra_name: str,
    extra_arguments: str,
    position: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    first = [
        ToolCall("read", "read_file", '{"path":"greeting.py"}'),
        ToolCall(
            "edit", "edit_file",
            '{"path":"greeting.py","old_text":"return f\\\"Hello, {name}!\\\"","new_text":"return f\\\"你好，{name}！\\\""}',
        ),
        ToolCall("pytest-1", "run_command", '{"argv":["python","-m","pytest","-q"]}'),
    ]
    first.insert(position, ToolCall("extra", extra_name, extra_arguments))
    client = FakeModelClient(
        [*[_turn(None, call) for call in first], _turn("done"),
         _turn(None, ToolCall("pytest-2", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
         _turn("精确返回值：你好，小明！")]
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=client, mode="offline",
    )

    assert report.outcome == "PASS"
    assert report.turn1_ideal_trace is False
    assert report.turn2_ideal_trace is True
    assert report.ideal_trace_overall is False


@pytest.mark.parametrize(
    ("extra_name", "extra_arguments"),
    [
        ("read_file", '{"path":"greeting.py"}'),
        ("list_dir", '{"path":"."}'),
        ("grep", '{"query":"greeting","path":"."}'),
    ],
)
def test_probe_rejects_any_extra_second_turn_trace_event(
    extra_name: str,
    extra_arguments: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    client = FakeModelClient(
        [
            _turn(None, ToolCall("read", "read_file", '{"path":"greeting.py"}')),
            _turn(None, ToolCall("edit", "edit_file", '{"path":"greeting.py","old_text":"return f\\\"Hello, {name}!\\\"","new_text":"return f\\\"你好，{name}！\\\""}')),
            _turn(None, ToolCall("pytest-1", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("精确返回值：你好，小明！"),
            _turn(None, ToolCall("extra", extra_name, extra_arguments)),
            _turn(None, ToolCall("pytest-2", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("精确返回值：你好，小明！"),
        ]
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=client, mode="offline",
    )

    assert report.outcome == "PASS"
    assert report.turn1_ideal_trace is True
    assert report.turn2_ideal_trace is False
    assert report.ideal_trace_overall is False


@pytest.mark.parametrize(
    ("next_error", "close_ok"),
    [
        (5, True),
        (18, False),
    ],
)
def test_windows_toolhelp_enumeration_and_close_fail_closed(
    next_error: int,
    close_ok: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        pytest.skip("Toolhelp behavior applies only on Windows")
    import ctypes

    class NativeCall:
        def __init__(self, result: object) -> None:
            self.result = result

        def __call__(self, *_args: object) -> object:
            return self.result

    class Kernel32:
        def __init__(self) -> None:
            self.CreateToolhelp32Snapshot = NativeCall(1)
            self.Process32FirstW = NativeCall(True)
            self.Process32NextW = NativeCall(False)
            self.CloseHandle = NativeCall(close_ok)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel32())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: next_error)

    with pytest.raises(OSError) as error:
        run_session_probe._direct_subprocess_pids()

    assert "PID" not in str(error.value)


def _probe_summary() -> FixtureSummary:
    return FixtureSummary(
        fixture_sha256="a" * 64,
        prompt_sha256="b" * 64,
        initial_test_returncode=1,
        target_relative_path="greeting.py",
        target_initial_sha256=run_session_probe._sha256_file(FIXTURE_ROOT / "greeting.py"),
    )


def _offline_frozen_metadata() -> FrozenProbeMetadata:
    return FrozenProbeMetadata(
        fixture_sha256="a" * 64,
        prompt_sha256="b" * 64,
        target_initial_sha256="c" * 64,
        production_tree_sha256="d" * 64,
        evaluator_protocol_sha256="e" * 64,
        config={
            "base_url": "https://probe.invalid/v1",
            "model": "synthetic-probe-model",
            "context_window": 32_000,
            "max_output_tokens": 8_000,
            "max_model_rounds": 16,
            "max_tool_calls": 32,
            "test_command": ["python", "-m", "pytest", "-q"],
            "test_timeout_seconds": 60,
            "ask_all": True,
            "auto_approve_tests": False,
            "python_implementation": "CPython",
            "python_version": "3.11.0",
            "platform": "test-platform",
            "httpx_version": "test-httpx",
            "pytest_version": "test-pytest",
        },
    )


def _offline_reservation(*, attempt_index: int) -> AttemptReservation:
    return AttemptReservation(
        protocol_version=O1B_PROTOCOL_VERSION,
        attempt_index=attempt_index,
        created_at="2026-09-01T00:00:00+08:00",
        metadata=_offline_frozen_metadata(),
    )


def _run_probe_with_client(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeModelClient,
) -> ProbeReport:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    return run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"),
        config=_probe_config(),
        client=client,
        mode="offline",
        attempt_index=1,
        frozen_metadata=_offline_frozen_metadata(),
    )


def _successful_probe_client(*, second_call: ToolCall | None = None) -> FakeModelClient:
    second_prefix = [] if second_call is None else [_turn(None, second_call)]
    return FakeModelClient(
        [
            _turn(None, ToolCall("one", "read_file", '{"path":"greeting.py"}')),
            _turn(
                None,
                ToolCall(
                    "two", "edit_file",
                    '{"path":"greeting.py","old_text":"return f\\\"Hello, {name}!\\\"","new_text":"return f\\\"你好，{name}！\\\""}',
                ),
            ),
            _turn(None, ToolCall("three", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("done"),
            *second_prefix,
            _turn(None, ToolCall("four", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("已确认返回值：你好，小明！"),
        ]
    )


def test_extra_successful_reads_preserve_primary_pass_but_miss_ideal_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    client = FakeModelClient(
        [
            _turn(None, ToolCall("ls", "list_dir", '{"path":"."}')),
            _turn(None, ToolCall("read", "read_file", '{"path":"greeting.py"}')),
            _turn(None, ToolCall("grep", "grep", '{"query":"Hello","path":"greeting.py"}')),
            _turn(
                None,
                ToolCall(
                    "edit", "edit_file",
                    '{"path":"greeting.py","old_text":"return f\\"Hello, {name}!\\"","new_text":"return f\\"你好，{name}！\\""}',
                ),
            ),
            _turn(None, ToolCall("test1", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("fixed"),
            _turn(None, ToolCall("reread", "read_file", '{"path":"greeting.py"}')),
            _turn(None, ToolCall("test2", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
            _turn("精确返回值是“你好，小明！”。"),
        ]
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"),
        config=_probe_config(),
        client=client,
        mode="offline",
    )

    assert report.outcome == "PASS"
    assert report.turn1_ideal_trace is False
    assert report.turn2_ideal_trace is False
    assert report.ideal_trace_overall is False
    assert report.turn2_exact_value_observed is True


def test_probe_report_schema_v2_carries_only_frozen_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _run_probe_with_client(monkeypatch, _successful_probe_client())

    assert report.schema_version == 2
    assert report.protocol_version == O1B_PROTOCOL_VERSION
    assert report.attempt_index == 1
    assert report.production_tree_sha256 == "d" * 64
    assert report.evaluator_protocol_sha256 == "e" * 64
    assert report.config == _offline_frozen_metadata().config
    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert "synthetic-probe-key" not in serialized
    assert "你好，小明！" not in serialized
    assert TURN1_PATH.read_text(encoding="utf-8") not in serialized
    assert "arguments_raw" not in serialized


@pytest.mark.parametrize(
    "final_text",
    ["你好，小明。", "你好， 小明！", "你好，\u3000小明！", "没有精确值"],
)
def test_second_turn_exact_value_uses_raw_substring_only(
    final_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _successful_probe_client()
    client._turns[-1] = _turn(final_text)  # type: ignore[attr-defined]

    report = _run_probe_with_client(monkeypatch, client)

    assert (report.outcome, report.failure_code) == (
        "FAIL",
        "TURN2_EXACT_VALUE_MISSING",
    )
    assert report.turn2_exact_value_observed is False


def test_exact_value_in_tool_payload_does_not_count_as_final_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _successful_probe_client(
        second_call=ToolCall(
            "payload", "grep", '{"query":"你好，小明！","path":"greeting.py"}'
        )
    )
    client._turns[-1] = _turn("工具参数有值，但回答没有")  # type: ignore[attr-defined]

    report = _run_probe_with_client(monkeypatch, client)

    assert (report.outcome, report.failure_code) == (
        "FAIL",
        "TURN2_EXACT_VALUE_MISSING",
    )
    assert report.turn2_exact_value_observed is False


def test_unexecuted_second_turn_keeps_ideal_and_exact_fields_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _run_probe_with_client(monkeypatch, FakeModelClient([_turn(None)]))

    assert report.failure_code == "TURN1_NOT_COMPLETED"
    assert report.turn1_ideal_trace is False
    assert report.turn2_ideal_trace is None
    assert report.ideal_trace_overall is None
    assert report.turn2_exact_value_observed is None


@pytest.mark.parametrize(
    "first_calls",
    [
        [
            ToolCall("ls", "list_dir", '{"path":"."}'),
            ToolCall("edit", "edit_file", '{"path":"greeting.py","old_text":"return f\\"Hello, {name}!\\"","new_text":"return f\\"你好，{name}！\\""}'),
            ToolCall("cmd", "run_command", '{"argv":["python","-m","pytest","-q"]}'),
        ],
        [
            ToolCall("read", "read_file", '{"path":"greeting.py"}'),
            ToolCall("edit1", "edit_file", '{"path":"greeting.py","old_text":"return f\\"Hello, {name}!\\"","new_text":"return f\\"你好，{name}！\\""}'),
            ToolCall("edit2", "edit_file", '{"path":"greeting.py","old_text":"return f\\"Hello, {name}!\\"","new_text":"return f\\"你好，{name}！\\""}'),
            ToolCall("cmd", "run_command", '{"argv":["python","-m","pytest","-q"]}'),
        ],
        [
            ToolCall("read", "read_file", '{"path":"greeting.py"}'),
            ToolCall("write", "write_file", '{"path":"greeting.py","content":"bad"}'),
            ToolCall("cmd", "run_command", '{"argv":["python","-m","pytest","-q"]}'),
        ],
        [
            ToolCall("read", "read_file", '{"path":"greeting.py"}'),
            ToolCall("edit", "edit_file", '{"path":"greeting.py","old_text":"return f\\"Hello, {name}!\\"","new_text":"return f\\"你好，{name}！\\""}'),
            ToolCall("cmd1", "run_command", '{"argv":["python","-m","pytest","-q"]}'),
            ToolCall("cmd2", "run_command", '{"argv":["python","-m","pytest","-q"]}'),
        ],
        [
            ToolCall("edit", "edit_file", '{"path":"greeting.py","old_text":"return f\\"Hello, {name}!\\"","new_text":"return f\\"你好，{name}！\\""}'),
            ToolCall("read", "read_file", '{"path":"greeting.py"}'),
            ToolCall("cmd", "run_command", '{"argv":["python","-m","pytest","-q"]}'),
        ],
    ],
)
def test_first_primary_tool_policy_rejects_structural_violations(
    first_calls: list[ToolCall],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [_turn(None, call) for call in first_calls] + [_turn("ignored")]
    report = _run_probe_with_client(monkeypatch, FakeModelClient(turns))

    assert (report.outcome, report.failure_code) == (
        "FAIL",
        "PRIMARY_TOOL_POLICY_FAILED",
    )
    assert report.turn2_ideal_trace is None


@pytest.mark.parametrize(
    "second_calls",
    [
        [ToolCall("write", "write_file", '{"path":"extra.txt","content":"bad"}'), ToolCall("cmd", "run_command", '{"argv":["python","-m","pytest","-q"]}')],
        [ToolCall("cmd1", "run_command", '{"argv":["python","-m","pytest","-q"]}'), ToolCall("cmd2", "run_command", '{"argv":["python","-m","pytest","-q"]}')],
        [ToolCall("missing", "read_file", '{"path":"missing.txt"}'), ToolCall("cmd", "run_command", '{"argv":["python","-m","pytest","-q"]}')],
        [ToolCall("unknown", "unknown_tool", '{}'), ToolCall("cmd", "run_command", '{"argv":["python","-m","pytest","-q"]}')],
    ],
)
def test_second_primary_tool_policy_rejects_writes_failures_and_unknown_tools(
    second_calls: list[ToolCall],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _turn(None, ToolCall("read", "read_file", '{"path":"greeting.py"}')),
        _turn(None, ToolCall("edit", "edit_file", '{"path":"greeting.py","old_text":"return f\\"Hello, {name}!\\"","new_text":"return f\\"你好，{name}！\\""}')),
        _turn(None, ToolCall("cmd", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
        _turn("first"),
    ]
    turns.extend(_turn(None, call) for call in second_calls)
    turns.append(_turn("精确返回值：你好，小明！"))
    report = _run_probe_with_client(monkeypatch, FakeModelClient(turns))

    assert (report.outcome, report.failure_code) == (
        "FAIL",
        "PRIMARY_TOOL_POLICY_FAILED",
    )
    assert report.turn2_ideal_trace is False


def test_run_probe_rejects_non_frozen_prompt_without_echoing_it() -> None:
    supplied = "prompt片段 Authorization: Bearer synthetic-secret-value"

    with pytest.raises(EvalInfrastructureError) as error:
        run_session_probe.run_probe(
            fixture_root=FIXTURE_ROOT,
            turn1=supplied,
            turn2=TURN2_PATH.read_text(encoding="utf-8"),
            config=_probe_config(),
            client=None,
            mode="offline",
        )

    assert str(error.value) == "Session 探针 prompt 不匹配"
    assert supplied not in str(error.value)


@pytest.mark.parametrize(
    ("client", "failure_code", "statuses"),
    [
        (FakeModelClient([_turn(None)]), "TURN1_NOT_COMPLETED", ("EMPTY_RESPONSE",)),
        (
            FakeModelClient(
                [
                    _turn(None, ToolCall("read", "read_file", '{"path":"greeting.py"}')),
                    _turn(None, ToolCall("edit", "edit_file", '{"path":"greeting.py","old_text":"return f\\\"Hello, {name}!\\\"","new_text":"return f\\\"你好，{name}！\\\""}')),
                    _turn(None, ToolCall("test", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
                    _turn("done"),
                    _turn(None),
                ]
            ),
            "TURN2_NOT_COMPLETED",
            ("COMPLETED", "EMPTY_RESPONSE"),
        ),
    ],
)
def test_run_probe_classifies_non_completed_turns_without_final_text(
    client: FakeModelClient,
    failure_code: str,
    statuses: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"),
        config=_probe_config(),
        client=client,
        mode="offline",
    )

    assert report.outcome == "FAIL"
    assert report.failure_code == failure_code
    assert report.turn_statuses == statuses
    assert report.model_rounds
    assert report.tool_calls
    if failure_code == "TURN2_NOT_COMPLETED":
        assert report.turn2_exact_value_observed is None
    assert "final_text" not in report.to_dict()


def test_run_probe_rejects_second_turn_file_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    client = _successful_probe_client(
        second_call=ToolCall(
            "four", "write_file", '{"path":"extra.txt","content":"not allowed"}'
        )
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=client, mode="offline",
    )

    assert (report.outcome, report.failure_code) == ("FAIL", "PRIMARY_TOOL_POLICY_FAILED")


@pytest.mark.parametrize(
    ("client", "changed_paths", "failure_code"),
    [
        (
            FakeModelClient(
                [
                    _turn(None, ToolCall("pytest", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
                    _turn("claimed success"),
                ]
                ),
                (),
                "PRIMARY_TOOL_POLICY_FAILED",
        ),
        (
            FakeModelClient(
                [
                    _turn(
                        None,
                        ToolCall("outside", "write_file", '{"path":"outside.txt","content":"bad"}'),
                    ),
                    _turn(None, ToolCall("pytest", "run_command", '{"argv":["python","-m","pytest","-q"]}')),
                    _turn("claimed success"),
                ]
            ),
                (),
                "PRIMARY_TOOL_POLICY_FAILED",
        ),
    ],
)
def test_run_probe_requires_exactly_one_allowed_first_turn_change(
    client: FakeModelClient,
    changed_paths: tuple[str, ...],
    failure_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=client, mode="offline",
    )

    assert (report.outcome, report.failure_code) == ("FAIL", failure_code)
    assert report.changed_paths_after_turn1 == changed_paths


@pytest.mark.parametrize(
    ("after_first", "after_second", "outcome", "failure_code"),
    [
        (CommandResult(1, "secret stdout", "secret stderr", False), None, "FAIL", "TURN1_TESTS_FAILED"),
        (CommandResult(None, "secret stdout", "secret stderr", True), None, "INVALID_INFRA", "PROCESS_FAILURE"),
    ],
)
def test_run_probe_classifies_independent_test_failure_without_output_leaks(
    monkeypatch: pytest.MonkeyPatch,
    after_first: CommandResult,
    after_second: CommandResult | None,
    outcome: str,
    failure_code: str,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    results = [after_first] if after_second is None else [after_first, after_second]
    monkeypatch.setattr(run_session_probe, "run_process", lambda *_args, **_kwargs: results.pop(0))

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert (report.outcome, report.failure_code) == (outcome, failure_code)
    assert "secret stdout" not in serialized
    assert "secret stderr" not in serialized


@pytest.mark.parametrize(
    ("stage", "result", "expected"),
    [
        ("first", CommandResult(1, "hidden", "hidden", False), ("FAIL", "TURN1_TESTS_FAILED")),
        ("first", CommandResult(None, "hidden", "hidden", True), ("INVALID_INFRA", "PROCESS_FAILURE")),
        ("second", CommandResult(1, "hidden", "hidden", False), ("FAIL", "TURN2_TESTS_FAILED")),
        ("second", CommandResult(None, "hidden", "hidden", True), ("INVALID_INFRA", "PROCESS_FAILURE")),
    ],
)
def test_run_probe_classifies_each_independent_pytest_result(
    stage: str,
    result: CommandResult,
    expected: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    results = [result] if stage == "first" else [CommandResult(0, "", "", False), result]
    monkeypatch.setattr(run_session_probe, "run_process", lambda *_args, **_kwargs: results.pop(0))

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    assert (report.outcome, report.failure_code) == expected


@pytest.mark.parametrize("stage", ["first", "second"])
def test_run_probe_redacts_exception_from_each_independent_pytest(
    stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    calls = 0

    def failing_process(*_args: object, **_kwargs: object) -> CommandResult:
        nonlocal calls
        calls += 1
        if calls == (1 if stage == "first" else 2):
            raise RuntimeError(f"prompt片段 Bearer synthetic-secret-value {tmp_path}")
        return CommandResult(0, "", "", False)

    monkeypatch.setattr(run_session_probe, "run_process", failing_process)
    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert (report.outcome, report.failure_code) == ("INVALID_INFRA", "PROCESS_FAILURE")
    assert "synthetic-secret-value" not in serialized
    assert str(tmp_path) not in serialized


def test_run_probe_redacts_session_constructor_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive = f"prompt片段 Bearer synthetic-secret-value {tmp_path}"
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())

    def broken_session(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(sensitive)

    monkeypatch.setattr(run_session_probe, "AgentSession", broken_session)
    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=None, mode="offline",
    )

    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert (report.outcome, report.failure_code) == ("INVALID_INFRA", "PROBE_RUNTIME_ERROR")
    assert sensitive not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.parametrize(
    ("patch_kind", "expected"),
    [
        ("undo", ("FAIL", "UNDO_NOT_RESTORED")),
        ("reset", ("FAIL", "RESET_NOT_CLEAN")),
        ("artifact", ("FAIL", "SESSION_ARTIFACT_FORBIDDEN")),
        ("close", ("INVALID_INFRA", "PROBE_RUNTIME_ERROR")),
    ],
)
def test_run_probe_classifies_lifecycle_boundary_failures(
    patch_kind: str,
    expected: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    results = [CommandResult(0, "", "", False), CommandResult(0, "", "", False)]
    monkeypatch.setattr(run_session_probe, "run_process", lambda *_args, **_kwargs: results.pop(0))
    if patch_kind == "undo":
        monkeypatch.setattr(
            run_session_probe.AgentSession,
            "undo",
            lambda _self: UndoResult(False, "UNDO_WRITE_FAILED", "hidden"),
        )
    elif patch_kind == "reset":
        monkeypatch.setattr(run_session_probe.AgentSession, "reset", lambda _self: None)
    elif patch_kind == "artifact":
        monkeypatch.setattr(
            run_session_probe,
            "_forbidden_session_artifact_count",
            lambda _initial, _current: 1,
        )
    else:
        def failing_close(_self: object) -> None:
            raise OSError("Bearer synthetic-secret-value")

        monkeypatch.setattr(run_session_probe.AgentSession, "close", failing_close)

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    assert (report.outcome, report.failure_code) == expected
    assert "synthetic-secret-value" not in json.dumps(report.to_dict(), ensure_ascii=False)


def test_late_artifact_exception_preserves_completed_lifecycle_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    monkeypatch.setattr(run_session_probe, "_direct_subprocess_pids", lambda: frozenset(), raising=False)

    def fail_artifact_scan(_initial: object, _current: object) -> int:
        raise OSError("Bearer synthetic-artifact-secret")

    monkeypatch.setattr(
        run_session_probe,
        "_forbidden_session_artifact_count",
        fail_artifact_scan,
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"),
        config=_probe_config(),
        client=_successful_probe_client(),
        mode="offline",
    )

    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert (report.outcome, report.failure_code) == ("INVALID_INFRA", "PROBE_RUNTIME_ERROR")
    assert report.reset_history_message_count == 1
    assert report.reset_read_hash_count == 0
    assert report.reset_undo_depth == 0
    assert report.reset_pending_events == 0
    assert report.close_idempotent is True
    assert report.owned_client_close_calls is None
    assert report.baseline_direct_subprocess_count == 0
    assert report.new_residual_direct_subprocess_count == 0
    assert "synthetic-artifact-secret" not in serialized


def test_workspace_snapshot_excludes_git_and_runtime_files(tmp_path: Path) -> None:
    (tmp_path / "greeting.py").write_text("source", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "state").write_text("cache", encoding="utf-8")
    (tmp_path / "compiled.pyc").write_bytes(b"bytecode")

    assert run_session_probe._workspace_file_map(tmp_path) == {
        "greeting.py": run_session_probe._sha256_file(tmp_path / "greeting.py")
    }


def test_workspace_snapshot_excludes_git_worktree_pointer_file(tmp_path: Path) -> None:
    (tmp_path / "greeting.py").write_text("source", encoding="utf-8")
    (tmp_path / ".git").write_text("gitdir: elsewhere", encoding="utf-8")

    assert set(run_session_probe._workspace_file_map(tmp_path)) == {"greeting.py"}


@pytest.mark.parametrize(
    ("argv", "sentinel_name"),
    [
        (["python", "-c", "open('sentinel.txt', 'w').write('x')"], "sentinel.txt"),
        (["git", "push"], None),
    ],
)
def test_probe_rejects_every_model_command_except_exact_pytest(
    argv: list[str],
    sentinel_name: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    observed_sentinel_states: list[bool] = []
    original_map = run_session_probe._workspace_file_map

    def observe_workspace(root: Path) -> dict[str, str]:
        if sentinel_name is not None:
            observed_sentinel_states.append((root / sentinel_name).exists())
        return original_map(root)

    monkeypatch.setattr(run_session_probe, "_workspace_file_map", observe_workspace)
    client = FakeModelClient(
        [
            _turn(None, ToolCall("unexpected-command", "run_command", json.dumps({"argv": argv}))),
            _turn("claimed success"),
        ]
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=client, mode="offline",
    )

    assert report.outcome != "PASS"
    assert report.failure_code == "UNEXPECTED_COMMAND"
    if sentinel_name is not None:
        assert observed_sentinel_states and not any(observed_sentinel_states)


def test_probe_report_redacts_unexpected_changed_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    leaked_name = "prompt片段-Bearer-synthetic-probe-key.txt"
    original_run = run_session_probe.AgentSession.run
    calls = 0

    def inject_leaked_name(session: object, task: str) -> object:
        nonlocal calls
        result = original_run(session, task)  # type: ignore[arg-type]
        calls += 1
        if calls == 1:
            (session._workspace_policy.workspace / leaked_name).write_text(  # type: ignore[attr-defined]
                "covert", encoding="utf-8"
            )
        return result

    monkeypatch.setattr(run_session_probe.AgentSession, "run", inject_leaked_name)

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert report.failure_code == "TURN1_CHANGE_SCOPE"
    assert report.changed_paths_after_turn1 == ("greeting.py", "<unexpected-path>")
    assert leaked_name not in serialized
    assert "synthetic-probe-key" not in serialized


def test_probe_rejects_second_turn_same_content_write_even_without_hash_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    client = _successful_probe_client(
        second_call=ToolCall(
            "same-content-write",
            "write_file",
            '{"path":"greeting.py","content":"def greeting(name: str) -> str:\\n    return f\\\"你好，{name}！\\\"\\n"}',
        )
    )

    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=client, mode="offline",
    )

    assert (report.outcome, report.failure_code) == ("FAIL", "PRIMARY_TOOL_POLICY_FAILED")


@pytest.mark.parametrize(
    ("turn_number", "relative"),
    [
        (1, ".code-operator/history.json"),
        (2, "covert-empty-directory"),
        (2, ".pytest_cache/covert.txt"),
    ],
)
def test_probe_rejects_unknown_or_new_workspace_entries(
    turn_number: int,
    relative: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    original_run = run_session_probe.AgentSession.run
    runs = 0

    def inject_entry(session: object, task: str) -> object:
        nonlocal runs
        result = original_run(session, task)  # type: ignore[arg-type]
        runs += 1
        if runs == turn_number:
            workspace = session._workspace_policy.workspace  # type: ignore[attr-defined]
            entry = workspace / relative
            if entry.suffix:
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text("covert", encoding="utf-8")
            else:
                entry.mkdir(parents=True)
        return result

    monkeypatch.setattr(run_session_probe.AgentSession, "run", inject_entry)
    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    assert (report.outcome, report.failure_code) == ("FAIL", "WORKSPACE_MANIFEST_CHANGED")


def test_probe_rejects_workspace_symlink_entry_when_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")
    original_run = run_session_probe.AgentSession.run
    calls = 0

    def inject_link(session: object, task: str) -> object:
        nonlocal calls
        result = original_run(session, task)  # type: ignore[arg-type]
        calls += 1
        if calls == 2:
            _make_symlink_or_skip(
                session._workspace_policy.workspace / "covert-link",  # type: ignore[attr-defined]
                external,
                target_is_directory=False,
            )
        return result

    monkeypatch.setattr(run_session_probe.AgentSession, "run", inject_link)
    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    assert (report.outcome, report.failure_code) == ("FAIL", "WORKSPACE_MANIFEST_CHANGED")


def test_probe_rejects_first_turn_ntfs_junction_even_when_tests_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_session_probe, "validate_fixture", lambda **_kwargs: _probe_summary())
    original_run = run_session_probe.AgentSession.run
    runs = 0

    def replace_tests_with_junction(session: object, task: str) -> object:
        nonlocal runs
        result = original_run(session, task)  # type: ignore[arg-type]
        runs += 1
        if runs == 1:
            workspace = session._workspace_policy.workspace  # type: ignore[attr-defined]
            outside_tests = workspace.parent / "outside-tests"
            shutil.move(str(workspace / "tests"), str(outside_tests))
            _make_junction_or_skip(workspace / "tests", outside_tests)
        return result

    monkeypatch.setattr(run_session_probe.AgentSession, "run", replace_tests_with_junction)
    report = run_session_probe.run_probe(
        fixture_root=FIXTURE_ROOT, turn1=TURN1_PATH.read_text(encoding="utf-8"),
        turn2=TURN2_PATH.read_text(encoding="utf-8"), config=_probe_config(),
        client=_successful_probe_client(), mode="offline",
    )

    assert (report.outcome, report.failure_code) == ("FAIL", "WORKSPACE_MANIFEST_CHANGED")


def test_cli_validate_fixture_is_offline_and_prints_only_safe_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = _probe_summary()
    calls: list[str] = []
    monkeypatch.setattr(
        run_session_probe,
        "validate_fixture",
        lambda: calls.append("validate") or summary,
    )
    monkeypatch.setattr(
        run_session_probe,
        "load_provider_config",
        lambda: calls.append("config"),
        raising=False,
    )
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **_kwargs: calls.append("probe"),
    )

    assert run_session_probe.main(["--validate-fixture"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    payload = json.loads(captured.out)
    assert calls == ["validate"]
    assert payload == {
        "mode": "validate-fixture",
        "outcome": "VALID",
        "fixture_sha256": summary.fixture_sha256,
        "prompt_sha256": summary.prompt_sha256,
        "target_initial_sha256": summary.target_initial_sha256,
        "initial_test_returncode": 1,
        "target_relative_path": "greeting.py",
    }


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--validate-fixture", "--real", "--report", "docs/evidence/e4-session-probe.json"],
        ["--real"],
        ["--validate-fixture", "--report", "docs/evidence/e4-session-probe.json"],
        ["--validate-fixture", "--api-key", "synthetic-key"],
    ],
)
def test_cli_parser_rejects_invalid_mode_combinations(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        run_session_probe.build_parser().parse_args(argv)

    assert error.value.code == 2


def test_cli_unknown_argument_value_is_not_echoed_by_main(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "synthetic-parser-secret-value"

    assert run_session_probe.main(["--validate-fixture", "--api-key", secret]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "invalid command-line arguments\n"
    assert secret not in captured.out
    assert secret not in captured.err


def test_cli_unknown_argument_value_is_not_echoed_by_module_entrypoint() -> None:
    secret = "synthetic-subprocess-parser-secret"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.run_session_probe",
            "--validate-fixture",
            "--api-key",
            secret,
        ],
        cwd=run_session_probe.REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "invalid command-line arguments\n"
    assert secret not in result.stdout
    assert secret not in result.stderr


def test_resolve_report_path_requires_exact_safe_repository_destination(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    evidence = repository / "docs" / "evidence"
    evidence.mkdir(parents=True)
    expected = evidence / "e4-session-probe.json"

    assert run_session_probe.resolve_report_path(
        Path("docs/evidence/e4-session-probe.json"), repository_root=repository
    ) == expected
    for raw in (
        Path("docs/evidence/other.json"),
        Path("docs/evidence/../evidence/e4-session-probe.json"),
        tmp_path / "outside.json",
    ):
        with pytest.raises(EvalInfrastructureError):
            run_session_probe.resolve_report_path(raw, repository_root=repository)


def test_resolve_report_path_rejects_linked_parent_or_existing_link(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    (repository / "docs").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    _make_symlink_or_skip(repository / "docs" / "evidence", outside, target_is_directory=True)
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.resolve_report_path(
            Path("docs/evidence/e4-session-probe.json"), repository_root=repository
        )


def test_resolve_report_path_rejects_existing_report_link(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    evidence = repository / "docs" / "evidence"
    evidence.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    _make_symlink_or_skip(
        evidence / "e4-session-probe.json", outside, target_is_directory=False
    )

    with pytest.raises(EvalInfrastructureError):
        run_session_probe.resolve_report_path(
            Path("docs/evidence/e4-session-probe.json"), repository_root=repository
        )


def test_real_mode_rejects_broken_ntfs_junction_report_target_before_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    evidence = repository / "docs" / "evidence"
    evidence.mkdir(parents=True)
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    target = evidence / "e4-session-probe.json"
    _make_junction_or_skip(target, outside)
    shutil.rmtree(outside)
    assert not target.exists()
    assert not target.is_symlink()
    assert run_session_probe._is_link_or_reparse(target)
    calls: list[str] = []
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository, raising=False)
    monkeypatch.setattr(
        run_session_probe,
        "load_provider_config",
        lambda: calls.append("config"),
        raising=False,
    )
    monkeypatch.setattr(
        run_session_probe, "run_probe", lambda **_kwargs: calls.append("probe")
    )

    assert run_session_probe.main(["--real", "--report", "docs/evidence/e4-session-probe.json"]) == 2
    assert calls == []


def test_real_mode_rejects_existing_report_before_config_or_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "repo"
    report = repository / "docs" / "evidence" / "e4-session-probe.json"
    report.parent.mkdir(parents=True)
    report.write_text("existing", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository, raising=False)
    monkeypatch.setattr(
        run_session_probe, "load_provider_config", lambda: calls.append("config"), raising=False
    )
    monkeypatch.setattr(
        run_session_probe, "run_probe", lambda **_kwargs: calls.append("probe")
    )

    assert run_session_probe.main(["--real", "--report", "docs/evidence/e4-session-probe.json"]) == 2
    assert calls == []
    assert report.read_text(encoding="utf-8") == "existing"
    assert "existing" not in capsys.readouterr().err


def test_real_mode_uses_only_provider_config_and_safe_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "repo"
    (repository / "docs" / "evidence").mkdir(parents=True)
    config = ProviderConfig(
        api_key="synthetic-real-key", base_url="https://api.moonshot.cn/v1", model="kimi-k3"
    )
    report = ProbeReport.invalid_infra("real", "FIXTURE_ERROR")
    observed: dict[str, object] = {}
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository, raising=False)
    monkeypatch.setattr(run_session_probe, "load_provider_config", lambda: config, raising=False)
    monkeypatch.setattr(
        run_session_probe,
        "run_probe",
        lambda **kwargs: observed.update(kwargs) or report,
    )

    assert run_session_probe.main(["--real", "--report", "docs/evidence/e4-session-probe.json"]) == 2

    assert observed["client"] is None
    assert observed["fixture_root"] == FIXTURE_ROOT
    assert observed["turn1"] == TURN1_PATH.read_text(encoding="utf-8")
    assert observed["turn2"] == TURN2_PATH.read_text(encoding="utf-8")
    assert observed["mode"] == "real"
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "mode", "outcome", "failure_code", "turn_statuses", "model_rounds", "tool_calls",
        "provider_total_tokens", "tests_after_turn1_returncode", "tests_after_turn2_returncode",
        "undo_ok", "undo_depth_after", "reset_undo_depth", "reset_pending_events",
        "reset_history_message_count", "reset_read_hash_count", "close_idempotent",
        "owned_client_close_calls", "baseline_direct_subprocess_count",
        "new_residual_direct_subprocess_count",
        "session_artifact_count", "elapsed_seconds", "report",
    }
    assert payload["report"] == "docs/evidence/e4-session-probe.json"
    assert payload["baseline_direct_subprocess_count"] is None
    assert payload["new_residual_direct_subprocess_count"] is None
    assert "active_subprocess_count" not in payload
    assert "synthetic-real-key" not in json.dumps(payload)


@pytest.mark.parametrize(
    "config",
    [
        ProviderConfig(api_key="key", base_url="https://api.moonshot.cn/v1", model="other"),
        ProviderConfig(api_key="key", base_url="http://api.moonshot.cn/v1", model="kimi-k3"),
        ProviderConfig(api_key="key", base_url="https://other.example/v1", model="kimi-k3"),
    ],
)
def test_real_mode_rejects_non_moonshot_kimi_configuration(
    config: ProviderConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    (repository / "docs" / "evidence").mkdir(parents=True)
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository, raising=False)
    monkeypatch.setattr(run_session_probe, "load_provider_config", lambda: config, raising=False)
    monkeypatch.setattr(run_session_probe, "run_probe", lambda **_kwargs: pytest.fail("must not run"))

    assert run_session_probe.main(["--real", "--report", "docs/evidence/e4-session-probe.json"]) == 2


def test_write_probe_report_redacts_bearer_but_rejects_nonreportable_content(
    tmp_path: Path,
) -> None:
    target = tmp_path / "e4-session-probe.json"
    safe = {"detail": "Authorization: Bearer synthetic-bearer-value"}
    run_session_probe.write_probe_report_exclusive(target, safe, api_key="synthetic-api-key")
    written = target.read_text(encoding="utf-8")
    assert "synthetic-bearer-value" not in written
    assert "Bearer <REDACTED>" in written
    for unsafe in (
        {"detail": "synthetic-api-key"},
        {"detail": "-----BEGIN RSA PRIVATE KEY-----"},
        {"detail": TURN1_PATH.read_text(encoding="utf-8")},
        {"detail": str(tmp_path)},
    ):
        candidate = tmp_path / ("candidate-" + str(len(list(tmp_path.iterdir()))) + ".json")
        before = sorted(item.name for item in tmp_path.iterdir())
        with pytest.raises(EvalInfrastructureError):
            run_session_probe.write_probe_report_exclusive(candidate, unsafe, api_key="synthetic-api-key")
        assert not candidate.exists()
        assert sorted(item.name for item in tmp_path.iterdir()) == before


def test_write_probe_report_preserves_exclusive_create_semantics(tmp_path: Path) -> None:
    path = tmp_path / "e4-session-probe.json"
    run_session_probe.write_probe_report_exclusive(path, {"ok": True}, api_key="key")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.write_probe_report_exclusive(path, {"ok": True}, api_key="key")


def test_write_probe_report_removes_artifact_when_parent_is_replaced_at_writer_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "repo" / "docs" / "evidence"
    evidence.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    path = evidence / "e4-session-probe.json"

    def replace_parent_then_write(
        received_path: Path, _report: dict[str, object], *, api_key: str
    ) -> None:
        shutil.rmtree(evidence)
        _make_junction_or_skip(evidence, outside)
        received_path.write_text("created-by-writer", encoding="utf-8")

    monkeypatch.setattr(run_session_probe, "write_report_exclusive", replace_parent_then_write)

    with pytest.raises(EvalInfrastructureError):
        run_session_probe.write_probe_report_exclusive(path, {"ok": True}, api_key="synthetic-key")

    # The parent changed, so the helper cannot prove ownership of the new
    # external entry and must prefer a possible residue to unsafe deletion.
    assert (outside / "e4-session-probe.json").exists()
    assert not list(outside.glob(".e4-session-probe.json.*.tmp"))


def test_write_probe_report_never_deletes_second_parent_replacement_victim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "repo" / "docs" / "evidence"
    evidence.mkdir(parents=True)
    outside_one = tmp_path / "outside-one"
    outside_two = tmp_path / "outside-two"
    outside_one.mkdir()
    outside_two.mkdir()
    path = evidence / "e4-session-probe.json"
    victim = outside_two / "e4-session-probe.json"
    victim.write_text("preserved-victim", encoding="utf-8")

    def replace_parent_twice(
        received_path: Path, _report: dict[str, object], *, api_key: str
    ) -> None:
        shutil.rmtree(evidence)
        _make_junction_or_skip(evidence, outside_one)
        received_path.write_text("created-by-writer", encoding="utf-8")
        evidence.rmdir()
        _make_junction_or_skip(evidence, outside_two)

    monkeypatch.setattr(run_session_probe, "write_report_exclusive", replace_parent_twice)

    with pytest.raises(EvalInfrastructureError):
        run_session_probe.write_probe_report_exclusive(path, {"ok": True}, api_key="synthetic-key")

    assert victim.read_text(encoding="utf-8") == "preserved-victim"
    assert (outside_one / "e4-session-probe.json").read_text(encoding="utf-8") == "created-by-writer"


def test_validate_fixture_unexpected_error_is_fixed_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sensitive = f"Bearer synthetic-validate-secret {tmp_path}"
    monkeypatch.setattr(
        run_session_probe,
        "validate_fixture",
        lambda: (_ for _ in ()).throw(RuntimeError(sensitive)),
    )

    assert run_session_probe.main(["--validate-fixture"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "冻结 fixture 校验失败\n"
    assert sensitive not in captured.err
    assert str(tmp_path) not in captured.err


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(9)])
def test_validate_fixture_does_not_swallow_interrupt_or_exit(
    error: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        run_session_probe,
        "validate_fixture",
        lambda: (_ for _ in ()).throw(error),
    )

    with pytest.raises(type(error)):
        run_session_probe.main(["--validate-fixture"])


@pytest.mark.parametrize(
    "unsafe_path",
    [r"\\server\share\e4-session-probe.json", r"\\?\C:\secret\probe.json", r"\\.\PhysicalDrive0"],
)
def test_write_probe_report_rejects_raw_unc_and_device_paths(
    tmp_path: Path, unsafe_path: str
) -> None:
    path = tmp_path / "e4-session-probe.json"

    with pytest.raises(EvalInfrastructureError):
        run_session_probe.write_probe_report_exclusive(
            path, {"detail": unsafe_path}, api_key="synthetic-key"
        )

    assert not path.exists()
    assert not list(tmp_path.glob(".e4-session-probe.json.*.tmp"))


# Task 4: fixed O1b result paths, offline summary, and CLI gates.
def _write_o1b_reservation(root: Path, attempt_index: int) -> Path:
    path = root / f"o1b-session-probe-{attempt_index:02d}.reservation.json"
    path.write_text(
        json.dumps(asdict(_offline_reservation(attempt_index=attempt_index)), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _write_o1b_result(root: Path, attempt_index: int, *, outcome: str, ideal: object = True) -> Path:
    path = root / f"o1b-session-probe-{attempt_index:02d}.json"
    metadata = _offline_frozen_metadata()
    payload = asdict(ProbeReport.invalid_infra("real", "SYNTHETIC"))
    payload.update({
        "protocol_version": O1B_PROTOCOL_VERSION,
        "attempt_index": attempt_index,
        "production_tree_sha256": metadata.production_tree_sha256,
        "evaluator_protocol_sha256": metadata.evaluator_protocol_sha256,
        "config": metadata.config,
        "outcome": outcome,
        "failure_code": None if outcome == "PASS" else "PRIMARY_TOOL_POLICY_FAILED",
        "fixture_sha256": metadata.fixture_sha256,
        "prompt_sha256": metadata.prompt_sha256,
        "target_initial_sha256": metadata.target_initial_sha256,
        "turn1_ideal_trace": ideal,
        "turn2_ideal_trace": ideal,
        "ideal_trace_overall": ideal,
        "turn2_exact_value_observed": True if outcome == "PASS" else False,
    })
    if outcome == "PASS":
        payload.update({
            "turn_statuses": ["COMPLETED", "COMPLETED"],
            "model_rounds": [1, 1],
            "tool_calls": [1, 1],
            "provider_total_tokens": [1, 1],
            "changed_paths_after_turn1": ["greeting.py"],
            "tests_after_turn1_returncode": 0,
            "tests_after_turn2_returncode": 0,
            "undo_ok": True,
            "undo_depth_after": 0,
            "reset_undo_depth": 0,
            "reset_pending_events": 0,
            "reset_history_message_count": 1,
            "reset_read_hash_count": 0,
            "close_idempotent": True,
            "owned_client_close_calls": 1,
            "baseline_direct_subprocess_count": 0,
            "new_residual_direct_subprocess_count": 0,
            "session_artifact_count": 0,
            "target_after_turn1_sha256": "d" * 64,
            "target_after_undo_sha256": metadata.target_initial_sha256,
        })
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def test_summary_keeps_reserved_attempt_without_result_as_invalid_infra(tmp_path: Path) -> None:
    _write_o1b_reservation(tmp_path, 1)
    summary = run_session_probe.build_o1b_summary(
        evidence_root=tmp_path,
        stop_reason="PROVIDER_BOUNDARY_STOP",
    )
    assert summary.planned_attempts == 3
    assert summary.attempted_count == 1
    assert summary.valid_attempts == 0
    assert summary.invalid_infra_count == 1
    assert summary.unexecuted_attempts == (2, 3)
    assert summary.classification == "O1B_INCONCLUSIVE"
    assert summary.attempts[0]["status"] == "RESERVED_NO_RESULT"


@pytest.mark.parametrize(
    ("outcomes", "classification"),
    [
        (("PASS", "PASS", "INVALID_INFRA"), "O1B_SUPPORTED"),
        (("PASS", "FAIL"), "O1B_MIXED"),
        (("FAIL", "FAIL"), "O1B_NOT_SUPPORTED"),
        (("PASS",), "O1B_INCONCLUSIVE"),
    ],
)
def test_summary_classifies_only_valid_attempts(
    tmp_path: Path, outcomes: tuple[str, ...], classification: str
) -> None:
    for index, outcome in enumerate(outcomes, 1):
        _write_o1b_reservation(tmp_path, index)
        _write_o1b_result(tmp_path, index, outcome=outcome, ideal=outcome == "PASS")
    summary = run_session_probe.build_o1b_summary(
        evidence_root=tmp_path,
        stop_reason="COMPLETED_PLANNED_ATTEMPTS",
    )
    assert summary.classification == classification
    assert summary.valid_attempts == sum(outcome in {"PASS", "FAIL"} for outcome in outcomes)
    assert summary.primary_passes == sum(outcome == "PASS" for outcome in outcomes)
    assert summary.ideal_trace_passes == sum(outcome == "PASS" for outcome in outcomes)
    assert summary.planned_attempts == 3


def test_summary_rejects_result_without_matching_reservation(tmp_path: Path) -> None:
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


def test_summary_rejects_reservation_number_hole(tmp_path: Path) -> None:
    _write_o1b_reservation(tmp_path, 2)
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


def test_summary_rejects_result_metadata_mismatch(tmp_path: Path) -> None:
    _write_o1b_reservation(tmp_path, 1)
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    result = tmp_path / "o1b-session-probe-01.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["config"]["model"] = "drifted"
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


def test_summary_rejects_result_with_extra_or_missing_schema_fields(tmp_path: Path) -> None:
    _write_o1b_reservation(tmp_path, 1)
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    result = tmp_path / "o1b-session-probe-01.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload.pop("config")
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )
    payload["config"] = _offline_frozen_metadata().config
    payload["answer"] = "你好，小明！"
    result.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


def test_summary_requires_real_result_mode(tmp_path: Path) -> None:
    _write_o1b_reservation(tmp_path, 1)
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    result = tmp_path / "o1b-session-probe-01.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["mode"] = "offline"
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


@pytest.mark.parametrize(
    ("turn1", "turn2", "overall"),
    [
        (False, True, True),
        (None, True, True),
    ],
)
def test_summary_rejects_inconsistent_ideal_trace_overall(
    tmp_path: Path, turn1: object, turn2: object, overall: object
) -> None:
    _write_o1b_reservation(tmp_path, 1)
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    result = tmp_path / "o1b-session-probe-01.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["turn1_ideal_trace"] = turn1
    payload["turn2_ideal_trace"] = turn2
    payload["turn2_exact_value_observed"] = True
    payload["ideal_trace_overall"] = overall
    result.write_text(json.dumps(payload), encoding="utf-8")
    expected = overall if turn1 is not None and turn2 is not None else None
    if (turn1 is False and turn2 is True and overall is True) or expected != overall:
        with pytest.raises(EvalInfrastructureError):
            run_session_probe.build_o1b_summary(
                evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
            )


@pytest.mark.parametrize(
    "field",
    ["turn1_ideal_trace", "turn2_ideal_trace", "turn2_exact_value_observed"],
)
@pytest.mark.parametrize("value", ["true", 0, 1])
def test_summary_rejects_non_boolean_nullable_trace_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    _write_o1b_reservation(tmp_path, 1)
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    result = tmp_path / "o1b-session-probe-01.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload[field] = value
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


def test_summary_rejects_non_boolean_undo_ok(tmp_path: Path) -> None:
    _write_o1b_reservation(tmp_path, 1)
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    result = tmp_path / "o1b-session-probe-01.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["undo_ok"] = "not-bool"
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


def test_summary_rejects_pass_without_exact_value(tmp_path: Path) -> None:
    _write_o1b_reservation(tmp_path, 1)
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    result = tmp_path / "o1b-session-probe-01.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["turn2_exact_value_observed"] = False
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


def test_summary_rejects_pass_with_unobserved_ideal_trace_fields(tmp_path: Path) -> None:
    _write_o1b_reservation(tmp_path, 1)
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    result = tmp_path / "o1b-session-probe-01.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["turn1_ideal_trace"] = None
    payload["turn2_ideal_trace"] = None
    payload["ideal_trace_overall"] = None
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


def test_summary_rejects_hand_edited_outcome_failure_pair(tmp_path: Path) -> None:
    _write_o1b_reservation(tmp_path, 1)
    _write_o1b_result(tmp_path, 1, outcome="PASS")
    result = tmp_path / "o1b-session-probe-01.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["outcome"] = "FAIL"
    payload["failure_code"] = None
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(
            evidence_root=tmp_path, stop_reason="PROVIDER_BOUNDARY_STOP"
        )


@pytest.mark.parametrize("stop_reason", ["BAD", "", "security_stop"])
def test_summary_rejects_non_frozen_stop_reason(tmp_path: Path, stop_reason: str) -> None:
    with pytest.raises(EvalInfrastructureError):
        run_session_probe.build_o1b_summary(evidence_root=tmp_path, stop_reason=stop_reason)


def test_o1b_cli_real_requires_attempt_reservation_and_fixed_report_before_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    evidence = repository / "docs" / "evidence"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(
        run_session_probe, "load_provider_config", lambda: pytest.fail("config must not load")
    )
    assert run_session_probe.main(["--real", "--report", "docs/evidence/o1b-session-probe-01.json"]) == 2
    assert not list(evidence.iterdir())


def test_o1b_cli_real_rejects_nonfixed_paths_and_existing_files_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    evidence = repository / "docs" / "evidence"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(
        run_session_probe, "load_provider_config", lambda: pytest.fail("config must not load")
    )
    args = ["--real", "--attempt", "1", "--reservation", "bad.json", "--report", "bad.json"]
    assert run_session_probe.main(args) == 2
    reservation = evidence / "o1b-session-probe-01.reservation.json"
    report = evidence / "o1b-session-probe-01.json"
    reservation.write_text("occupied", encoding="utf-8")
    assert run_session_probe.main([
        "--real", "--attempt", "1", "--reservation",
        "docs/evidence/o1b-session-probe-01.reservation.json", "--report",
        "docs/evidence/o1b-session-probe-01.json",
    ]) == 2
    assert report.exists() is False


def test_o1b_cli_summarize_is_offline_and_prints_relative_safe_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "repo"
    evidence = repository / "docs" / "evidence"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(
        run_session_probe, "load_provider_config", lambda: pytest.fail("summary must not load config")
    )
    summary = evidence / "o1b-session-probe-summary.json"
    assert run_session_probe.main([
        "--summarize", "--stop-reason", "PROVIDER_BOUNDARY_STOP",
        "--summary", "docs/evidence/o1b-session-probe-summary.json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["classification"] == "O1B_INCONCLUSIVE"
    assert payload["summary"] == "docs/evidence/o1b-session-probe-summary.json"
    assert str(repository) not in json.dumps(payload)
    assert not summary.read_text(encoding="utf-8").find(str(repository)) >= 0


def test_o1b_cli_summarize_rejects_existing_summary_before_reading_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    summary = repository / "docs" / "evidence" / "o1b-session-probe-summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository)
    assert run_session_probe.main([
        "--summarize", "--stop-reason", "PROVIDER_BOUNDARY_STOP",
        "--summary", "docs/evidence/o1b-session-probe-summary.json",
    ]) == 2
    assert summary.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("stage", ["config", "probe"])
def test_real_mode_classifies_unexpected_runtime_errors_without_leaking_them(
    stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "repo"
    (repository / "docs" / "evidence").mkdir(parents=True)
    sensitive = f"Bearer synthetic-secret-value {tmp_path}"
    config = ProviderConfig(
        api_key="synthetic-api-key", base_url="https://api.moonshot.cn/v1", model="kimi-k3"
    )
    monkeypatch.setattr(run_session_probe, "REPOSITORY_ROOT", repository, raising=False)
    if stage == "config":
        monkeypatch.setattr(
            run_session_probe,
            "load_provider_config",
            lambda: (_ for _ in ()).throw(RuntimeError(sensitive)),
            raising=False,
        )
    else:
        monkeypatch.setattr(run_session_probe, "load_provider_config", lambda: config, raising=False)
        monkeypatch.setattr(
            run_session_probe,
            "run_probe",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(sensitive)),
        )

    assert run_session_probe.main(["--real", "--report", "docs/evidence/e4-session-probe.json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert sensitive not in captured.err
    assert "synthetic-secret-value" not in captured.err
    assert str(tmp_path) not in captured.err
