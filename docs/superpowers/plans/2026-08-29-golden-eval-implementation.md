# Golden Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个冻结的订单价格流水线 Bug、可重复三次运行的真实 `kimi-k3` Eval harness，以及不保存凭据或 reasoning 的可审查结果。

**Architecture:** `evals/golden_bug/project` 保存故意处于红灯状态的两文件 Python 项目；`evals/run_golden.py` 每次复制 fixture 到全新临时目录，建立只读基线索引，独立执行前后 pytest，并通过现有 `run_task()` 运行 Agent。单次结果按测试哈希、允许路径、独立后测和非空 Git diff 判定，三次结果再计算 2/3 视频候选门槛。

**Tech Stack:** Python 3.11 标准库、现有 `code_operator`、pytest、Git CLI、现有 `httpx` 运行依赖；不增加新依赖。

---

## 文件职责

- Create: `pytest.ini`：让仓库全量 pytest 只收集 `tests/`，避免故意失败的 fixture 被直接收集。
- Create: `evals/golden_bug/task.txt`：冻结且不泄露答案的用户提示。
- Create: `evals/golden_bug/project/.gitignore`：忽略 audit 与 pytest/Python 运行产物。
- Create: `evals/golden_bug/project/pricing.py`：行小计、比例四舍五入、含预置取整缺陷的折扣计算。
- Create: `evals/golden_bug/project/invoice.py`：订单汇总、含预置错误计税基数。
- Create: `evals/golden_bug/project/tests/test_invoice.py`：冻结业务判据；正式运行要求哈希不变。
- Create: `evals/run_golden.py`：命令、哈希、临时工作区、单次判定、三次汇总、脱敏报告和 CLI。
- Create: `tests/test_golden_eval.py`：fixture 元测试、runner 单元测试和真实 `run_task()` 假模型集成。
- Modify: `README.md`：正式运行命令、自动测试审批边界与真实三次结论。
- Modify after real runs: `DESIGN.md`：只加入实际三次结果和限制。
- Create after real runs: `docs/evidence/e2-golden-eval.json`：排他生成的真实脱敏证据。

实施期间不创建中间提交。所有 Task 转绿、真实三次结果完成并经过完整暂存审核后，只创建计划指定的 `test(eval): add reproducible golden coding task`；这是为了遵守项目作者要求的逐提交人工审核。

### Task 1: 冻结故意失败的订单项目

**Files:**
- Create: `pytest.ini`
- Create: `evals/golden_bug/task.txt`
- Create: `evals/golden_bug/project/.gitignore`
- Create: `evals/golden_bug/project/pricing.py`
- Create: `evals/golden_bug/project/invoice.py`
- Create: `evals/golden_bug/project/tests/test_invoice.py`
- Create: `tests/test_golden_eval.py`

- [x] **Step 1: 先写 fixture 前置条件失败测试**

在 `tests/test_golden_eval.py` 写入：

```python
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evals" / "golden_bug" / "project"


def run_fixture_tests(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def test_frozen_fixture_starts_with_expected_failures(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    shutil.copytree(FIXTURE, workspace)

    completed = run_fixture_tests(workspace)

    assert completed.returncode == 1
    assert "3 failed, 2 passed" in completed.stdout
```

- [x] **Step 2: 运行测试并确认红灯**

Run: `python -m pytest tests/test_golden_eval.py::test_frozen_fixture_starts_with_expected_failures -q`

Expected: FAIL，原因是 `evals/golden_bug/project` 尚不存在。

- [x] **Step 3: 创建冻结提示与项目文件**

`pytest.ini`：

```ini
[pytest]
testpaths = tests
```

`evals/golden_bug/task.txt`：

```text
修复这个订单金额计算项目。请先检查相关生产代码并运行现有测试，定位根因后只修改生产代码，不得修改、删除或绕过测试文件。完成修改后再次运行完整 pytest，并依据真实测试结果总结改动与仍存在的限制。
```

`evals/golden_bug/project/.gitignore`：

```gitignore
.code-operator/
.pytest_cache/
__pycache__/
*.pyc
```

`evals/golden_bug/project/pricing.py`：

```python
from __future__ import annotations


def _require_non_negative(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def require_percent(value: int, name: str) -> None:
    _require_non_negative(value, name)
    if value > 100:
        raise ValueError(f"{name} must be between 0 and 100")


def round_ratio_half_up(numerator: int, denominator: int) -> int:
    _require_non_negative(numerator, "numerator")
    if isinstance(denominator, bool) or not isinstance(denominator, int) or denominator <= 0:
        raise ValueError("denominator must be a positive integer")
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(remainder * 2 >= denominator)


def line_subtotal(unit_price_cents: int, quantity: int) -> int:
    _require_non_negative(unit_price_cents, "unit_price_cents")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("quantity must be a positive integer")
    return unit_price_cents * quantity


def discounted_subtotal(subtotal_cents: int, discount_percent: int) -> int:
    _require_non_negative(subtotal_cents, "subtotal_cents")
    require_percent(discount_percent, "discount_percent")
    discount_cents = subtotal_cents * discount_percent // 100
    return subtotal_cents - discount_cents
```

