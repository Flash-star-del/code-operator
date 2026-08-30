import json
import unicodedata

import pytest

import code_operator.trace as trace_module
from code_operator.models import RunResult, ToolCall, ToolResult
from code_operator.redaction import Redactor
from code_operator.trace import MAX_ARGUMENT_SUMMARY_CHARS, MAX_TRACE_DETAIL_CHARS, TerminalTrace, _truncate


def trace_lines():
    lines = []
    return TerminalTrace(Redactor(["SECRET", "tok123"]), write=lines.append), lines

def call(name, raw="{}"):
    return ToolCall("id", name, raw)


def result(name, ok=True, error_code=None, details=None):
    return ToolResult("id", name, ok, error_code, "leaked SECRET message", details or {})


def assert_no_untrusted_controls(text, *, allow_newline=False):
    for character in text:
        if allow_newline and character == chr(10):
            continue
        assert unicodedata.category(character) not in {"Cc", "Cf", "Zl", "Zp"}, repr(character)


def test_terminal_safe_text_converts_objects_and_escapes_unicode_controls():
    terminal_safe_text = getattr(trace_module, "terminal_safe_text", None)
    assert terminal_safe_text is not None, "terminal_safe_text is missing"

    class DisplayValue:
        def __str__(self):
            return (
                "中"
                + chr(10)
                + chr(13)
                + chr(9)
                + chr(8)
                + chr(27)
                + chr(7)
                + chr(0x9B)
                + chr(0x2028)
                + chr(0x2029)
                + chr(0x202E)
                + chr(0xE0001)
            )

    single_line = terminal_safe_text(DisplayValue(), multiline=False)
    assert single_line == (
        "中\\n\\r\\t\\b\\x1b\\x07\\x9b"
        "\\u2028\\u2029\\u202e\\U000e0001"
    )
    assert_no_untrusted_controls(single_line)

    multi_line = terminal_safe_text(DisplayValue(), multiline=True)
    assert multi_line.startswith("中" + chr(10) + "\\r\\t\\b")
    assert multi_line.count(chr(10)) == 1
    assert "\\u2028\\u2029" in multi_line
    assert_no_untrusted_controls(multi_line, allow_newline=True)


def test_trace_budgets_are_locked():
    assert MAX_ARGUMENT_SUMMARY_CHARS == 500
    assert MAX_TRACE_DETAIL_CHARS == 4000


def test_model_round_and_run_have_stable_format():
    trace, lines = trace_lines()
    trace.record_model_round(2, 3, True)
    trace.record_model_round(3, 0, False)
    trace.record_run(RunResult("success", "final", 2, 3, 10, 20))
    assert lines == ["[模型 2] tool_calls=3 usage=available", "[模型 3] tool_calls=0 usage=unavailable", "[结束] stop_reason=success usage=available"]


def test_run_usage_unavailable_and_status_redacted():
    trace, lines = trace_lines()
    trace.record_run(RunResult("SECRET-stop", "", 0, 0, None, 0))
    assert lines == ["[结束] stop_reason=<REDACTED>-stop usage=unavailable"]


def test_tool_arguments_are_sorted_redacted_and_file_content_omitted():
    trace, lines = trace_lines()
    raw = json.dumps({"z": "tok123", "content": "abc", "nested": {"old_text": "SECRET", "API_SECRET": "tok123"}, "API_TOKEN": "SECRET", "a": 1})
    trace.record_tool(call("write_file", raw), result("write_file", details={"diff": "ok"}))
    assert '"a":1' in lines[0] and '"z":"<REDACTED>"' in lines[0]
    assert "API_SECRET" not in lines[0] and "API_<REDACTED>\":\"<REDACTED>" in lines[0]
    assert 'content":"<omitted; chars=3>"' in lines[0] and "SECRET" not in lines[0]


