import hashlib
import json
from dataclasses import asdict

import pytest

from evals.reliability.schema import (
    ArmResult,
    PairingViolation,
    StudyReport,
    STUDY_ID,
    canonical_sha256,
    validate_tool_pairing,
)
from evals.reliability.context_study import (
    CONTEXT_MATRIX,
    ContextScenario,
    context_scenario,
    frozen_context_scenarios,
    message_level_trim,
    run_context_scenario,
)
from evals.reliability.abort_study import (
    AbortScenario,
    _captured_abort_tool_messages,
    _count_completed_success_payloads,
    frozen_abort_scenarios,
    immediate_abort_baseline,
    production_abort_result,
    run_abort_scenario,
)
from evals.reliability.error_study import (
    ERROR_CLASSES,
    RETRY_SHAPES,
    ErrorScenario,
    classify_retry,
    frozen_error_scenarios,
    run_error_scenario,
    structured_error_payload,
    vague_error_payload,
    _attributable_failure,
)
from code_operator.models import ToolResult


def _assistant(*calls):
    return {"role": "assistant", "tool_calls": [{"id": call} for call in calls]}


def _tool(call):
    return {"role": "tool", "tool_call_id": call, "content": "ok"}


def test_schema_models_and_canonical_hash_are_stable():
    violation = PairingViolation("MISSING_RESULT", "c1", 1)
    result = ArmResult("s1", "native", "control", True, {"n": 1}, (violation,))
    report = StudyReport(1, STUDY_ID, "abc", (result,))
    assert asdict(report)["results"][0]["violations"][0]["kind"] == "MISSING_RESULT"
    assert report.to_dict() == asdict(report)
    payload = {"b": 1, "a": "汉字"}
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert canonical_sha256(payload) == expected


def test_validate_tool_pairing_accepts_ordered_c1_c2():
    assert validate_tool_pairing([_assistant("c1", "c2"), _tool("c1"), _tool("c2")]) == ()


def test_validate_tool_pairing_reports_orphan_missing_out_of_order_and_duplicate():
    messages = [
        _tool("orphan"),
        _assistant("missing"),
        _assistant("a", "b"),
        _tool("b"),
        _tool("a"),
        _tool("a"),
    ]
    violations = validate_tool_pairing(messages)
    assert [item.kind for item in violations] == [
        "ORPHAN_RESULT", "MISSING_RESULT", "ORPHAN_RESULT",
        "ORPHAN_RESULT", "ORPHAN_RESULT"
    ]


def test_non_tool_and_wrong_id_occupy_the_fixed_result_window():
    non_tool = [
        _assistant("c1"),
        {"role": "user", "content": "inserted"},
        _tool("c1"),
    ]
    wrong_id = [
        _assistant("c1"),
        _tool("wrong"),
        _tool("c1"),
    ]
    assert [v.kind for v in validate_tool_pairing(non_tool)] == [
        "MISSING_RESULT", "ORPHAN_RESULT"
    ]
    assert [v.kind for v in validate_tool_pairing(wrong_id)] == [
        "ORPHAN_RESULT", "MISSING_RESULT", "ORPHAN_RESULT"
    ]


def test_eof_missing_has_exact_count_and_index():
    violations = validate_tool_pairing([_assistant("c1", "c2")])
    assert [(v.kind, v.call_id, v.message_index) for v in violations] == [
        ("MISSING_RESULT", "c1", 0), ("MISSING_RESULT", "c2", 0)
    ]


def test_out_of_order_b_then_a_reports_each_mismatched_window_slot():
    violations = validate_tool_pairing([_assistant("a", "b"), _tool("b"), _tool("a")])
    assert [v.kind for v in violations] == ["OUT_OF_ORDER_RESULT", "OUT_OF_ORDER_RESULT"]