`evals/golden_bug/project/invoice.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from pricing import (
    discounted_subtotal,
    line_subtotal,
    require_percent,
    round_ratio_half_up,
)


@dataclass(frozen=True)
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int


@dataclass(frozen=True)
class InvoiceTotals:
    subtotal_cents: int
    discounted_subtotal_cents: int
    tax_cents: int
    total_cents: int


def calculate_invoice(
    items: Sequence[LineItem],
    *,
    discount_percent: int,
    tax_percent: int,
) -> InvoiceTotals:
    subtotal_cents = sum(
        line_subtotal(item.unit_price_cents, item.quantity) for item in items
    )
    discounted_cents = discounted_subtotal(subtotal_cents, discount_percent)
    require_percent(tax_percent, "tax_percent")
    tax_cents = round_ratio_half_up(subtotal_cents * tax_percent, 100)
    return InvoiceTotals(
        subtotal_cents=subtotal_cents,
        discounted_subtotal_cents=discounted_cents,
        tax_cents=tax_cents,
        total_cents=discounted_cents + tax_cents,
    )
```

`evals/golden_bug/project/tests/test_invoice.py`：

```python
from __future__ import annotations

import pytest

from invoice import LineItem, calculate_invoice
from pricing import discounted_subtotal, line_subtotal


def test_no_discount_or_tax() -> None:
    totals = calculate_invoice(
        [LineItem("book", 1299, 2)], discount_percent=0, tax_percent=0
    )
    assert totals.subtotal_cents == 2598
    assert totals.discounted_subtotal_cents == 2598
    assert totals.tax_cents == 0
    assert totals.total_cents == 2598


def test_line_subtotal_rejects_invalid_quantity() -> None:
    with pytest.raises(ValueError, match="quantity"):
        line_subtotal(500, 0)


def test_discount_rounds_half_cent_up() -> None:
    assert discounted_subtotal(101, 50) == 50


def test_tax_uses_discounted_subtotal() -> None:
    totals = calculate_invoice(
        [LineItem("lamp", 1000, 1)], discount_percent=10, tax_percent=10
    )
    assert totals.discounted_subtotal_cents == 900
    assert totals.tax_cents == 90
    assert totals.total_cents == 990


def test_multi_item_discount_tax_and_rounding() -> None:
    totals = calculate_invoice(
        [LineItem("pen", 999, 3), LineItem("clip", 250, 2)],
        discount_percent=15,
        tax_percent=8,
    )
    assert totals.subtotal_cents == 3497
    assert totals.discounted_subtotal_cents == 2972
    assert totals.tax_cents == 238
    assert totals.total_cents == 3210
```

- [x] **Step 4: 验证 fixture 元测试转绿且仓库套件不直接收集故意失败测试**

Run: `python -m pytest tests/test_golden_eval.py::test_frozen_fixture_starts_with_expected_failures -q`

Expected: `1 passed`。

Run: `python -m pytest --collect-only -q`

Expected: 收集 `tests/test_golden_eval.py`，不直接收集 `evals/golden_bug/project/tests/test_invoice.py`。

质量复核追加闭环：根级隔离子进程测试先证明 `tax_percent=101/True` 被错误接受，再通过复用 `require_percent` 关闭非预期第三缺陷；fixture 自身仍精确为 `3 failed, 2 passed`，根套件为 242 项通过。

### Task 2: 命令、哈希与 Git 基线原语

**Files:**
- Create: `evals/run_golden.py`
- Modify: `tests/test_golden_eval.py`

- [x] **Step 1: 写哈希、摘要和报告排他创建失败测试**

在 `tests/test_golden_eval.py` 追加：