def test_invalid_or_non_object_json_never_echoes_raw():
    trace, lines = trace_lines()
    raw = '{"bad":"BROKEN_PRIVATE_SENTINEL"'
    trace.record_tool(call("x", raw), result("x"))
    trace.record_tool(call("x", '["SECRET"]'), result("x"))
    assert "SECRET" not in "\n".join(lines) and raw not in "\n".join(lines)
    assert "invalid_json" in lines[0] and "json_type=array" in lines[2]


@pytest.mark.parametrize("name", ["write_file", "edit_file"])
def test_successful_file_diff_is_shown_and_truncated(name):
    trace, lines = trace_lines()
    diff = "HEAD_OF_DIFF" + "tok123" + "A" * (MAX_TRACE_DETAIL_CHARS + 100) + "TAIL_OF_DIFF"
    trace.record_tool(call(name), result(name, details={"diff": diff}))
    assert lines[1].startswith("[结果] "+name+" ok=true")
    assert "HEAD_OF_DIFF" in lines[2] and "TAIL_OF_DIFF" in lines[2]
    assert "tok123" not in lines[2] and "<REDACTED>" in lines[2]
    assert "... <truncated; original_chars=" in lines[2]
    assert len(lines[2]) <= MAX_TRACE_DETAIL_CHARS


def test_failed_file_does_not_print_message_or_details():
    trace, lines = trace_lines()
    trace.record_tool(call("write_file"), result("write_file", False, "NOPE", {"diff": "SECRET"}))
    assert len(lines) == 2 and "SECRET" not in "\n".join(lines)


def test_run_command_displays_exit_timeout_and_streams_redacted():
    trace, lines = trace_lines()
    trace.record_tool(call("run_command"), result("run_command", False, "COMMAND_FAILED", {
        "exit_code": 7, "timed_out": False, "stdout": "out SECRET", "stderr": "",}))
    assert lines[1] == "[结果] run_command ok=false error_code=COMMAND_FAILED"
    assert lines[2] == "[命令] exit_code=7 timed_out=false"
    assert lines[3] == "  stdout:" and lines[4] == "  out <REDACTED>"
    assert lines[5] == "  stderr:" and lines[6] == "  <empty>"


def test_run_command_missing_fields_are_unavailable():
    trace, lines = trace_lines()
    trace.record_tool(call("run_command"), result("run_command", True, details={}))
    assert lines[2:] == ["[命令] exit_code=- timed_out=-", "  stdout:", "  <unavailable>", "  stderr:", "  <unavailable>"]


@pytest.mark.parametrize("name", ["read_file", "grep", "list_dir"])
def test_read_grep_list_payload_is_not_displayed(name):
    trace, lines = trace_lines()
    trace.record_tool(call(name), result(name, details={"content": "SECRET", "entries": ["SECRET"]}))
    assert len(lines) == 2 and "SECRET" not in "\n".join(lines)


def test_unknown_tool_has_minimal_format():
    trace, lines = trace_lines()
    trace.record_tool(call("future_tool", '{"token":"SECRET"}'), result("future_tool", False, "UNKNOWN"))
    assert lines == ["[工具] future_tool 参数={\"token\":\"<REDACTED>\"}", "[结果] future_tool ok=false error_code=UNKNOWN"]


def test_sink_failure_is_attempted_only_once_and_then_silent():
    attempts = []
    def sink(_):
        attempts.append(1)
        raise RuntimeError("sink")
    trace = TerminalTrace(Redactor([]), write=sink)
    trace.record_model_round(1, 0, False)
    trace.record_model_round(2, 0, False)
    assert trace.output_failed is True and len(attempts) == 1


def test_argument_summary_is_bounded():
    trace, lines = trace_lines()
    trace.record_tool(call("x", json.dumps({"value": "x" * 1000})), result("x"))
    assert len(lines[0]) <= MAX_ARGUMENT_SUMMARY_CHARS + len("[工具] x 参数=")
    assert "original_chars=" in lines[0]

