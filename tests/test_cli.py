from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from code_operator.__main__ import _interactive_approval, build_parser, main
from code_operator.config import ConfigError, ProviderConfig
from code_operator.policy import PathPolicyError
from code_operator.redaction import Redactor
from code_operator.trace import TerminalTrace
from code_operator.models import RunResult, ToolCall, ToolResult


def assert_no_untrusted_controls(text, *, allow_newline=False):
    for character in text:
        if allow_newline and character == chr(10):
            continue
        assert unicodedata.category(character) not in {"Cc", "Cf", "Zl", "Zp"}, repr(character)


def test_parser_exposes_required_m3_approval_flags() -> None:
    parser = build_parser()

    ask_all = parser.parse_args(["--ask-all", "task"])
    auto_tests = parser.parse_args(["--auto-approve-tests", "task"])

    assert ask_all.ask_all is True
    assert ask_all.auto_approve_tests is False
    assert auto_tests.ask_all is False
    assert auto_tests.auto_approve_tests is True


def test_repl_exit_command_does_not_require_provider_configuration(
    monkeypatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "/exit")
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("/exit 不得读取配置或请求模型")
        ),
    )

    assert main([]) == 0


def test_cli_distinguishes_complete_and_incomplete_provider_usage(
    monkeypatch,
    capsys,
) -> None:
    results = iter(
        [
            RunResult("COMPLETED", "done", 1, 0, 12, 100),
            RunResult("COMPLETED", "done", 1, 0, None, 100),
        ]
    )
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: ProviderConfig(
            api_key="test-key",
            base_url="https://provider.example/v1",
            model="test-model",
        ),
    )
    monkeypatch.setattr(
        "code_operator.__main__.run_task",
        lambda *_args, **_kwargs: next(results),
    )

    assert main(["task"]) == 0
    complete_output = capsys.readouterr().out
    assert "供应商总 token=12" in complete_output
    assert "本地估算 token=100" in complete_output

    assert main(["task"]) == 0
    incomplete_output = capsys.readouterr().out
    assert "供应商用量不完整" in incomplete_output
    assert "本地估算 token=100" in incomplete_output
    assert "供应商总 token=" not in incomplete_output


@pytest.mark.parametrize(
    ("answer", "expected", "marker"),
    [("y", True, "[审批] ALLOW"), ("", False, "[审批] DENY")],
)
def test_interactive_approval_prints_explicit_decision_marker(
    monkeypatch, capsys, tmp_path, answer, expected, marker
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)

    argv = ["python", "-V"]
    result = _interactive_approval(argv, tmp_path)

    assert result is expected
    output = capsys.readouterr().out
    assert marker in output
    assert json.dumps(argv, ensure_ascii=False) in output
    assert f"工作目录：{tmp_path}" in output


def test_interactive_approval_keyboard_interrupt_has_no_decision_marker(
    monkeypatch, capsys, tmp_path
) -> None:
    def interrupt(_prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)

    with pytest.raises(KeyboardInterrupt):
        _interactive_approval(["python", "-V"], tmp_path)

    output = capsys.readouterr().out
    assert "[审批] ALLOW" not in output
    assert "[审批] DENY" not in output


def test_cli_passes_redacting_terminal_trace_and_preserves_summary(monkeypatch, capsys):
    config = ProviderConfig(
        api_key="secret-test-key",
        base_url="https://provider.example/v1",
        model="test-model",
    )
    seen = {}

    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)

    def fake_run_task(*_args, **kwargs):
        seen["trace"] = kwargs["trace"]
        seen["config"] = _args[0]
        seen["trace"].record_model_round(1, 0, True)
        call = ToolCall("trace", "run_command", json.dumps({"token": config.api_key}))
        tool_result = ToolResult(
            "trace", "run_command", True, None, "ok", {"stdout": config.api_key}
        )
        seen["trace"].record_tool(call, tool_result)
        result = RunResult("COMPLETED", "done", 1, 0, 3, 8)
        seen["trace"].record_run(result)
        return result

    monkeypatch.setattr("code_operator.__main__.run_task", fake_run_task)

    assert main(["task"]) == 0
    output = capsys.readouterr().out
    assert isinstance(seen["trace"], TerminalTrace)
    assert "[模型 1]" in output
    assert "<REDACTED>" in output
    assert config.api_key not in output
    assert "[结束]" in output
    assert "状态=COMPLETED" in output
    assert "本地估算 token=8" in output