```python
import json

import pytest

from code_operator.config import ProviderConfig
from evals.run_golden import (
    EvalInfrastructureError,
    combined_hash,
    run_process,
    stable_summary,
    write_report_exclusive,
)


CONFIG = ProviderConfig(
    api_key="golden-secret-value",
    base_url="https://provider.example/v1",
    model="kimi-k3",
)


def test_combined_hash_is_path_order_stable(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("two", encoding="utf-8")
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    first = combined_hash([tmp_path / "b.txt", tmp_path / "a.txt"], root=tmp_path)
    second = combined_hash([tmp_path / "a.txt", tmp_path / "b.txt"], root=tmp_path)
    assert first == second


def test_stable_summary_keeps_bounded_nonempty_tail() -> None:
    output = "\n".join(f"line-{index}" for index in range(40))
    summary = stable_summary(output)
    assert summary.splitlines()[0] == "line-28"
    assert summary.splitlines()[-1] == "line-39"


def test_report_is_redacted_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    write_report_exclusive(
        path,
        {"message": "Bearer golden-secret-value", "api_key": "golden-secret-value"},
        api_key=CONFIG.api_key,
    )
    text = path.read_text(encoding="utf-8")
    assert "golden-secret-value" not in text
    assert "<REDACTED>" in text
    with pytest.raises(EvalInfrastructureError, match="已存在"):
        write_report_exclusive(path, {"ok": True}, api_key=CONFIG.api_key)


def test_process_timeout_returns_stable_result(tmp_path: Path) -> None:
    result = run_process(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=tmp_path,
        timeout=0.1,
    )
    assert result.timed_out is True
    assert result.returncode is None
```

- [x] **Step 2: 运行并确认模块缺失红灯**

Run: `python -m pytest tests/test_golden_eval.py -q`

Expected: collection ERROR，`ModuleNotFoundError: No module named 'evals.run_golden'`。

- [x] **Step 3: 实现原语与稳定数据结构**

创建 `evals/run_golden.py`，先写入以下完整原语：

```python
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from code_operator.__main__ import run_task
from code_operator.config import ConfigError, ProviderConfig, load_provider_config
from code_operator.loop import ModelLike
from code_operator.models import RunResult
from code_operator.redaction import Redactor


EVAL_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = EVAL_ROOT / "golden_bug" / "project"
TASK_PATH = EVAL_ROOT / "golden_bug" / "task.txt"
ALLOWED_CHANGED_PATHS = frozenset({"pricing.py", "invoice.py"})
OFFICIAL_RUNS = 3
TEST_TIMEOUT_SECONDS = 60
GIT_TIMEOUT_SECONDS = 30


class EvalInfrastructureError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


class AgentRunner(Protocol):
    def __call__(
        self,
        config: ProviderConfig,
        *,
        workspace: Path,
        task: str,
        client: ModelLike | None,
    ) -> RunResult: ...


def stable_summary(output: str, *, lines: int = 12, characters: int = 2_000) -> str:
    nonempty = [line.rstrip() for line in output.splitlines() if line.strip()]
    return "\n".join(nonempty[-lines:])[-characters:]


def combined_hash(paths: Sequence[Path], *, root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def fixture_hash(root: Path) -> str:
    files = [path for path in root.rglob("*") if path.is_file()]
    return combined_hash(files, root=root)


def tests_hash(root: Path) -> str:
    files = [path for path in (root / "tests").rglob("*.py") if path.is_file()]
    if not files:
        raise EvalInfrastructureError("冻结项目缺少 Python 测试文件")
    return combined_hash(files, root=root)


def run_process(argv: Sequence[str], *, cwd: Path, timeout: float) -> CommandResult:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            None,
            error.stdout if isinstance(error.stdout, str) else "",
            error.stderr if isinstance(error.stderr, str) else "",
            True,
        )
    except OSError as error:
        raise EvalInfrastructureError(f"无法启动命令：{argv[0]}") from error
    return CommandResult(completed.returncode, completed.stdout, completed.stderr, False)


def require_process(argv: Sequence[str], *, cwd: Path, timeout: float) -> CommandResult:
    result = run_process(argv, cwd=cwd, timeout=timeout)
    if result.timed_out or result.returncode != 0:
        raise EvalInfrastructureError(
            f"基础设施命令失败：{json.dumps(list(argv), ensure_ascii=False)}"
        )
    return result


def write_report_exclusive(path: Path, report: dict[str, object], *, api_key: str) -> None:
    if not path.parent.is_dir():
        raise EvalInfrastructureError("报告父目录不存在")
    payload = Redactor([api_key]).redact_object(report)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise EvalInfrastructureError("报告文件已存在，拒绝覆盖") from error
```

- [x] **Step 4: 运行原语测试至绿**

Run: `python -m pytest tests/test_golden_eval.py -q`

Expected: 当前所有测试 PASS。

### Task 3: 单次隔离运行与严格成功判据

**Files:**
- Modify: `evals/run_golden.py`
- Modify: `tests/test_golden_eval.py`

- [x] **Step 1: 写单次成功、测试篡改、越界文件和空 diff 失败测试**

在 `tests/test_golden_eval.py` 追加以下 helper 和测试：