def test_duplicate_result_has_exact_single_violation():
    violations = validate_tool_pairing([_assistant("c1"), _tool("c1"), _tool("c1")])
    assert [(v.kind, v.call_id, v.message_index) for v in violations] == [
        ("DUPLICATE_RESULT", "c1", 2)
    ]


def test_context_matrix_is_frozen_and_calibrated_for_message_level_ablation():
    assert CONTEXT_MATRIX == (
        ("C1_ONE_CALL_BOUNDARY", 0, 1, 1, "x" * 180, 1),
        ("C2_TWO_CALL_BOUNDARY", 0, 1, 2, "y" * 180, 1),
        ("C3_OLD_TURN_DROP", 1, 1, 1, "old" * 80, 3),
        ("C4_MULTI_TURN_MULTI_CALL", 2, 2, 2, "payload" * 40, 4),
        ("C5_NO_TOOL_OLD_TURN", 2, 0, 0, "plain" * 60, 2),
        ("C6_UTF8_BOUNDARY", 1, 1, 2, "中文路径" * 60, 3),
    )
    scenarios = frozen_context_scenarios()
    assert isinstance(scenarios, tuple)
    assert all(isinstance(item, ContextScenario) for item in scenarios)
    assert tuple(item.scenario_id for item in scenarios) == tuple(row[0] for row in CONTEXT_MATRIX)

    for scenario, row in zip(scenarios, CONTEXT_MATRIX):
        assert scenario.max_output_tokens == 64
        assert scenario.messages[0]["role"] == "system"
        assert sum(message.get("role") == "user" for message in scenario.messages) == row[1] + 1
        assert scenario.context_window > scenario.max_output_tokens


def test_context_calibration_full_input_is_over_budget_but_exact_message_drop_fits():
    for scenario, row in zip(frozen_context_scenarios(), CONTEXT_MATRIX):
        from code_operator.context import ContextManager

        manager = ContextManager(
            context_window=10_000,
            max_output_tokens=scenario.max_output_tokens,
        )
        full_estimate = manager.estimate_tokens(scenario.messages, scenario.tools)
        trimmed = [dict(message) for message in scenario.messages]
        for _ in range(row[5]):
            del trimmed[1]
        exact_estimate = manager.estimate_tokens(trimmed, scenario.tools)
        assert full_estimate > scenario.context_window - scenario.max_output_tokens
        assert exact_estimate <= scenario.context_window - scenario.max_output_tokens


def test_context_arms_validate_pairing_and_production_keeps_groups_intact():
    results = [run_context_scenario(item) for item in frozen_context_scenarios()]
    assert all(production.arm == "production_full_group" for _, production in results)
    assert all(production.violations == () for _, production in results)
    assert any(baseline.violations for baseline, _ in results)
    assert all(
        set(result.metrics) >= {
            "estimated_tokens",
            "kept_messages",
            "trimmed_messages",
            "trimmed_turns",
            "trimmed_rounds",
        }
        for pair in results
        for result in pair
    )


def test_message_level_trim_is_eval_only_and_keeps_system_and_newest_user():
    scenario = frozen_context_scenarios()[-1]
    messages, metrics = message_level_trim(scenario)
    assert messages[0] == scenario.messages[0]
    assert messages[-1].get("role") != "system"
    newest_user = [
        message
        for message in scenario.messages
        if message.get("role") == "user"
    ][-1]
    assert newest_user in messages
    assert metrics["trimmed_messages"] >= 0


def test_production_boundary_stops_safely_without_fabricating_prepared_metrics():
    scenarios = frozen_context_scenarios()
    for scenario in scenarios[:2]:
        _, production = run_context_scenario(scenario)
        assert production.passed is False
        assert production.metrics["outcome"] == "CONTEXT_LIMIT"
        assert production.metrics["safe_stop"] is True
        assert production.metrics["protocol_checked"] is False
        assert production.violations == ()
        assert production.metrics["input_budget"]
        assert production.metrics["required_minimum_tokens"]
        assert production.metrics["shortfall_tokens"] > 0
        assert production.metrics["kept_messages"] is None
        assert production.metrics["trimmed_messages"] is None
        assert production.metrics["trimmed_turns"] is None
        assert production.metrics["trimmed_rounds"] is None


