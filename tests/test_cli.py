from __future__ import annotations

from code_operator.__main__ import build_parser, main
from code_operator.models import RunResult


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
        lambda **_: object(),
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