```python
def completed_result(tokens: int | None = 21) -> RunResult:
    return RunResult("COMPLETED", "done", 4, 5, tokens, 100)


def fix_production(workspace: Path) -> None:
    pricing = workspace / "pricing.py"
    pricing.write_text(
        pricing.read_text(encoding="utf-8").replace(
            "discount_cents = subtotal_cents * discount_percent // 100",
            "discount_cents = round_ratio_half_up(subtotal_cents * discount_percent, 100)",
        ),
        encoding="utf-8",
    )
    invoice = workspace / "invoice.py"
    invoice.write_text(
        invoice.read_text(encoding="utf-8").replace(
            "tax_cents = round_ratio_half_up(subtotal_cents * tax_percent, 100)",
            "tax_cents = round_ratio_half_up(discounted_cents * tax_percent, 100)",
        ),
        encoding="utf-8",
    )


def test_single_run_accepts_only_independently_verified_production_fix() -> None:
    def runner(config, *, workspace, task, client):
        fix_production(workspace)
        return completed_result()

    result = run_single(CONFIG, index=1, agent_runner=runner)

    assert result["success"] is True
    assert result["failure_reasons"] == []
    assert result["initial_test"]["exit_code"] == 1
    assert result["final_test"]["exit_code"] == 0
    assert result["tests_unchanged"] is True
    assert result["changed_paths"] == ["invoice.py", "pricing.py"]
    assert result["git_diff"]


def test_each_single_run_uses_and_cleans_a_different_workspace() -> None:
    seen: list[Path] = []

    def runner(config, *, workspace, task, client):
        seen.append(workspace)
        fix_production(workspace)
        return completed_result()

    run_single(CONFIG, index=1, agent_runner=runner)
    run_single(CONFIG, index=2, agent_runner=runner)

    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert all(not workspace.exists() for workspace in seen)


def test_single_run_rejects_test_modification_even_when_tests_pass() -> None:
    def runner(config, *, workspace, task, client):
        fix_production(workspace)
        test_file = workspace / "tests" / "test_invoice.py"
        test_file.write_text(test_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return completed_result()

    result = run_single(CONFIG, index=1, agent_runner=runner)

    assert result["success"] is False
    assert "tests_modified" in result["failure_reasons"]


def test_single_run_rejects_unexpected_new_file() -> None:
    def runner(config, *, workspace, task, client):
        fix_production(workspace)
        (workspace / "answer.py").write_text("value = 1\n", encoding="utf-8")
        return completed_result()

    result = run_single(CONFIG, index=1, agent_runner=runner)

    assert result["success"] is False
    assert "unexpected_paths:answer.py" in result["failure_reasons"]


def test_single_run_rejects_completed_without_a_fix() -> None:
    result = run_single(
        CONFIG,
        index=1,
        agent_runner=lambda config, workspace, task, client: completed_result(None),
    )

    assert result["success"] is False
    assert "final_tests_failed" in result["failure_reasons"]
    assert "empty_diff" in result["failure_reasons"]
    assert result["provider_total_tokens"] is None
    assert result["usage_complete"] is False
```

同时把以下 import 加到该测试文件：

```python
from code_operator.models import RunResult
from evals.run_golden import run_single
```

- [x] **Step 2: 运行并确认 `run_single` 缺失红灯**

Run: `python -m pytest tests/test_golden_eval.py -q`

Expected: collection ERROR 或 FAIL，指出 `run_single` 尚未定义。

- [x] **Step 3: 实现临时 Git 基线、变更读取与单次判定**

在 `evals/run_golden.py` 追加：