def test_production_success_records_protocol_and_latest_group_status():
    scenarios = frozen_context_scenarios()
    for scenario in scenarios[2:]:
        _, production = run_context_scenario(scenario)
        assert production.metrics["outcome"] == "OK"
        assert production.metrics["safe_stop"] is False
        assert production.metrics["protocol_checked"] is True
        assert production.metrics["latest_group_preserved"] is True
        assert production.passed is True


def test_baseline_records_explicit_stop_and_protocol_fields():
    for scenario in frozen_context_scenarios():
        baseline, _ = run_context_scenario(scenario)
        assert baseline.metrics["outcome"] in {"OK", "CONTEXT_LIMIT"}
        assert isinstance(baseline.metrics["safe_stop"], bool)
        assert isinstance(baseline.metrics["protocol_checked"], bool)


def test_production_distinguishes_invalid_context_from_budget_exhaustion():
    from code_operator.context import ContextManager
    from evals.reliability.context_study import ContextScenario

    scenario = ContextScenario(
        "INVALID_ORPHAN",
        (
            {"role": "system", "content": "system"},
            {"role": "user", "content": "current"},
            {"role": "tool", "tool_call_id": "orphan", "content": "bad"},
        ),
        (),
        1_000,
        64,
    )
    manager = ContextManager(context_window=scenario.context_window, max_output_tokens=scenario.max_output_tokens)
    actual_estimate = manager.estimate_tokens(scenario.messages, scenario.tools)
    _, production = run_context_scenario(scenario)
    assert production.passed is False
    assert production.metrics["outcome"] == "INVALID_CONTEXT"
    assert production.metrics["safe_stop"] is False
    assert production.metrics["protocol_checked"] is True
    assert production.metrics["estimated_tokens"] == actual_estimate
    assert production.metrics["input_budget"] == manager.input_budget
    assert production.metrics["required_minimum_tokens"] is None
    assert production.metrics["shortfall_tokens"] == 0
    assert production.metrics["kept_messages"] is None
    assert production.metrics["trimmed_messages"] is None
    assert any(item.kind == "ORPHAN_RESULT" for item in production.violations)


def test_abort_matrix_is_frozen_with_every_position():
    scenarios = frozen_abort_scenarios()
    assert scenarios == tuple(
        AbortScenario(f"A{count}_{index}", count, index)
        for count in (2, 3, 4)
        for index in range(count)
    )


def test_immediate_abort_baseline_omits_current_or_future_results():
    for scenario in frozen_abort_scenarios():
        result = immediate_abort_baseline(scenario)
        assert result.arm == "immediate_abort"
        assert result.passed is False
        assert result.metrics["declared_calls"] == scenario.tool_count
        assert result.metrics["result_count"] < scenario.tool_count
        assert result.metrics["ordered_result_count"] < scenario.tool_count
        assert result.metrics["next_round_accepted"] is False
        assert result.metrics["completed_before_abort"] == scenario.abort_index


def test_completed_count_requires_exact_success_payload():
    exact = {"ok": True, "error_code": None, "message": "ok", "details": {}}
    near_miss = {"ok": True, "error_code": None, "message": "different", "details": {}}
    assert _count_completed_success_payloads([exact, near_miss, exact]) == 2


def test_production_abort_preserves_ordered_results_and_accepts_next_round():
    for scenario in frozen_abort_scenarios():
        result = production_abort_result(scenario)
        assert result.arm == "production_ordered"
        assert result.passed is True
        assert result.violations == ()
        assert result.metrics == {
            "declared_calls": scenario.tool_count,
            "result_count": scenario.tool_count,
            "ordered_result_count": scenario.tool_count,
            "next_round_accepted": True,
            "completed_before_abort": scenario.abort_index,
            "synthetic_after_abort": scenario.tool_count - scenario.abort_index - 1,
        }