def test_new_text_nested_is_omitted_and_summary_keeps_distinct_head_tail():
    trace, lines = trace_lines()
    raw = json.dumps({"a": "HEAD", "new_text": "SECRET", "z": "TAIL", "pad": "x" * 700})
    trace.record_tool(call("edit_file", raw), result("edit_file"))
    assert "<omitted; chars=6>" in lines[0] and "SECRET" not in lines[0]
    assert '"a":"HEAD"' in lines[0] and '"z":"TAIL"' in lines[0]
    assert "original_chars=" in lines[0]

@pytest.mark.parametrize("name", ["write_file", "edit_file"])
def test_failed_file_tools_never_show_details(name):
    trace, lines = trace_lines()
    trace.record_tool(call(name), result(name, False, "SECRET-CODE", {"diff": "SECRET"}))
    assert len(lines) == 2 and "SECRET" not in "\n".join(lines)

@pytest.mark.parametrize("details", [
    {"exit_code": 0, "timed_out": False, "stdout": "ok SECRET", "stderr": "err"},
    {"exit_code": 2, "timed_out": False, "stdout": "out", "stderr": "bad SECRET"},
    {"exit_code": None, "timed_out": True, "stdout": "late", "stderr": ""},
    {"exit_code": "bad", "timed_out": "bad", "stdout": 3, "stderr": None},
])
def test_run_command_matrix(details):
    trace, lines = trace_lines()
    trace.record_tool(call("run_command"), result("run_command", details=details))
    expected_exit = details["exit_code"] if isinstance(details["exit_code"], int) and not isinstance(details["exit_code"], bool) else "-"
    expected_timeout = "true" if details["timed_out"] is True else "false" if details["timed_out"] is False else "-"
    assert lines[2] == f"[命令] exit_code={expected_exit} timed_out={expected_timeout}"
    assert lines[3] == "  stdout:" and lines[5] == "  stderr:"
    if details["stdout"] == 3 and details["stderr"] is None:
        assert lines[4] == "  <unavailable>" and lines[6] == "  <unavailable>"
    assert "SECRET" not in "\n".join(lines)

def test_run_command_long_streams_keep_both_ends_and_marker():
    trace, lines = trace_lines()
    out = "OUT_HEAD" + "o" * 5000 + "OUT_TAIL SECRET"
    err = "ERR_HEAD" + "e" * 5000 + "ERR_TAIL SECRET"
    trace.record_tool(call("run_command"), result("run_command", details={"exit_code": 0, "timed_out": False, "stdout": out, "stderr": err}))
    output = "\n".join(lines)
    assert "OUT_HEAD" in output and "OUT_TAIL" in output
    assert "ERR_HEAD" in output and "ERR_TAIL" in output
    assert output.count("original_chars=") >= 2

def test_unregistered_sensitive_keys_are_redacted_without_killing_tokenizer():
    trace = TerminalTrace(Redactor([]), write=(lines := []).append)
    raw = json.dumps({"nested": {"API_KEY": "KEY_SENTINEL", "ACCESS_TOKEN": "TOKEN_SENTINEL", "CLIENT_SECRET": "CLIENT_SENTINEL", "PASSWORD": "PASS_SENTINEL", "tokenizer": "keep"}})
    trace.record_tool(call("future_tool", raw), result("future_tool"))
    assert all(sentinel not in lines[0] for sentinel in ("KEY_SENTINEL", "TOKEN_SENTINEL", "CLIENT_SENTINEL", "PASS_SENTINEL"))
    assert '"tokenizer":"keep"' in lines[0]

def test_common_sensitive_suffix_keys_are_redacted_with_clear_boundary():
    trace = TerminalTrace(Redactor([]), write=(lines := []).append)
    raw = json.dumps({"API_TOKEN": "API_TOKEN_SENTINEL", "API_SECRET": "API_SECRET_SENTINEL", "GH_TOKEN": "GH_TOKEN_SENTINEL", "DB_PASSWORD": "DB_PASSWORD_SENTINEL", "tokenizer": "keep"})
    trace.record_tool(call("future_tool", raw), result("future_tool"))
    assert all(s not in lines[0] for s in ("API_TOKEN_SENTINEL", "API_SECRET_SENTINEL", "GH_TOKEN_SENTINEL", "DB_PASSWORD_SENTINEL"))
    assert '"tokenizer":"keep"' in lines[0]