```python
def _default_agent_runner(
    config: ProviderConfig,
    *,
    workspace: Path,
    task: str,
    client: ModelLike | None,
) -> RunResult:
    return run_task(
        config,
        workspace=workspace,
        task=task,
        approve=lambda _argv, _cwd: False,
        client=client,
        auto_approve_tests=True,
    )


def _test_result(workspace: Path) -> CommandResult:
    return run_process(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        timeout=TEST_TIMEOUT_SECONDS,
    )


def _git_text(workspace: Path, *arguments: str) -> str:
    result = require_process(
        ["git", "-c", "core.quotePath=false", *arguments],
        cwd=workspace,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    return result.stdout


def _initialize_git(workspace: Path) -> None:
    require_process(["git", "init", "-q"], cwd=workspace, timeout=GIT_TIMEOUT_SECONDS)
    require_process(["git", "add", "--all"], cwd=workspace, timeout=GIT_TIMEOUT_SECONDS)


def _changed_paths(workspace: Path) -> list[str]:
    tracked = {
        line.strip()
        for line in _git_text(workspace, "diff", "--name-only", "--").splitlines()
        if line.strip()
    }
    status = _git_text(workspace, "status", "--porcelain", "--untracked-files=all")
    untracked = {
        line[3:].strip()
        for line in status.splitlines()
        if line.startswith("?? ") and line[3:].strip()
    }
    return sorted(tracked | untracked)


def run_single(
    config: ProviderConfig,
    *,
    index: int,
    fixture_root: Path = FIXTURE_ROOT,
    task_path: Path = TASK_PATH,
    agent_runner: AgentRunner = _default_agent_runner,
    client: ModelLike | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"code-operator-golden-{index}-") as directory:
        workspace = Path(directory) / "project"
        shutil.copytree(fixture_root, workspace)
        task = task_path.read_text(encoding="utf-8").strip()
        before_tests = tests_hash(workspace)
        _initialize_git(workspace)
        initial = _test_result(workspace)
        if initial.timed_out:
            raise EvalInfrastructureError("冻结项目初始测试超时")
        if initial.returncode == 0:
            raise EvalInfrastructureError("INVALID_FIXTURE：冻结项目初始测试意外通过")

        agent_error: str | None = None
        agent_result: RunResult | None = None
        try:
            agent_result = agent_runner(
                config,
                workspace=workspace,
                task=task,
                client=client,
            )
        except Exception as error:
            agent_error = f"{type(error).__name__}: {error}"

        final = _test_result(workspace)
        after_tests = tests_hash(workspace)
        changed_paths = _changed_paths(workspace)
        git_diff = _git_text(workspace, "diff", "--binary", "--")
        failure_reasons: list[str] = []
        status = agent_result.status if agent_result is not None else "HARNESS_AGENT_ERROR"
        if agent_error is not None:
            failure_reasons.append(f"agent_exception:{agent_error}")
        if status != "COMPLETED":
            failure_reasons.append(f"agent_status:{status}")
        if final.timed_out:
            failure_reasons.append("final_test_timeout")
        elif final.returncode != 0:
            failure_reasons.append("final_tests_failed")
        if before_tests != after_tests:
            failure_reasons.append("tests_modified")
        unexpected = sorted(set(changed_paths) - ALLOWED_CHANGED_PATHS)
        if unexpected:
            failure_reasons.append(f"unexpected_paths:{','.join(unexpected)}")
        if not git_diff.strip():
            failure_reasons.append("empty_diff")

        return {
            "index": index,
            "success": not failure_reasons,
            "failure_reasons": failure_reasons,
            "initial_test": {
                "exit_code": initial.returncode,
                "summary": stable_summary(initial.stdout + "\n" + initial.stderr),
            },
            "final_test": {
                "exit_code": final.returncode,
                "timed_out": final.timed_out,
                "summary": stable_summary(final.stdout + "\n" + final.stderr),
            },
            "agent_status": status,
            "model_rounds": agent_result.model_rounds if agent_result else 0,
            "tool_calls": agent_result.tool_calls if agent_result else 0,
            "provider_total_tokens": (
                agent_result.provider_total_tokens if agent_result else None
            ),
            "usage_complete": bool(
                agent_result is not None
                and agent_result.provider_total_tokens is not None
            ),
            "tests_sha256_before": before_tests,
            "tests_sha256_after": after_tests,
            "tests_unchanged": before_tests == after_tests,
            "changed_paths": changed_paths,
            "git_diff": git_diff,
        }
```

- [x] **Step 4: 运行单次判定测试至绿**

Run: `python -m pytest tests/test_golden_eval.py -q`

Expected: 当前测试全部 PASS；测试篡改、额外文件、后测失败和空 diff 均被稳定拒绝。

### Task 4: 真实 AgentLoop 假模型集成与三次正式报告

**Files:**
- Modify: `evals/run_golden.py`
- Modify: `tests/test_golden_eval.py`

- [x] **Step 1: 写真实 `run_task()` 集成和三次汇总失败测试**

在 `tests/test_golden_eval.py` 追加 import 与 helper：