def test_production_abort_payloads_are_exact_and_ids_are_unique():
    for scenario in frozen_abort_scenarios():
        messages = _captured_abort_tool_messages(scenario)
        ids = [str(message["tool_call_id"]) for message in messages]
        assert ids == [f"call-{index}" for index in range(scenario.tool_count)]
        assert len(ids) == len(set(ids)) == scenario.tool_count
        payloads = [json.loads(str(message["content"])) for message in messages]
        for payload in payloads[: scenario.abort_index]:
            assert payload == {
                "ok": True,
                "error_code": None,
                "message": "ok",
                "details": {},
            }
        assert payloads[scenario.abort_index] == {
            "ok": False,
            "error_code": "USER_ABORTED",
            "message": "工具执行被用户中止",
            "details": {},
        }
        for payload in payloads[scenario.abort_index + 1 :]:
            assert payload == {
                "ok": False,
                "error_code": "NOT_EXECUTED_AFTER_ABORT",
                "message": "同轮较早的工具调用被中止，本调用未执行",
                "details": {},
            }


def test_abort_runner_returns_baseline_then_production_for_each_frozen_case():
    for scenario in frozen_abort_scenarios():
        baseline, production = run_abort_scenario(scenario)
        assert baseline.scenario_id == production.scenario_id == scenario.scenario_id
        assert baseline.mechanism == production.mechanism == "abort_ordering"


def test_error_matrix_is_frozen_at_nine_cases_and_hashes_first_arguments():
    scenarios = frozen_error_scenarios()
    assert len(scenarios) == 9
    assert tuple(item.error_code for item in scenarios[::3]) == ERROR_CLASSES
    assert tuple(item.retry_shape for item in scenarios[:3]) == RETRY_SHAPES
    assert all(len(item.first_arguments_sha256) == 64 for item in scenarios)
    for offset in range(0, len(scenarios), 3):
        assert len({item.first_arguments_sha256 for item in scenarios[offset:offset + 3]}) == 1


def test_retry_classifier_has_only_observable_three_way_labels():
    first = {"path": "outside.txt", "token": "secret-value"}
    assert classify_retry(
        first_tool="read_file", first_arguments=first,
        retry_tool="read_file", retry_arguments=dict(first),
    ) == "SAME_FAILURE_RETRY"
    assert classify_retry(
        first_tool="read_file", first_arguments=first,
        retry_tool="read_file", retry_arguments={"path": "inside.txt"},
    ) == "CORRECTED_RETRY"
    assert classify_retry(
        first_tool="read_file", first_arguments=first,
        retry_tool="list_dir", retry_arguments={"path": "."},
    ) == "UNRELATED_ACTION"


def test_error_payload_contracts_are_stable_and_bounded():
    vague = vague_error_payload()
    assert vague == {"ok": False, "message": "工具执行失败"}
    result = ToolResult(
        tool_call_id="failure",
        name="read_file",
        ok=False,
        error_code="PATH_OUTSIDE_WORKSPACE",
        message="x" * 10_000,
        details={"path": "secret-value"},
    )
    structured = structured_error_payload(result)
    assert structured["ok"] is False
    assert structured["error_code"] == "PATH_OUTSIDE_WORKSPACE"
    assert isinstance(structured["message"], str)
    assert len(structured["message"]) <= 200
    assert "secret-value" not in json.dumps(structured, ensure_ascii=False)