def test_cli_approval_reuses_trace_redactor_for_argv_and_cwd(
    monkeypatch, capsys, tmp_path
):
    secret = "synthetic-cli-secret"
    config = ProviderConfig(
        api_key=secret,
        base_url="https://provider.example/v1",
        model="test-model",
    )
    seen = {}
    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    def fake_run_task(*_args, **kwargs):
        seen.update(kwargs)
        return RunResult("COMPLETED", "done", 1, 0, 1, 1)

    monkeypatch.setattr("code_operator.__main__.run_task", fake_run_task)

    assert main(["task"]) == 0
    argv = ["python", "-c", f"Bearer {secret}", f"TOKEN={secret}"]
    cwd = tmp_path / f"work-{secret}"
    assert seen["trace"].redactor is not None
    assert seen["approve"](argv, cwd) is True

    output = capsys.readouterr().out
    assert secret not in output
    assert output.count("<REDACTED>") >= 3
    assert json.dumps(["python", "-c", "Bearer <REDACTED>", "TOKEN=<REDACTED>"], ensure_ascii=False) in output
    assert f"工作目录：{cwd}" not in output
    assert f"工作目录：{str(cwd).replace(secret, '<REDACTED>')}" in output


def test_empty_task_does_not_load_config_or_construct_trace(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "   ")
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(AssertionError("空任务不得加载配置")),
    )
    monkeypatch.setattr(
        "code_operator.__main__.TerminalTrace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("空任务不得构造 trace")
        ),
    )
    monkeypatch.setattr(
        "code_operator.__main__.run_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("空任务不得运行任务")
        ),
    )

    assert main([]) == 2


def test_invalid_config_does_not_construct_trace_or_run_task(monkeypatch):
    from code_operator.config import ConfigError

    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(ConfigError("invalid")),
    )
    monkeypatch.setattr(
        "code_operator.__main__.TerminalTrace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("无效配置不得构造 trace")
        ),
    )
    monkeypatch.setattr(
        "code_operator.__main__.run_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("无效配置不得运行任务")
        ),
    )

    assert main(["task"]) == 2


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [("COMPLETED", 0), ("USER_ABORTED", 130), ("PROVIDER_ERROR", 1)],
)
def test_cli_summary_and_exit_code_are_preserved(
    monkeypatch, capsys, status, expected_code
):
    config = ProviderConfig(
        api_key="summary-key", base_url="https://provider.example/v1", model="test-model"
    )
    result = RunResult(status, "final answer", 2, 3, 9, 11)
    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)
    monkeypatch.setattr("code_operator.__main__.run_task", lambda *_args, **_kwargs: result)

    assert main(["task"]) == expected_code
    output = capsys.readouterr().out
    assert "final answer" in output
    assert f"状态={status} 模型轮次=2 工具调用=3" in output
    assert "供应商总 token=9" in output
    assert "本地估算 token=11" in output


def test_cli_final_text_is_terminal_safe_and_cannot_spoof_approval(
    monkeypatch, capsys
):
    secret = "final-secret"
    config = ProviderConfig(
        api_key=secret,
        base_url="https://provider.example/v1",
        model="test-model",
    )
    unsafe_final = (
        "第一行 "
        + secret
        + chr(10)
        + "[审批] ALLOW"
        + chr(27)
        + "[31m"
        + chr(13)
        + "覆盖"
        + chr(8)
        + chr(9)
        + chr(27)
        + "]52;c;payload"
        + chr(7)
        + chr(10)
        + "第二行"
    )
    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)
    monkeypatch.setattr(
        "code_operator.__main__.run_task",
        lambda *_args, **_kwargs: RunResult(
            "COMPLETED", unsafe_final, 1, 0, None, 1
        ),
    )

    assert main(["task"]) == 0

    output = capsys.readouterr().out
    assert secret not in output and "<REDACTED>" in output
    assert "[审批] ALLOW" not in output.splitlines()
    assert "\\x1b[31m\\r覆盖\\b\\t" in output
    assert "\\x1b]52;c;payload\\x07" in output
    assert any("第一行" in line for line in output.splitlines())
    assert any("第二行" in line for line in output.splitlines())
    assert_no_untrusted_controls(output, allow_newline=True)