```python
from code_operator.models import AssistantTurn, ToolCall, Usage
from evals import run_golden
from evals.run_golden import OFFICIAL_RUNS, run_official_eval
from tests.fakes import FakeModelClient


def call(call_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(call_id, name, arguments)


def scripted_fix_client() -> FakeModelClient:
    usage = Usage(10, 2, 12)
    return FakeModelClient(
        [
            AssistantTurn(
                None,
                [
                    call("read-pricing", "read_file", '{"path":"pricing.py"}'),
                    call("read-invoice", "read_file", '{"path":"invoice.py"}'),
                ],
                finish_reason="tool_calls",
                usage=usage,
            ),
            AssistantTurn(
                None,
                [
                    call(
                        "fix-pricing",
                        "edit_file",
                        json.dumps(
                            {
                                "path": "pricing.py",
                                "old_text": "discount_cents = subtotal_cents * discount_percent // 100",
                                "new_text": "discount_cents = round_ratio_half_up(subtotal_cents * discount_percent, 100)",
                            }
                        ),
                    ),
                    call(
                        "fix-invoice",
                        "edit_file",
                        json.dumps(
                            {
                                "path": "invoice.py",
                                "old_text": "tax_cents = round_ratio_half_up(subtotal_cents * tax_percent, 100)",
                                "new_text": "tax_cents = round_ratio_half_up(discounted_cents * tax_percent, 100)",
                            }
                        ),
                    ),
                ],
                finish_reason="tool_calls",
                usage=usage,
            ),
            AssistantTurn(
                None,
                [
                    call(
                        "run-tests",
                        "run_command",
                        '{"argv":["python","-m","pytest","-q"],"timeout_seconds":30}',
                    )
                ],
                finish_reason="tool_calls",
                usage=usage,
            ),
            AssistantTurn("fixed and verified", [], finish_reason="stop", usage=usage),
        ]
    )


def test_single_run_uses_real_agent_loop_with_scripted_model() -> None:
    result = run_single(CONFIG, index=1, client=scripted_fix_client())
    assert result["success"] is True
    assert result["model_rounds"] == 4
    assert result["tool_calls"] == 5
    assert result["provider_total_tokens"] == 48


def test_official_eval_runs_three_fresh_successful_workspaces() -> None:
    seen_clients: list[FakeModelClient] = []

    def factory(index: int) -> FakeModelClient:
        client = scripted_fix_client()
        seen_clients.append(client)
        return client

    report = run_official_eval(CONFIG, client_factory=factory)

    assert len(seen_clients) == OFFICIAL_RUNS == 3
    assert report["run_count"] == 3
    assert report["success_count"] == 3
    assert report["batch_status"] == "COMPLETED"
    assert report["video_candidate"] is True
    assert [item["index"] for item in report["runs"]] == [1, 2, 3]


def test_official_eval_reports_invalid_fixture_without_calling_model(
    tmp_path: Path,
) -> None:
    passing_fixture = tmp_path / "passing-project"
    shutil.copytree(FIXTURE, passing_fixture)
    fix_production(passing_fixture)
    calls = 0

    def forbidden_factory(index: int) -> FakeModelClient:
        nonlocal calls
        calls += 1
        return scripted_fix_client()

    report = run_official_eval(
        CONFIG,
        fixture_root=passing_fixture,
        client_factory=forbidden_factory,
    )

    assert calls == 0
    assert report["batch_status"] == "INVALID_FIXTURE"
    assert report["run_count"] == 0
    assert report["video_candidate"] is False


def test_official_eval_keeps_failed_middle_run_and_continues() -> None:
    def factory(index: int) -> FakeModelClient:
        if index == 2:
            return FakeModelClient([RuntimeError("synthetic provider failure")])
        return scripted_fix_client()

    report = run_official_eval(CONFIG, client_factory=factory)

    assert report["run_count"] == 3
    assert report["success_count"] == 2
    assert report["video_candidate"] is True
    assert report["runs"][1]["success"] is False
    assert report["runs"][2]["index"] == 3
```

- [x] **Step 2: 运行并确认 `run_official_eval` 缺失红灯**

Run: `python -m pytest tests/test_golden_eval.py -q`

Expected: FAIL 或 collection ERROR，指出 `run_official_eval` 尚未定义。

- [x] **Step 3: 实现正式三次汇总和 CLI**

在 `evals/run_golden.py` 追加：