def test_error_arms_produce_attributable_failure_from_observable_fields_only():
    for scenario in frozen_error_scenarios():
        vague, structured = run_error_scenario(scenario)
        assert vague.arm == "vague_error"
        assert structured.arm == "structured_error"
        assert vague.mechanism == structured.mechanism == "error_feedback"
        assert vague.metrics["attributable_failure"] is False
        assert structured.metrics["attributable_failure"] is True
        assert vague.metrics["retry_classification"] == structured.metrics["retry_classification"]
        assert structured.metrics["retry_classification"] in {
            "SAME_FAILURE_RETRY", "CORRECTED_RETRY", "UNRELATED_ACTION",
        }
        assert vague.passed is vague.metrics["attributable_failure"]
        assert structured.passed is structured.metrics["attributable_failure"]


def test_error_scenario_inputs_are_not_mutated_and_metrics_do_not_persist_secrets():
    scenario = frozen_error_scenarios()[0]
    before = scenario
    vague, structured = run_error_scenario(scenario)
    serialized = json.dumps(
        {"vague": vague.to_dict(), "structured": structured.to_dict()},
        ensure_ascii=False,
    )
    assert scenario == before
    assert "secret-value" not in serialized
    assert "first_arguments_sha256" in structured.metrics
    assert structured.metrics["first_arguments_utf8_bytes"] > 0


def test_wrong_structured_error_code_is_not_attributable():
    assert _attributable_failure(
        {"ok": False, "error_code": "COMMAND_DENIED"},
        expected_error_code="PATH_OUTSIDE_WORKSPACE",
    ) is False


def test_attribution_is_true_for_all_structured_and_false_for_all_vague():
    pairs = [run_error_scenario(item) for item in frozen_error_scenarios()]
    assert all(result.passed is True for _, result in pairs)
    assert all(result.metrics["attributable_failure"] is True for _, result in pairs)
    assert all(result.passed is False for result, _ in pairs)
    assert all(result.metrics["attributable_failure"] is False for result, _ in pairs)


def test_error_scenario_results_are_deterministic():
    for scenario in frozen_error_scenarios():
        assert run_error_scenario(scenario) == run_error_scenario(scenario)


def test_reliability_report_has_fixed_manifest_rows_and_safe_schema():
    from evals.run_reliability_study import (
        EXPECTED_RESULT_COUNT,
        generate_report,
        scenario_manifest_sha256,
    )

    report = generate_report()
    assert report["schema_version"] == 1
    assert report["study_id"] == STUDY_ID
    assert report["scenario_manifest_sha256"] == scenario_manifest_sha256()
    rows = report["results"]
    assert len(rows) == EXPECTED_RESULT_COUNT == 48
    assert [row["mechanism"] for row in rows].count("context") == 12
    assert [row["mechanism"] for row in rows].count("abort_ordering") == 18
    assert [row["mechanism"] for row in rows].count("error_feedback") == 18
    assert [(row["mechanism"], row["scenario_id"], row["arm"]) for row in rows] == sorted(
        (row["mechanism"], row["scenario_id"], row["arm"]) for row in rows
    )
    encoded = json.dumps(report, ensure_ascii=False).lower()
    for forbidden in (
        "provider", "model", "credential", "api_key", "authorization",
        "reasoning", "prompt", "username", "started_at", "timestamp",
    ):
        assert forbidden not in encoded


def test_reliability_report_embeds_hashed_manifest_and_context_summary():
    import evals.run_reliability_study as runner

    report = runner.generate_report()
    manifest = report["scenario_manifest"]
    assert manifest == runner.scenario_manifest()
    assert canonical_sha256(manifest) == report["scenario_manifest_sha256"]
    assert report["context_production_summary"] == {
        "production_rows": 6,
        "protocol_checked_rows": 4,
        "safe_stop_rows": 2,
        "protocol_violation_rows": 0,
        "h1_all_scenarios_success": False,
    }
    production = [
        row for row in report["results"]
        if row["mechanism"] == "context"
        and row["arm"] == "production_full_group"
    ]
    boundary = {
        row["scenario_id"]: row
        for row in production
        if row["scenario_id"] in {"C1_ONE_CALL_BOUNDARY", "C2_TWO_CALL_BOUNDARY"}
    }
    assert set(boundary) == {"C1_ONE_CALL_BOUNDARY", "C2_TWO_CALL_BOUNDARY"}
    for row in boundary.values():
        assert row["passed"] is False
        assert row["metrics"]["outcome"] == "CONTEXT_LIMIT"
        assert row["metrics"]["safe_stop"] is True
        assert row["metrics"]["protocol_checked"] is False
        assert row["metrics"]["shortfall_tokens"] > 0