def test_redacted_tool_name_still_uses_original_control_flow():
    trace = TerminalTrace(Redactor(["run"]), write=(lines := []).append)
    trace.record_tool(call("run_command"), result("run_command", details={"exit_code": 0, "timed_out": False, "stdout": "ok", "stderr": "err"}))
    assert lines[0].startswith("[工具] <REDACTED>_command")
    assert "[命令] exit_code=0 timed_out=false" in lines
    assert "  stdout:" in lines and "  ok" in lines

@pytest.mark.parametrize("raw", [
    '{"value": ' + "9" * 5000 + '}',
    '{"BROKEN_PRIVATE_SENTINEL":' + ("{" * 1100) + '1' + ("}" * 1100) + '}',
])
def test_argument_summary_handles_pathological_json_without_echo(raw):
    trace = TerminalTrace(Redactor([]), write=(lines := []).append)
    trace.record_tool(call("future_tool", raw), result("future_tool"))
    assert lines and "BROKEN_PRIVATE_SENTINEL" not in lines[0] and len(lines[0]) < 700

@pytest.mark.parametrize("limit", [0, 1, 10, 3999, 4000, 4001])
def test_truncate_never_exceeds_budget_or_reveals_tail_on_zero(limit):
    text = "HEAD_SENTINEL" + ("x" * 5000) + "TAIL_SENTINEL"
    output = _truncate(text, limit)
    assert len(output) <= limit
    if limit <= 10:
        assert "TAIL_SENTINEL" not in output


@pytest.mark.parametrize("budget", [MAX_ARGUMENT_SUMMARY_CHARS, MAX_TRACE_DETAIL_CHARS])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_truncate_boundary_has_marker_only_when_input_exceeds_budget(budget, delta):
    source_length = budget + delta
    text = "H" + ("x" * max(0, source_length - 2)) + "T" if source_length >= 2 else "x" * source_length

    output = _truncate(text, budget)

    expected_length = source_length if source_length <= budget else budget
    assert len(output) == expected_length
    assert ("... <truncated; original_chars=" in output) is (source_length > budget)