```python
def run_official_eval(
    config: ProviderConfig,
    *,
    fixture_root: Path = FIXTURE_ROOT,
    task_path: Path = TASK_PATH,
    client_factory: Callable[[int], ModelLike | None] | None = None,
) -> dict[str, object]:
    if config.model != "kimi-k3":
        raise EvalInfrastructureError("正式黄金 Eval 必须使用 kimi-k3")
    if not fixture_root.is_dir() or not task_path.is_file():
        raise EvalInfrastructureError("黄金 Eval fixture 或 task.txt 不存在")
    task = task_path.read_text(encoding="utf-8").strip()
    source_fixture_hash = fixture_hash(fixture_root)
    task_sha256 = hashlib.sha256(task.encode("utf-8")).hexdigest()
    common = {
        "schema_version": 1,
        "platform": {
            "os": platform.platform(),
            "python": platform.python_version(),
        },
        "configuration": {
            "model": config.model,
            "context_window": config.context_window,
            "max_output_tokens": config.max_output_tokens,
            "max_model_rounds": config.max_model_rounds,
            "max_tool_calls": config.max_tool_calls,
        },
        "fixture_sha256": source_fixture_hash,
        "task_sha256": task_sha256,
    }
    with tempfile.TemporaryDirectory(prefix="code-operator-golden-preflight-") as directory:
        preflight_workspace = Path(directory) / "project"
        shutil.copytree(fixture_root, preflight_workspace)
        preflight = _test_result(preflight_workspace)
    if preflight.timed_out:
        raise EvalInfrastructureError("冻结项目前置测试超时")
    if preflight.returncode == 0:
        return {
            **common,
            "batch_status": "INVALID_FIXTURE",
            "run_count": 0,
            "success_count": 0,
            "video_candidate": False,
            "runs": [],
        }
    runs: list[dict[str, object]] = []
    for index in range(1, OFFICIAL_RUNS + 1):
        client = client_factory(index) if client_factory is not None else None
        runs.append(
            run_single(
                config,
                index=index,
                fixture_root=fixture_root,
                task_path=task_path,
                client=client,
            )
        )
    success_count = sum(bool(item["success"]) for item in runs)
    return {
        **common,
        "batch_status": "COMPLETED",
        "run_count": len(runs),
        "success_count": success_count,
        "video_candidate": success_count >= 2,
        "runs": runs,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行三次冻结的 code-operator 黄金 Eval。")
    parser.add_argument("--report", required=True, type=Path, help="新的 JSON 报告路径")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.report.exists() or not args.report.parent.is_dir():
        print("报告路径必须尚不存在且父目录已经存在", file=sys.stderr)
        return 2
    try:
        config = load_provider_config()
        report = run_official_eval(config)
        write_report_exclusive(args.report, report, api_key=config.api_key)
    except (ConfigError, EvalInfrastructureError) as error:
        print(Redactor([]).redact(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "report": str(args.report),
                "success_count": report["success_count"],
                "run_count": report["run_count"],
                "video_candidate": report["video_candidate"],
            },
            ensure_ascii=False,
        )
    )
    if report["batch_status"] == "INVALID_FIXTURE":
        return 2
    return 0 if report["video_candidate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: 增加禁止字段与报告 CLI 测试**

在 `tests/test_golden_eval.py` 追加：

```python
def test_written_official_report_has_no_forbidden_fields(tmp_path: Path) -> None:
    report = run_official_eval(CONFIG, client_factory=lambda index: scripted_fix_client())
    path = tmp_path / "report.json"
    write_report_exclusive(path, report, api_key=CONFIG.api_key)
    payload = json.loads(path.read_text(encoding="utf-8"))
    encoded = json.dumps(payload, ensure_ascii=False).lower()
    assert CONFIG.api_key not in encoded
    for forbidden in ["api_key", "authorization", "request_id", "reasoning", "response_body"]:
        assert forbidden not in encoded


def test_cli_refuses_existing_report_before_loading_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "existing.json"
    report.write_text("{}", encoding="utf-8")
    calls = 0

    def forbidden_load() -> ProviderConfig:
        nonlocal calls
        calls += 1
        return CONFIG

    monkeypatch.setattr(run_golden, "load_provider_config", forbidden_load)

    exit_code = run_golden.main(["--report", str(report)])

    assert exit_code == 2
    assert calls == 0
```

- [x] **Step 5: 运行专项和全量离线测试至绿**

Run: `python -m pytest tests/test_golden_eval.py -q`

Expected: 全部 PASS，且没有真实网络访问。

Run: `python -m pytest -q`

Expected: 既有 240 项加 E2 新测试全部 PASS；故意失败的 fixture 不被根套件直接收集。

Run: `python -m compileall -q code_operator evals tests`

Expected: exit 0。

### Task 5: 真实 `kimi-k3` 三次正式运行与证据复核

**Files:**
- Create: `docs/evidence/e2-golden-eval.json`
- Modify: `README.md`
- Modify: `DESIGN.md`

- [x] **Step 1: 运行前固定环境与哈希**

确认环境变量只通过进程环境提供且不显示值：`CODE_OPERATOR_API_KEY`、`CODE_OPERATOR_BASE_URL=https://api.moonshot.cn/v1`、`CODE_OPERATOR_MODEL=kimi-k3`。运行：

`python -c "import os; print({'key_configured': bool(os.environ.get('CODE_OPERATOR_API_KEY')), 'base_configured': bool(os.environ.get('CODE_OPERATOR_BASE_URL')), 'model': os.environ.get('CODE_OPERATOR_MODEL')})"`

Expected: 两个 configured 均为 `True`，model 为 `kimi-k3`；不得打印 Key。

