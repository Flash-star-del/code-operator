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
from code_operator.journal import UndoResult


def completed(text: str = "done") -> RunResult:
    return RunResult("COMPLETED", text, 1, 0, 2, 3)


class FakeSession:
    def __init__(
        self,
        *,
        results: list[RunResult | BaseException] | None = None,
        undo_results: list[UndoResult] | None = None,
        undo_depth: int = 0,
        secret: str = "fake-secret",
        close_error: BaseException | None = None,
    ) -> None:
        self.results = list(results or [])
        self.undo_results = list(undo_results or [])
        self.undo_depth = undo_depth
        self.tasks: list[str] = []
        self.reset_calls = 0
        self.close_calls = 0
        self._redactor = Redactor([secret])
        self.close_error = close_error

    def run(self, task: str) -> RunResult:
        self.tasks.append(task)
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def undo(self) -> UndoResult:
        result = self.undo_results.pop(0)
        if result.ok:
            self.undo_depth = result.remaining
        return result

    def reset(self) -> None:
        self.reset_calls += 1
        self.undo_depth = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def scripted_input(*items: str | BaseException):
    values = iter(items)

    def read(_prompt: str) -> str:
        item = next(values)
        if isinstance(item, BaseException):
            raise item
        return item

    return read


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


def test_no_argument_cli_runs_two_tasks_in_one_session_then_exits(
    monkeypatch, capsys
) -> None:
    fake = FakeSession(results=[completed("one"), completed("two")])
    monkeypatch.setattr(
        "builtins.input", scripted_input("first", "continue", "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.tasks == ["first", "continue"]
    assert fake.close_calls == 1
    output = capsys.readouterr().out
    assert "one" in output and "two" in output


def test_empty_and_unknown_local_command_do_not_load_config(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "builtins.input", scripted_input("", "   ", "/history", "/undo extra", "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得加载配置")),
    )

    assert main([]) == 0
    output = capsys.readouterr().out
    assert output.count("任务不能为空") == 2
    assert output.count("未知本地命令") == 2


def test_slash_word_inside_natural_language_is_sent_to_model(
    monkeypatch,
) -> None:
    fake = FakeSession(results=[completed()])
    monkeypatch.setattr(
        "builtins.input", scripted_input("请解释 /undo 的边界", "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.tasks == ["请解释 /undo 的边界"]
    assert fake.close_calls == 1


def test_local_commands_are_exact_after_strip_and_casefold(monkeypatch) -> None:
    fake = FakeSession(
        results=[completed()],
        undo_results=[UndoResult(False, "UNDO_EMPTY", "empty")],
    )
    monkeypatch.setattr(
        "builtins.input", scripted_input("normal", "  /UnDo  ", "  /ExIt  ")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.tasks == ["normal"]
    assert fake.close_calls == 1


def test_undo_before_initialization_is_local_and_configuration_free(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr("builtins.input", scripted_input("/undo", "/exit"))
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得加载配置")),
    )

    assert main([]) == 0
    output = capsys.readouterr().out
    assert "[撤销] ERROR" in output
    assert "error_code=UNDO_EMPTY" in output


def test_undo_result_is_redacted_terminal_safe_and_diff_only_is_multiline(
    monkeypatch, capsys
) -> None:
    secret = "current-api-key"
    controls = "\x1b]52;c;x\x07\r\b\u202e"
    fake = FakeSession(
        results=[completed()],
        undo_results=[
            UndoResult(
                True,
                None,
                f"restored {secret} Bearer hidden {controls}\n[回答] spoof",
                path=f"dir/{secret}/{controls}\n[撤销] ERROR.py",
                source_tool=f"write_file{controls}\n[审批] ALLOW",
                diff=(
                    f"--- {secret}\n+++ Bearer hidden\n-{controls}"
                    "\n+[撤销] OK forged"
                ),
                remaining=0,
            )
        ],
        undo_depth=1,
        secret=secret,
    )
    monkeypatch.setattr(
        "builtins.input", scripted_input("normal", "/undo", "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    output = capsys.readouterr().out
    lines = output.splitlines()
    assert lines.count("[撤销] OK") == 1
    assert secret not in output
    assert "Bearer hidden" not in output
    assert "<REDACTED>" in output
    assert "tool=write_file" in output
    assert "path=dir/" in output
    assert "remaining=0" in output
    assert r"\x1b]52;c;x\x07\r\b\u202e" in output
    assert "[审批] ALLOW" not in lines
    assert "[回答] spoof" not in lines
    assert "[撤销] ERROR.py" not in lines
    assert "[撤销] OK forged" not in lines
    assert_no_untrusted_controls(output, allow_newline=True)


def test_failed_undo_renders_stable_error_without_model_call(
    monkeypatch, capsys
) -> None:
    fake = FakeSession(
        results=[completed()],
        undo_results=[
            UndoResult(
                False,
                "UNDO_CONFLICT\n[撤销] OK",
                "changed\n[审批] ALLOW",
                path="bad\npath",
                source_tool="edit_file\x1b[31m",
                remaining=1,
            )
        ],
        undo_depth=1,
    )
    monkeypatch.setattr(
        "builtins.input", scripted_input("normal", "/undo", "/exit", "y")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    output = capsys.readouterr().out
    assert output.splitlines().count("[撤销] ERROR") == 1
    assert output.splitlines().count("[撤销] OK") == 0
    assert "UNDO_CONFLICT\\n[撤销] OK" in output
    assert "changed\\n[审批] ALLOW" in output
    assert fake.tasks == ["normal"]


@pytest.mark.parametrize("answer", ["", "n", "later"])
def test_new_with_undo_records_defaults_to_refusal_and_continues(
    monkeypatch, capsys, answer
) -> None:
    fake = FakeSession(results=[completed("one"), completed("two")], undo_depth=3)
    monkeypatch.setattr(
        "builtins.input",
        scripted_input("first", "/new", answer, "continue", "/exit", "允许"),
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.reset_calls == 0
    assert fake.tasks == ["first", "continue"]
    assert "丢失 3 条撤销记录" in capsys.readouterr().out


@pytest.mark.parametrize("answer", [" y ", "YES", "允许"])
def test_new_confirmation_resets_without_undoing_files(
    monkeypatch, answer
) -> None:
    fake = FakeSession(results=[completed()], undo_depth=2)
    monkeypatch.setattr(
        "builtins.input", scripted_input("first", "/new", answer, "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.reset_calls == 1
    assert fake.undo_results == []
    assert fake.close_calls == 1


def test_new_at_zero_depth_does_not_confirm(monkeypatch) -> None:
    fake = FakeSession(results=[completed()], undo_depth=0)
    monkeypatch.setattr(
        "builtins.input", scripted_input("first", "/new", "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.reset_calls == 1


def test_exit_with_undo_records_defaults_to_refusal_then_can_confirm(
    monkeypatch, capsys
) -> None:
    fake = FakeSession(results=[completed("one"), completed("two")], undo_depth=2)
    monkeypatch.setattr(
        "builtins.input",
        scripted_input("first", "/exit", "", "continue", "/exit", "yes"),
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.tasks == ["first", "continue"]
    assert fake.close_calls == 1
    output = capsys.readouterr().out
    assert "2 条撤销记录将永久消失" in output
    assert "文件保持当前状态" in output


def test_eof_with_undo_records_warns_and_exits_without_reset_or_undo(
    monkeypatch, capsys
) -> None:
    fake = FakeSession(results=[completed()], undo_depth=4)
    monkeypatch.setattr("builtins.input", scripted_input("first", EOFError()))
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.close_calls == 1
    assert fake.reset_calls == 0
    assert fake.undo_results == []
    output = capsys.readouterr().out
    assert "4 条撤销记录将永久消失" in output
    assert "文件保持当前状态" in output


def test_eof_before_initialization_is_configuration_free(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", scripted_input(EOFError()))
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得加载配置")),
    )

    assert main([]) == 0


def test_keyboard_interrupt_at_prompt_only_cancels_input(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "builtins.input", scripted_input(KeyboardInterrupt(), "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得加载配置")),
    )

    assert main([]) == 0
    assert "已取消当前输入" in capsys.readouterr().out


def test_keyboard_interrupt_during_undo_reports_cancellation_and_continues(
    monkeypatch, capsys
) -> None:
    fake = FakeSession(results=[completed()], undo_depth=1)

    def interrupted_undo():
        raise KeyboardInterrupt

    fake.undo = interrupted_undo  # type: ignore[method-assign]
    monkeypatch.setattr(
        "builtins.input", scripted_input("first", "/undo", "/exit", "yes")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.close_calls == 1
    assert "撤销操作已取消" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["USER_ABORTED", "PROVIDER_ERROR"])
def test_interactive_task_failure_is_rendered_and_loop_continues(
    monkeypatch, status
) -> None:
    fake = FakeSession(
        results=[RunResult(status, "failed", 1, 0, None, 2), completed("ok")]
    )
    monkeypatch.setattr(
        "builtins.input", scripted_input("first", "continue", "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.tasks == ["first", "continue"]
    assert fake.close_calls == 1


def test_interactive_task_keyboard_interrupt_renders_aborted_and_continues(
    monkeypatch, capsys
) -> None:
    fake = FakeSession(results=[KeyboardInterrupt(), completed("recovered")])
    monkeypatch.setattr(
        "builtins.input", scripted_input("first", "continue", "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    assert fake.tasks == ["first", "continue"]
    assert fake.close_calls == 1
    output = capsys.readouterr().out
    assert "状态=USER_ABORTED" in output
    assert "状态=COMPLETED" in output
    assert "recovered" in output


@pytest.mark.parametrize("error", [ConfigError("bad config"), PathPolicyError("bad path")])
def test_interactive_session_initialization_failure_is_exit_two_and_safe(
    monkeypatch, capsys, error
) -> None:
    monkeypatch.setattr("builtins.input", scripted_input("task"))
    monkeypatch.setattr(
        "code_operator.__main__._create_session",
        lambda *_a, **_k: (_ for _ in ()).throw(error),
    )

    assert main([]) == 2
    assert "bad" in capsys.readouterr().err


def test_unexpected_interactive_cli_error_returns_one_and_closes_once(
    monkeypatch, capsys
) -> None:
    fake = FakeSession(results=[RuntimeError("boom\x1b[31m")])
    monkeypatch.setattr("builtins.input", scripted_input("task"))
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 1
    assert fake.close_calls == 1
    assert r"boom\x1b[31m" in capsys.readouterr().err


def test_close_failure_does_not_replace_existing_cli_error(monkeypatch, capsys) -> None:
    secret = "fake-secret"
    fake = FakeSession(
        results=[RuntimeError("primary")],
        close_error=OSError(f"close failed {secret}\x1b[31m"),
        secret=secret,
    )
    monkeypatch.setattr("builtins.input", scripted_input("task"))
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 1
    assert fake.close_calls == 1
    error = capsys.readouterr().err
    assert "primary" in error
    assert secret not in error
    assert r"close failed <REDACTED>\x1b[31m" in error


def test_close_failure_on_normal_exit_returns_one_and_closes_once(
    monkeypatch, capsys
) -> None:
    fake = FakeSession(
        results=[completed()], close_error=OSError("close failed\x1b[31m")
    )
    monkeypatch.setattr("builtins.input", scripted_input("task", "/exit"))
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 1
    assert fake.close_calls == 1
    assert r"close failed\x1b[31m" in capsys.readouterr().err


def test_keyboard_interrupt_from_close_is_not_swallowed(monkeypatch) -> None:
    fake = FakeSession(results=[completed()], close_error=KeyboardInterrupt())
    monkeypatch.setattr("builtins.input", scripted_input("task", "/exit"))
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    with pytest.raises(KeyboardInterrupt):
        main([])

    assert fake.close_calls == 1


def test_unexpected_session_factory_failure_is_exit_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", scripted_input("task"))
    monkeypatch.setattr(
        "code_operator.__main__._create_session",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("factory failed")),
    )

    assert main([]) == 1
    assert "factory failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("error_type", "expected_code"),
    [
        (RuntimeError, 1),
        (AssertionError, 1),
        (ConfigError, 2),
        (PathPolicyError, 2),
        (OSError, 2),
    ],
)
def test_real_session_construction_failure_is_classified_and_redacts_loaded_key(
    monkeypatch, capsys, error_type, expected_code
) -> None:
    secret = "construction-secret"
    config = ProviderConfig(
        api_key=secret,
        base_url="https://provider.example/v1",
        model="test-model",
    )
    monkeypatch.setattr("builtins.input", scripted_input("task"))
    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)
    monkeypatch.setattr(
        "code_operator.__main__.AgentSession",
        lambda *_a, **_k: (_ for _ in ()).throw(
            error_type(f"failed {secret}\x1b[31m")
        ),
    )

    assert main([]) == expected_code
    error = capsys.readouterr().err
    assert secret not in error
    assert r"failed <REDACTED>\x1b[31m" in error


def test_terminal_trace_runtime_failure_is_exit_one_and_redacts_loaded_key(
    monkeypatch, capsys
) -> None:
    secret = "trace-construction-secret"
    config = ProviderConfig(
        api_key=secret,
        base_url="https://provider.example/v1",
        model="test-model",
    )
    monkeypatch.setattr("builtins.input", scripted_input("task"))
    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)
    monkeypatch.setattr(
        "code_operator.__main__.TerminalTrace",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError(f"trace failed {secret}\x1b[31m")
        ),
    )

    assert main([]) == 1
    error = capsys.readouterr().err
    assert secret not in error
    assert r"trace failed <REDACTED>\x1b[31m" in error


@pytest.mark.parametrize("command", ["/undo", "/new"])
def test_positional_session_only_commands_do_not_load_configuration(
    monkeypatch, capsys, command
) -> None:
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得加载配置")),
    )

    assert main([command]) == 2
    assert "仅交互模式" in capsys.readouterr().err


def test_positional_exit_is_configuration_free(monkeypatch) -> None:
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得加载配置")),
    )

    assert main(["  /ExIt  "]) == 0


def test_unknown_local_command_is_terminal_safe_and_redacts_bearer(
    monkeypatch, capsys
) -> None:
    unsafe = "/history Bearer secret\n[撤销] OK\x1b]52;c;x\x07\r\b\u202e"
    monkeypatch.setattr("builtins.input", scripted_input(unsafe, "/exit"))

    assert main([]) == 0
    output = capsys.readouterr().out
    assert "Bearer secret" not in output
    assert "未知本地命令。支持：/undo、/new、/exit。" in output
    assert output.splitlines().count("[撤销] OK") == 0
    assert_no_untrusted_controls(output, allow_newline=True)


def test_unknown_local_command_uses_initialized_session_redactor(
    monkeypatch, capsys
) -> None:
    secret = "fake-secret"
    fake = FakeSession(results=[completed()], secret=secret)
    monkeypatch.setattr(
        "builtins.input", scripted_input("task", f"/history {secret}", "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__._create_session", lambda *_a, **_k: fake
    )

    assert main([]) == 0
    output = capsys.readouterr().out
    assert secret not in output
    assert "未知本地命令。支持：/undo、/new、/exit。" in output


def test_unknown_local_command_before_initialization_never_echoes_environment_key(
    monkeypatch, capsys
) -> None:
    secret = "synthetic-environment-key"
    monkeypatch.setenv("CODE_OPERATOR_API_KEY", secret)
    monkeypatch.setattr(
        "builtins.input", scripted_input(f"/history {secret}", "/exit")
    )
    monkeypatch.setattr(
        "code_operator.__main__.load_provider_config",
        lambda **_: (_ for _ in ()).throw(AssertionError("不得加载配置")),
    )

    assert main([]) == 0
    output = capsys.readouterr().out
    assert secret not in output
    assert "未知本地命令。支持：/undo、/new、/exit。" in output


def test_positional_natural_language_containing_undo_remains_one_shot(
    monkeypatch
) -> None:
    seen = {}
    config = ProviderConfig(
        api_key="one-shot-key",
        base_url="https://provider.example/v1",
        model="test-model",
    )
    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)

    def fake_run_task(*_args, **kwargs):
        seen.update(kwargs)
        return completed()

    monkeypatch.setattr("code_operator.__main__.run_task", fake_run_task)

    assert main(["请解释", "/undo", "边界"]) == 0
    assert seen["task"] == "请解释 /undo 边界"


def test_unexpected_one_shot_error_returns_one_and_is_redacted(monkeypatch, capsys) -> None:
    secret = "one-shot-secret"
    config = ProviderConfig(
        api_key=secret,
        base_url="https://provider.example/v1",
        model="test-model",
    )
    monkeypatch.setattr("code_operator.__main__.load_provider_config", lambda **_: config)
    monkeypatch.setattr(
        "code_operator.__main__.run_task",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError(f"failed {secret}\x1b[31m")
        ),
    )

    assert main(["task"]) == 1
    error = capsys.readouterr().err
    assert secret not in error
    assert r"failed <REDACTED>\x1b[31m" in error


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
    monkeypatch.setattr("builtins.input", scripted_input("   ", "/exit"))
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

    assert main([]) == 0


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