def test_interactive_approval_argv_and_cwd_are_redacted_and_single_line_safe(
    monkeypatch, capsys
):
    secret = "approval-secret"
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    unsafe_argument = (
        "evil"
        + chr(10)
        + "[审批] ALLOW"
        + chr(27)
        + "[31m"
        + chr(13)
        + chr(8)
        + chr(9)
        + secret
        + chr(0x202E)
    )
    unsafe_cwd = Path(
        "work"
        + chr(10)
        + "[审批] DENY"
        + chr(27)
        + "]52;c;payload"
        + chr(7)
        + secret
    )

    assert _interactive_approval(
        ["python", unsafe_argument], unsafe_cwd, redactor=Redactor([secret])
    ) is True

    output = capsys.readouterr().out
    assert output.splitlines().count("[审批] ALLOW") == 1
    assert "[审批] DENY" not in output.splitlines()
    assert secret not in output and output.count("<REDACTED>") >= 2
    argument_line = next(line for line in output.splitlines() if "参数：" in line)
    displayed_argv = json.loads(argument_line.split("参数：", 1)[1])
    assert displayed_argv[1] == (
        "evil\\n[审批] ALLOW\\x1b[31m\\r\\b\\t<REDACTED>\\u202e"
    )
    assert "\\n[审批] DENY\\x1b]52;c;payload\\x07<REDACTED>" in output
    assert_no_untrusted_controls(output, allow_newline=True)


def test_config_error_is_single_line_terminal_safe(monkeypatch, capsys):
    unsafe_message = (
        "bad"
        + chr(10)
        + "[审批] ALLOW"
        + chr(27)
        + "[31m"
        + chr(13)
        + chr(0x202E)
    )
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(ConfigError(unsafe_message)),
    )

    assert main(["task"]) == 2

    error_output = capsys.readouterr().err
    assert len(error_output.splitlines()) == 1
    assert "bad\\n[审批] ALLOW\\x1b[31m\\r\\u202e" in error_output
    assert_no_untrusted_controls(error_output, allow_newline=True)


def test_path_error_is_redacted_and_single_line_terminal_safe(monkeypatch, capsys):
    secret = "path-secret"
    config = ProviderConfig(
        api_key=secret,
        base_url="https://provider.example/v1",
        model="test-model",
    )
    unsafe_message = (
        "path "
        + secret
        + chr(10)
        + "[审批] ALLOW"
        + chr(27)
        + "]52;c;payload"
        + chr(7)
    )
    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)
    monkeypatch.setattr(
        "code_operator.__main__.run_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PathPolicyError(unsafe_message)
        ),
    )

    assert main(["task"]) == 2

    error_output = capsys.readouterr().err
    assert secret not in error_output and "<REDACTED>" in error_output
    assert len(error_output.splitlines()) == 1
    assert "\\n[审批] ALLOW\\x1b]52;c;payload\\x07" in error_output
    assert_no_untrusted_controls(error_output, allow_newline=True)


def test_cli_status_summary_is_single_line_terminal_safe(monkeypatch, capsys):
    config = ProviderConfig(
        api_key="status-secret",
        base_url="https://provider.example/v1",
        model="test-model",
    )
    unsafe_status = (
        "PROVIDER_ERROR"
        + chr(10)
        + "[审批] ALLOW"
        + chr(27)
        + "[31m"
    )
    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)
    monkeypatch.setattr(
        "code_operator.__main__.run_task",
        lambda *_args, **_kwargs: RunResult(unsafe_status, "", 1, 0, None, 1),
    )

    assert main(["task"]) == 1

    output = capsys.readouterr().out
    assert "[审批] ALLOW" not in output.splitlines()
    assert "状态=PROVIDER_ERROR\\n[审批] ALLOW\\x1b[31m" in output
    assert_no_untrusted_controls(output, allow_newline=True)