- [x] **Step 2: 执行不可覆盖的正式三次运行**

Run: `python -m evals.run_golden --report docs/evidence/e2-golden-eval.json`

Expected: 固定执行三次；报告不存在时成功创建。退出码 0 仅表示至少 2/3 成功；退出码 1 表示三次完整记录但未达门槛；退出码 2 表示基础设施或配置失败。

- [x] **Step 3: 独立核对报告，不选择性删除失败**

Run: `python -m json.tool docs/evidence/e2-golden-eval.json`

Expected: 合法 JSON；`run_count == 3`，三个 index 为 1/2/3，fixture/task 哈希一致，测试哈希均不变。逐次核对 initial 非零、final、状态、changed_paths 和 diff；失败项必须原样保留。

Run: `python -c "import json; p=json.load(open('docs/evidence/e2-golden-eval.json',encoding='utf-8')); print({'runs':p['run_count'],'successes':p['success_count'],'candidate':p['video_candidate'],'test_files_unchanged':all(r['tests_unchanged'] for r in p['runs'])})"`

Expected: 不显示凭据；只有 successes 至少 2 时 candidate 为 True。

- [x] **Step 4: 若少于 2/3，停止文档成功声明并定位根因**（未触发：修复后从零重跑为 3/3）

不得更换 fixture、提示或断言。使用保存的三次状态、测试摘要与 diff 确定是否存在 Agent 核心根因；如需改生产 Agent，先新增独立失败测试、最小修复、全量验证，然后将当前报告保留为失败证据并使用新的报告路径重新开始完整三次计数。

- [x] **Step 5: 仅依据实际报告更新 README 与 DESIGN**

README 增加：

```markdown
## 黄金 Eval

冻结的订单价格流水线任务通过 `python -m evals.run_golden --report <NEW_REPORT.json>` 在三个全新临时工作区运行。该 harness 只自动批准明确的 pytest，测试文件哈希必须保持不变；这不构成 OS 沙箱。实际结果见 `docs/evidence/e2-golden-eval.json`，这里只表述该固定任务三次中的成功次数，不泛化为整体成功率。
```

随后把 `<NEW_REPORT.json>` 示例保留为占位参数，但实际成功次数必须从报告逐字写入 README/DESIGN，例如报告为 2 次成功时写“该固定任务三次中成功两次”；不得在执行前预写结果。

- [x] **Step 6: 最终本地验证与凭据扫描**

Run: `python -m pytest -q`

Expected: 全量 PASS。

Run: `python -m compileall -q code_operator evals tests`

Expected: exit 0。

运行现有凭据扫描口径，确认当前 API Key 在工作树、待审核差异和 Git 历史中的精确命中均为 0；形似私钥、Bearer、`.env`、audit JSONL、缓存、PID 和临时工作区文件均未进入待提交范围。

### Task 6: E2 人工审核、单一实现提交与推送门禁

**Files:**
- Modify after approval: `REVIEW_LOG.md`
- Modify outside repository: 根执行计划的 E2 复选框

- [x] **Step 1: 暂存完整 E2 实现范围并生成审核补丁**

暂存 fixture、runner、测试、README、DESIGN 和真实脱敏报告；不得暂存外部工作区、API Key、audit、缓存或旧失败报告的未审查副本。生成 `E2-001-review.patch` 和 SHA-256。

- [x] **Step 2: 向项目作者展示人工审核包**

必须包含文件范围、TDD 红—绿证据、全量测试、真实三次逐次结果、测试哈希、diff、usage 完整性、凭据扫描、开源/依赖边界、失败和限制。取得精确回复 `E2-001 人工审核通过` 前不得提交。

- [x] **Step 3: 写入审核台账并创建唯一实现提交**

Commit message: `test(eval): add reproducible golden coding task`

Trailers:

```text
Human-Review: approved
Review-Scope: E2-001; full-staged-diff
Review-Record: REVIEW_LOG.md#e2-001
```

- [ ] **Step 4: 展示第 9.5 节完整推送门禁**

列出 Git 状态、从 `origin/main` 到 HEAD 的全部待推送提交（包括已审核设计提交与实现提交）、完整变更、审查结论、测试/真实探针、凭据/临时文件扫描及文档—实现—开源边界一致性。只有项目作者当次再次回复 `允许推送` 或 `可以 push` 才普通推送；不得强推或改写历史。

- [ ] **Step 5: 推送后只读核验远端与 CI**

远端 `main` 必须解引用到本地 HEAD，GitHub Actions `offline-tests` 必须在该提交完成且结论为 success。随后才把根计划 E2 的提交/推送项标为完成，并报告 E3 预计 0.5–1.5 小时。