@pytest.mark.parametrize("summary_length", [
    MAX_ARGUMENT_SUMMARY_CHARS - 1,
    MAX_ARGUMENT_SUMMARY_CHARS,
    MAX_ARGUMENT_SUMMARY_CHARS + 1,
])
def test_valid_json_argument_summary_boundary_is_publicly_bounded(summary_length):
    base = json.dumps({"value": ""}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value_length = summary_length - len(base)
    raw = json.dumps({"value": "x" * value_length}, ensure_ascii=False)
    trace, lines = trace_lines()

    trace.record_tool(call("future_tool", raw), result("future_tool"))

    assert '参数={"value":"' in lines[0]
    assert "json_type=" not in lines[0] and "invalid_json" not in lines[0]
    assert ("... <truncated; original_chars=" in lines[0]) is (
        summary_length > MAX_ARGUMENT_SUMMARY_CHARS
    )


@pytest.mark.parametrize("field", ["diff", "stdout", "stderr"])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_public_trace_detail_boundary_marks_only_overlong_output(field, delta):
    source_length = MAX_TRACE_DETAIL_CHARS + delta
    value = "H" + ("x" * max(0, source_length - 2)) + "T"
    details = {"diff": value} if field == "diff" else {
        "exit_code": 0,
        "timed_out": False,
        "stdout": value if field == "stdout" else "ok",
        "stderr": value if field == "stderr" else "ok",
    }
    tool_name = "write_file" if field == "diff" else "run_command"
    trace, lines = trace_lines()

    trace.record_tool(call(tool_name), result(tool_name, details=details))

    output = "\n".join(lines)
    assert ("... <truncated; original_chars=" in output) is (delta > 0)


def test_chinese_path_argument_summary_is_complete_without_payload_or_garbled_text():
    path = "资料/" + ("南京大学软件学院考核/阶段性材料/" * 26) + "结果.txt"
    canonical = json.dumps(
        {"path": path}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert 400 <= len(canonical) <= MAX_ARGUMENT_SUMMARY_CHARS
    payload = "PAYLOAD_SENTINEL：不应出现在追踪中的文件内容"
    trace = TerminalTrace(Redactor([]), write=(lines := []).append)

    trace.record_tool(
        call("read_file", json.dumps({"path": path}, ensure_ascii=False)),
        result("read_file", details={"path": path, "content": payload}),
    )

    output = "\n".join(lines)
    assert path in output
    assert payload not in output
    assert "\ufffd" not in output
    assert "... <truncated" not in output


def test_tool_name_error_code_and_run_status_are_single_line_terminal_safe():
    trace = TerminalTrace(Redactor([]), write=(lines := []).append)
    unsafe_name = (
        "evil"
        + chr(10)
        + "[审批] ALLOW"
        + chr(27)
        + "[31m"
        + chr(0x85)
        + chr(0x202E)
    )
    unsafe_error = "ERR" + chr(13) + "CODE" + chr(9) + chr(8) + chr(7)
    unsafe_status = "FAILED" + chr(10) + "[审批] ALLOW" + chr(0x9B) + "31m"

    trace.record_tool(
        call(unsafe_name), result(unsafe_name, False, unsafe_error)
    )
    trace.record_run(RunResult(unsafe_status, "", 0, 0, None, 0))

    rendered = chr(10).join(lines)
    assert chr(10) + "[审批] ALLOW" not in rendered
    assert "evil\\n[审批] ALLOW\\x1b[31m\\x85\\u202e" in rendered
    assert "ERR\\rCODE\\t\\b\\x07" in rendered
    assert "FAILED\\n[审批] ALLOW\\x9b31m" in rendered
    for line in lines:
        assert_no_untrusted_controls(line)


def test_argument_summary_is_single_line_and_escapes_format_characters():
    trace = TerminalTrace(Redactor([]), write=(lines := []).append)
    raw = json.dumps(
        {chr(0x202E) + "key": "visible", "z": "x" * 700},
        ensure_ascii=False,
    )

    trace.record_tool(call("future_tool", raw), result("future_tool"))

    assert "\\u202ekey" in lines[0]
    assert_no_untrusted_controls(lines[0])


def test_diff_stdout_and_stderr_escape_terminal_controls_but_keep_line_feeds():
    unsafe = (
        "第一行"
        + chr(10)
        + "red"
        + chr(27)
        + "[31m"
        + chr(13)
        + "return"
        + chr(8)
        + "back"
        + chr(9)
        + "tab"
        + chr(27)
        + "]52;c;payload"
        + chr(7)
        + chr(10)
        + "末行"
    )
    trace = TerminalTrace(Redactor([]), write=(lines := []).append)

    trace.record_tool(
        call("write_file"), result("write_file", details={"diff": unsafe})
    )
    trace.record_tool(
        call("run_command"),
        result(
            "run_command",
            details={
                "exit_code": 0,
                "timed_out": False,
                "stdout": unsafe,
                "stderr": unsafe,
            },
        ),
    )

    rendered = chr(10).join(lines)
    assert "第一行" + chr(10) + "red" in rendered
    assert "\\x1b[31m\\rreturn\\bback\\ttab" in rendered
    assert "\\x1b]52;c;payload\\x07" in rendered
    assert rendered.count("末行") == 3
    assert_no_untrusted_controls(rendered, allow_newline=True)