def test_reliability_report_manifest_is_calculated_before_arm_execution(
    monkeypatch,
):
    import evals.run_reliability_study as runner

    observed = []

    real_execute = runner._execute_arms

    def fake_execute(manifest):
        observed.append(manifest)
        return real_execute(manifest)

    monkeypatch.setattr(runner, "_execute_arms", fake_execute)
    report = runner.generate_report()
    assert observed == [runner.scenario_manifest()]
    assert report["scenario_manifest_sha256"] == runner.scenario_manifest_sha256()
    assert len(report["results"]) == 48


def test_reliability_report_in_memory_generation_is_byte_identical():
    from evals.run_reliability_study import generate_report_bytes

    assert generate_report_bytes() == generate_report_bytes()
    assert b"started_at" not in generate_report_bytes()


def test_reliability_report_writer_refuses_overwrite_and_is_utf8_exclusive(
    tmp_path,
):
    from evals.run_reliability_study import (
        generate_report,
        write_report_exclusive,
    )

    path = tmp_path / "reliability-study.json"
    write_report_exclusive(path, generate_report())
    first = path.read_bytes()
    assert first.startswith(b"{")
    assert first.endswith(b"\n")
    assert json.loads(first.decode("utf-8"))["schema_version"] == 1
    try:
        write_report_exclusive(path, generate_report())
    except FileExistsError:
        pass
    else:
        raise AssertionError("report writer must refuse overwrite")


def test_reliability_report_writer_removes_its_partial_file_on_write_failure(
    tmp_path, monkeypatch,
):
    import evals.run_reliability_study as runner

    path = tmp_path / "partial-report.json"
    real_open = runner.Path.open

    class FailingWriter:
        def __init__(self, stream):
            self._stream = stream

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self._stream.close()

        def write(self, value):
            self._stream.write(value[:16])
            self._stream.flush()
            raise OSError("synthetic short write")

    def failing_open(target, *args, **kwargs):
        return FailingWriter(real_open(target, *args, **kwargs))

    monkeypatch.setattr(runner.Path, "open", failing_open)
    with pytest.raises(OSError, match="synthetic short write"):
        runner.write_report_exclusive(path, runner.generate_report())
    assert not path.exists()


def test_scenario_manifest_records_context_inputs_without_payloads():
    import evals.run_reliability_study as runner

    manifest = runner.scenario_manifest()
    contexts = [
        item for item in manifest["mechanisms"] if item["mechanism"] == "context"
    ]
    assert len(contexts) == 6
    required = {
        "scenario_id", "arms", "context_window", "max_output_tokens",
        "input_budget", "message_count", "messages_sha256", "tools_sha256",
    }
    frozen = {item.scenario_id: item for item in frozen_context_scenarios()}
    for entry in contexts:
        assert required <= set(entry)
        scenario = frozen[entry["scenario_id"]]
        assert entry["context_window"] == scenario.context_window
        assert entry["max_output_tokens"] == scenario.max_output_tokens
        assert entry["input_budget"] == scenario.context_window - scenario.max_output_tokens
        assert entry["message_count"] == len(scenario.messages)
        assert entry["messages_sha256"] == canonical_sha256(scenario.messages)
        assert entry["tools_sha256"] == canonical_sha256(scenario.tools)
        assert "x" * 180 not in json.dumps(entry, ensure_ascii=False)


def test_scenario_manifest_records_abort_and_error_inputs_without_secrets():
    import evals.run_reliability_study as runner

    manifest = runner.scenario_manifest()
    aborts = [item for item in manifest["mechanisms"] if item["mechanism"] == "abort_ordering"]
    errors = [item for item in manifest["mechanisms"] if item["mechanism"] == "error_feedback"]
    assert len(aborts) == 9
    assert all({"scenario_id", "arms", "tool_count", "abort_index"} <= set(item) for item in aborts)
    assert len(errors) == 9
    assert all(
        {
            "scenario_id", "arms", "error_code", "failed_tool",
            "first_arguments_sha256", "retry_shape", "retry_tool",
            "retry_arguments_sha256", "retry_arguments_utf8_bytes",
        } <= set(item)
        for item in errors
    )
    encoded = json.dumps(manifest, ensure_ascii=False)
    assert "synthetic-secret-value" not in encoded


def test_manifest_hash_changes_when_a_frozen_context_value_changes(monkeypatch):
    import dataclasses
    import evals.run_reliability_study as runner

    baseline_hash = runner.scenario_manifest_sha256()
    original = runner.frozen_context_scenarios()
    changed = (dataclasses.replace(original[0], context_window=original[0].context_window + 1), *original[1:])
    monkeypatch.setattr(runner, "frozen_context_scenarios", lambda: changed)
    assert runner.scenario_manifest_sha256() != baseline_hash


def test_error_manifest_commits_the_actual_retry_inputs(monkeypatch):
    import evals.reliability.error_study as error_study
    import evals.run_reliability_study as runner

    baseline_hash = runner.scenario_manifest_sha256()
    real_retry_arguments = error_study._retry_arguments

    def changed_retry_arguments(first_arguments, retry_shape):
        value = real_retry_arguments(first_arguments, retry_shape)
        if retry_shape == "corrected" and isinstance(value, dict):
            return {**value, "manifest_mutation": True}
        return value

    monkeypatch.setattr(error_study, "_retry_arguments", changed_retry_arguments)
    assert runner.scenario_manifest_sha256() != baseline_hash


def test_execution_rejects_manifest_when_frozen_inputs_change(monkeypatch):
    import dataclasses
    import evals.run_reliability_study as runner

    manifest = runner.scenario_manifest()
    original = runner.frozen_context_scenarios()
    changed = (dataclasses.replace(original[0], tools=()), *original[1:])
    monkeypatch.setattr(runner, "frozen_context_scenarios", lambda: changed)
    with pytest.raises(ValueError, match="scenario manifest changed"):
        runner._execute_arms(manifest)


@pytest.mark.parametrize(
    "value",
    [
        r"C:\Temp\report.json",
        r"D:\tmp\report.json",
        r"\\server\share\report.json",
        "/tmp/report.json",
        "/var/tmp/report.json",
        "/home/student/report.json",
        "/Users/student/report.json",
    ],
)
def test_report_validator_rejects_absolute_path_string_values(value):
    import evals.run_reliability_study as runner

    with pytest.raises(ValueError, match="绝对"):
        runner._validate_report_payload({"value": value})


def test_report_validator_accepts_relative_path_string_values():
    import evals.run_reliability_study as runner

    runner._validate_report_payload({"value": "tmp/report.json"})


def test_report_validator_checks_nested_raw_string_values():
    import evals.run_reliability_study as runner

    with pytest.raises(ValueError, match="绝对"):
        runner._validate_report_payload({"nested": ["/var/tmp/report.json"]})


@pytest.mark.parametrize(
    "value",
    [
        "sk-SYNTHETIC1234567890",
        "Bearer SYNTHETIC_TOKEN_123456",
        "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----",
    ],
)
def test_report_validator_rejects_nested_credential_shapes(value):
    import evals.run_reliability_study as runner

    with pytest.raises(ValueError, match="凭据"):
        runner._validate_report_payload({"nested": [{"value": value}]})
