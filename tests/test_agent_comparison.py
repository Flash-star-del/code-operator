"""Integrity checks for the frozen agent-comparison task fixtures."""

from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

import pytest

from evals.agent_comparison import validate_task
from evals.agent_comparison import fixture_validator


TASK_ROOT = Path(__file__).parents[1] / "evals" / "agent_comparison" / "tasks"


def _executable_hash(path: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()

EXPECTED_FIXTURE_FILES = (
    "T1/task.txt",
    "T1/project/ranges.py",
    "T1/project/tests/test_ranges.py",
    "T1/grader/test_hidden_ranges.py",
    "T2/task.txt",
    "T2/project/retry.py",
    "T2/project/tests/test_retry.py",
    "T2/grader/test_hidden_retry.py",
    "T3/task.txt",
    "T3/project/levels.py",
    "T3/project/events.py",
    "T3/project/tests/test_events.py",
    "T3/grader/test_hidden_events.py",
)


def test_task_fixture_files_exist() -> None:
    missing = [relative for relative in EXPECTED_FIXTURE_FILES if not (TASK_ROOT / relative).is_file()]
    assert not missing, f"missing task fixture files: {missing}"


def test_task_fixture_prompts_are_frozen() -> None:
    expected = {
        "T1": "`chunk_ranges` 在某些输入下不能完整覆盖 `[0, total)`。请定位并修复根因。只允许修改 `ranges.py`，必须保留参数校验和半开区间语义，并运行现有测试确认结果；不要新增依赖或修改测试。\n",
        "T2": "`retry_delay` 的第一次重试等待时间不符合其文档约定。请先在 `tests/` 下新增一个能够捕获该根因的有意义回归测试，再修复 `retry.py`，并运行完整测试。不得删除或放宽现有测试，不得新增依赖或修改其他生产文件。\n",
        "T3": "为日志级别增加兼容的规范化能力：在 `levels.py` 新增 `normalize_level(value: str) -> str`，去除首尾空白、转为小写并验证支持的级别；非字符串抛出 `TypeError`，未知或空级别抛出 `ValueError`。在 `events.py` 中复用该函数，使 `format_event` 接受例如 `\" Warning \"` 的输入，同时保持原有函数签名、默认行为和输出格式。只允许修改 `levels.py`、`events.py`，并运行现有测试；不要新增依赖或修改测试。\n",
    }
    for task_id, prompt in expected.items():
        assert (TASK_ROOT / task_id / "task.txt").read_text(encoding="utf-8") == prompt


def test_task_fixtures_have_red_green_invariants_and_stable_hashes() -> None:
    expected_allowed_paths = {
        "T1": ("ranges.py",),
        "T2": ("retry.py", "tests/test_*.py"),
        "T3": ("events.py", "levels.py"),
    }
    first = {task_id: validate_task(task_id) for task_id in ("T1", "T2", "T3")}
    for task_id, result in first.items():
        assert result.valid
        assert result.initial_fail_to_pass
        assert not result.initial_pass_to_pass
        assert result.initial_hidden_red
        assert result.reference_hidden_green
        assert not result.reference_fail_to_pass
        assert not result.reference_pass_to_pass
        assert result.allowed_paths == expected_allowed_paths[task_id]
        assert result.component_hashes == fixture_validator._component_hashes(task_id)


def test_task_fixture_project_files_and_component_hashes_are_frozen() -> None:
    expected_files = {
        "T1": ("ranges.py", "tests/test_ranges.py"),
        "T2": ("retry.py", "tests/test_retry.py"),
        "T3": ("events.py", "levels.py", "tests/test_events.py"),
    }
    expected_hashes = {
        "T1": {"prompt": "3dda119803fe36b9bc894e18b032aa9c1a3d9b11935f23b3f6fcb6ca27bbc142", "project": "7bea4ae853bcad4848688fbb5ae76a96ba764302db3f58be844bbf7c93e34065", "visible": "f1af98827ba51a3362861fd18525db76ff2059e1a81fc0f8dd1032714e7c7d51", "hidden": "402331244f8c4b67710483341d2c666c5dfce9c6b1bbf78723c8f506934cbaad", "reference": "b53c40c4f6d792430036adaa715669ea943aeb5d00b1a8a0ab47c8695b4c1e2e"},
        "T2": {"prompt": "562a167ba0e3a5a1e3ce02689941f599a430a5aded96e2cfcbc99a9dcebea737", "project": "64f4abf83a49fda4fd77907e666e477e4a863e9a194533cb45e0458791ca8e3f", "visible": "ba81a92792894363ccc0b37d8649bc970a11a491861de669c273a87258085467", "hidden": "30a2000bfb7275dfb1dd0fe997291624983a0dbedc0fa7243a94568b34930c5d", "reference": "c91835909bede0e75431080f7c6cd1df844bba0bf7130dc26ad50df9a5b875be"},
        "T3": {"prompt": "f75418092562021ee8320c54b4046739e8f6cc3c75af708f8f658c2d3f48d199", "project": "ae805d73f36dc93e4a85bfe2b1a3f31f010df0ace0a66fe6defc27c77479bf55", "visible": "b1e2f82626c6534d9bebd72e3128ac50473f0a0453ee25d600eb8f97606e9466", "hidden": "c5f62c84152ce16cd6232fb6fee8128fceac2b52105b478e5006227886e50185", "reference": "289d0174e8bfd4e812a464b7e93b64ad4404062e40fd7c2dfaa201c011f91033"},
    }
    for task_id, files in expected_files.items():
        assert fixture_validator._project_files(task_id) == files
        assert fixture_validator._component_hashes(task_id) == expected_hashes[task_id]


def test_task_fixture_participant_project_contains_no_grader_files() -> None:
    for task_id in ("T1", "T2", "T3"):
        project = TASK_ROOT / task_id / "project"
        assert not list(project.rglob("*grader*"))
        assert not list(project.rglob("test_hidden_*.py"))


def test_task_fixture_retry_requires_a_meaningful_added_regression_artifact() -> None:
    checker = getattr(fixture_validator, "validate_retry_artifact", None)
    assert callable(checker)
    with tempfile.TemporaryDirectory(prefix="retry-artifact-test-") as directory:
        candidate = Path(directory) / "project"
        shutil.copytree(TASK_ROOT / "T2" / "project", candidate)
        added = candidate / "tests" / "test_retry_first_attempt.py"
        added.write_text(
            "from retry import retry_delay\n\n\n"
            "def test_first_retry_waits_base():\n"
            "    assert retry_delay(1) == 0.5\n",
            encoding="utf-8",
        )
        (candidate / "retry.py").write_bytes(fixture_validator.REFERENCE_BYTES["T2"]["retry.py"])
        result = checker(candidate)
        assert result.valid
        assert result.added_tests == ("tests/test_retry_first_attempt.py",)
        assert result.baseline_failed
        assert result.final_passed

        no_tests = Path(directory) / "no-tests"
        shutil.copytree(TASK_ROOT / "T2" / "project", no_tests)
        assert not checker(no_tests).valid

        toothless = Path(directory) / "toothless"
        shutil.copytree(TASK_ROOT / "T2" / "project", toothless)
        (toothless / "tests" / "test_retry_cap.py").write_text(
            "from retry import retry_delay\n\n\n"
            "def test_cap_is_preserved():\n"
            "    assert retry_delay(10) == 8.0\n",
            encoding="utf-8",
        )
        assert not checker(toothless).valid


def test_task_fixture_reference_retry_artifact_is_green_without_exposing_it_to_project() -> None:
    checker = getattr(fixture_validator, "validate_reference_retry_artifact", None)
    assert callable(checker)
    result = checker()
    assert result.valid
    assert result.baseline_failed
    assert result.final_passed
    assert not (TASK_ROOT / "T2" / "project" / "tests" / "test_retry_first_attempt.py").exists()


def test_task_fixture_project_hash_and_enumeration_ignore_pytest_cache() -> None:
    project = TASK_ROOT / "T1" / "project"
    cache = project / ".pytest_cache" / "v" / "cache"
    before_files = fixture_validator._project_files("T1")
    before_hash = fixture_validator._component_hashes("T1")["project"]
    cache.mkdir(parents=True)
    (cache / "nodeids").write_text("generated", encoding="utf-8")
    try:
        assert fixture_validator._project_files("T1") == before_files
        assert fixture_validator._component_hashes("T1")["project"] == before_hash
    finally:
        shutil.rmtree(project / ".pytest_cache")


def test_task_fixture_rejects_source_inspection_pseudo_regression() -> None:
    checker = fixture_validator.validate_retry_artifact
    with tempfile.TemporaryDirectory(prefix="retry-source-pseudo-") as directory:
        candidate = Path(directory) / "project"
        shutil.copytree(TASK_ROOT / "T2" / "project", candidate)
        (candidate / "retry.py").write_bytes(fixture_validator.REFERENCE_BYTES["T2"]["retry.py"])
        (candidate / "tests" / "test_source_only.py").write_text(
            "from inspect import getsource\n"
            "from retry import retry_delay\n\n\n"
            "def test_source_looks_fixed():\n"
            "    retry_delay(1)\n"
            "    assert 'attempt - 1' in getsource(retry_delay)\n",
            encoding="utf-8",
        )
        assert not checker(candidate).valid


def test_task_fixture_requires_one_added_test_to_kill_both_mutants() -> None:
    checker = fixture_validator.validate_retry_artifact
    with tempfile.TemporaryDirectory(prefix="retry-split-attribution-") as directory:
        baseline_only = Path(directory) / "baseline-only"
        shutil.copytree(TASK_ROOT / "T2" / "project", baseline_only)
        (baseline_only / "retry.py").write_bytes(fixture_validator.REFERENCE_BYTES["T2"]["retry.py"])
        (baseline_only / "tests" / "test_attempt_two.py").write_text(
            "from retry import retry_delay\n\n\ndef test_attempt_two_matches_reference():\n"
            "    assert retry_delay(2) == 1.0\n",
            encoding="utf-8",
        )
        result = checker(baseline_only)
        assert not result.valid
        assert result.baseline_failed_tests == ("tests/test_attempt_two.py",)
        assert result.mutant_failed_tests == ()

        mutant_only = Path(directory) / "mutant-only"
        shutil.copytree(TASK_ROOT / "T2" / "project", mutant_only)
        (mutant_only / "retry.py").write_bytes(fixture_validator.REFERENCE_BYTES["T2"]["retry.py"])
        (mutant_only / "tests" / "test_baseline_shape.py").write_text(
            "from retry import retry_delay\n\n\ndef test_attempt_two_matches_frozen_shape():\n"
            "    assert retry_delay(2) == 2.0\n",
            encoding="utf-8",
        )
        result = checker(mutant_only)
        assert not result.valid
        assert result.baseline_failed_tests == ()
        assert result.mutant_failed_tests == ("tests/test_baseline_shape.py",)


def test_task_fixture_rejects_candidate_pytest_controls_and_unsafe_files() -> None:
    checker = fixture_validator.validate_retry_artifact
    forbidden = ("conftest.py", "pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
    with tempfile.TemporaryDirectory(prefix="retry-candidate-controls-") as directory:
        for name in forbidden:
            candidate = Path(directory) / name.replace(".", "-")
            shutil.copytree(TASK_ROOT / "T2" / "project", candidate)
            (candidate / name).write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
            (candidate / "retry.py").write_bytes(fixture_validator.REFERENCE_BYTES["T2"]["retry.py"])
            assert not checker(candidate).valid

        unsafe = Path(directory) / "unsafe"
        shutil.copytree(TASK_ROOT / "T2" / "project", unsafe)
        (unsafe / "retry.py").write_bytes(fixture_validator.REFERENCE_BYTES["T2"]["retry.py"])
        (unsafe / "tests" / "test_unsafe.py").write_text(
            "import os\n\n\ndef test_unsafe():\n    assert os.getcwd()\n",
            encoding="utf-8",
        )
        assert not checker(unsafe).valid

        link_candidate = Path(directory) / "symlink"
        shutil.copytree(TASK_ROOT / "T2" / "project", link_candidate)
        (link_candidate / "tests" / "test_link.py").write_text(
            "from retry import retry_delay\n\n\ndef test_link():\n    assert retry_delay(1) == 0.5\n",
            encoding="utf-8",
        )
        (link_candidate / "retry.py").write_bytes(fixture_validator.REFERENCE_BYTES["T2"]["retry.py"])
        with patch.object(Path, "is_symlink", return_value=True):
            assert not checker(link_candidate).valid


def test_task_fixture_discovers_recursive_tests_and_rejects_more_than_twenty() -> None:
    checker = fixture_validator.validate_retry_artifact
    with tempfile.TemporaryDirectory(prefix="retry-candidate-count-") as directory:
        candidate = Path(directory) / "nested"
        shutil.copytree(TASK_ROOT / "T2" / "project", candidate)
        (candidate / "retry.py").write_bytes(fixture_validator.REFERENCE_BYTES["T2"]["retry.py"])
        nested = candidate / "tests" / "nested"
        nested.mkdir()
        (nested / "test_first.py").write_text(
            "from retry import retry_delay\n\n\ndef test_first():\n    assert retry_delay(1) == 0.5\n",
            encoding="utf-8",
        )
        assert checker(candidate).added_tests == ("tests/nested/test_first.py",)

        too_many = Path(directory) / "too-many"
        shutil.copytree(candidate, too_many)
        for index in range(20):
            (too_many / "tests" / f"test_extra_{index}.py").write_text(
                "def test_extra():\n    assert True\n", encoding="utf-8"
            )
        assert not checker(too_many).valid


def test_task_fixture_rejects_symlinked_candidate_root_before_traversal() -> None:
    with tempfile.TemporaryDirectory(prefix="retry-root-link-") as directory:
        candidate = Path(directory) / "project"
        shutil.copytree(TASK_ROOT / "T2" / "project", candidate)
        (candidate / "retry.py").write_bytes(fixture_validator.REFERENCE_BYTES["T2"]["retry.py"])
        with patch.object(Path, "is_symlink", return_value=True), patch.object(
            Path, "rglob", side_effect=AssertionError("root must be checked before traversal")
        ):
            result = fixture_validator.validate_retry_artifact(candidate)
        assert not result.valid
        assert any("reparse-or-symlink" in violation for violation in result.candidate_violations)


def test_task_fixture_rejects_invalid_retry_file_structurally() -> None:
    with tempfile.TemporaryDirectory(prefix="retry-invalid-file-") as directory:
        for kind in ("missing", "directory", "symlink"):
            candidate = Path(directory) / kind
            shutil.copytree(TASK_ROOT / "T2" / "project", candidate)
            retry = candidate / "retry.py"
            retry.unlink()
            if kind == "directory":
                retry.mkdir()
            elif kind == "symlink":
                retry.write_text("", encoding="utf-8")
            if kind == "symlink":
                with patch.object(Path, "is_symlink", return_value=True):
                    result = fixture_validator.validate_retry_artifact(candidate)
            else:
                result = fixture_validator.validate_retry_artifact(candidate)
            assert not result.valid
            assert result.candidate_violations


def test_task_fixture_reports_missing_project_file() -> None:
    original = fixture_validator._project_files
    with patch.object(
        fixture_validator,
        "_project_files",
        side_effect=lambda task_id: ("ranges.py",) if task_id == "T1" else original(task_id),
    ):
        result = fixture_validator.validate_task("T1")
    assert not result.valid
    assert "missing-project-file" in result.violations


def test_manifest_builds_frozen_balanced_seeded_schedule() -> None:
    from dataclasses import FrozenInstanceError, is_dataclass

    from evals.agent_comparison.manifest import (
        FORMAL_RUN_COUNT,
        REPLICATES,
        build_manifest,
        canonical_json,
    )
    from evals.agent_comparison.schema import FrozenManifest, SystemConfig

    manifest = build_manifest(systems=_test_systems())
    assert isinstance(manifest, FrozenManifest)
    assert is_dataclass(manifest)
    assert manifest.timeout_seconds == 360
    assert len(manifest.pilot) == 3
    assert REPLICATES == (1,)
    assert FORMAL_RUN_COUNT == 9
    assert len(manifest.formal) == 9
    assert tuple(cell.task_id for cell in manifest.pilot) == ("T1",) * 3
    for phase in (manifest.pilot, manifest.formal):
        for cell in phase:
            assert cell.track == "A"
            assert cell.system_id in ("code-operator", "claude-code", "kimi-code")
        assert len({cell.order_index for cell in phase}) == len(phase)
    blocks = {(task, replicate): [] for task in ("T1", "T2", "T3") for replicate in REPLICATES}
    for cell in manifest.formal:
        blocks[(cell.task_id, cell.replicate)].append(cell.system_id)
    assert all(sorted(systems) == ["claude-code", "code-operator", "kimi-code"] for systems in blocks.values())
    assert canonical_json(manifest) == canonical_json(build_manifest(systems=_test_systems()))
    with pytest.raises(FrozenInstanceError):
        manifest.seed = 1


def test_manifest_validation_rejects_unsafe_or_incomplete_inputs() -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, validate_manifest

    manifest = build_manifest(systems=_test_systems())
    assert validate_manifest(manifest) == ()
    bad_config = replace(manifest.systems[0], argv_template=("relative-cli", "{task}"))
    bad = replace(manifest, systems=(bad_config,) + manifest.systems[1:])
    assert any("executable" in violation for violation in validate_manifest(bad))
    bad_timeout = replace(manifest, timeout_seconds=1)
    assert any("timeout" in violation for violation in validate_manifest(bad_timeout))
    bad_hashes = replace(manifest, task_hashes={"T1": {"prompt": "x"}})
    assert validate_manifest(bad_hashes)


def _test_systems():
    from evals.agent_comparison.schema import SystemConfig
    import sys

    executable = sys.executable
    return tuple(
        SystemConfig(
            system_id=system_id,
            cli_version="1.2.3",
            executable_sha256=_executable_hash(executable),
            model=f"model-{index + 1}",
            auth_type="none",
            argv_template=(executable, "{{task}}"),
            environment_names=("SAFE_ENV",),
            permission_mode="workspace-only-v1",
            output_mode="jsonl-v1",
        )
        for index, system_id in enumerate(("code-operator", "claude-code", "kimi-code"))
    )


def test_manifest_uses_one_local_rng_for_complete_frozen_order() -> None:
    import random

    from evals.agent_comparison.manifest import SEED, SYSTEMS, TASKS, build_manifest

    manifest = build_manifest(systems=_test_systems())
    rng = random.Random(SEED)
    expected_pilot = []
    systems = list(SYSTEMS)
    rng.shuffle(systems)
    for index, system_id in enumerate(systems):
        expected_pilot.append(("pilot", "A", system_id, "T1", 1, index))
    expected_formal = []
    order_index = len(expected_pilot)
    for task_id in TASKS:
        for replicate in (1,):
            systems = list(SYSTEMS)
            rng.shuffle(systems)
            for system_id in systems:
                expected_formal.append(("formal", "A", system_id, task_id, replicate, order_index))
                order_index += 1
    assert [tuple(cell.__dict__.values()) for cell in manifest.pilot] == expected_pilot
    assert [tuple(cell.__dict__.values()) for cell in manifest.formal] == expected_formal


def test_manifest_rejects_schedule_order_and_track_gate_mutations() -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, validate_before_pilot, validate_manifest

    manifest = build_manifest(systems=_test_systems())
    assert validate_manifest(replace(manifest, pilot=tuple(reversed(manifest.pilot))))
    assert validate_manifest(replace(manifest, formal=(manifest.formal[1], manifest.formal[0]) + manifest.formal[2:]))
    assert validate_manifest(replace(manifest, formal=(replace(manifest.formal[0], order_index=99),) + manifest.formal[1:]))
    assert validate_before_pilot(manifest) == ("track-b-not-frozen-before-pilot",)


def test_manifest_validation_rejects_all_unsafe_config_shapes_without_raising() -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, validate_manifest
    from evals.agent_comparison.schema import FrozenManifest, RunCell

    manifest = build_manifest(systems=_test_systems())
    malformed_fields = (
        "system_id",
        "cli_version",
        "executable_sha256",
        "model",
        "auth_type",
        "argv_template",
        "environment_names",
        "permission_mode",
        "output_mode",
    )
    for field in malformed_fields:
        config = replace(manifest.systems[0], **{field: []})
        candidate = replace(manifest, systems=(config,) + manifest.systems[1:])
        assert validate_manifest(candidate)
    for field in ("phase", "track", "system_id", "task_id", "replicate", "order_index"):
        malformed_cells = replace(manifest.formal[0], **{field: []})
        assert validate_manifest(replace(manifest, formal=(malformed_cells,) + manifest.formal[1:]))
    for field, value in (("systems", None), ("pilot", None), ("formal", None), ("task_hashes", None), ("track_b_status", [])):
        candidate = FrozenManifest(
            schema_version=manifest.schema_version,
            study_id=manifest.study_id,
            seed=manifest.seed,
            timeout_seconds=manifest.timeout_seconds,
            systems=manifest.systems,
            task_hashes=manifest.task_hashes,
            pilot=manifest.pilot,
            formal=manifest.formal,
            track_b_status=manifest.track_b_status,
        )
        object.__setattr__(candidate, field, value)
        assert validate_manifest(candidate)


def test_manifest_rejects_credentials_shell_placeholders_and_bad_hashes() -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, validate_manifest

    manifest = build_manifest(systems=_test_systems())
    base = manifest.systems[0]
    bad_configs = (
        replace(base, argv_template=(base.argv_template[0], "x;y")),
        replace(base, argv_template=(base.argv_template[0], "shell=True")),
        replace(base, argv_template=(base.argv_template[0], "--token", "secret-value")),
        replace(base, argv_template=(base.argv_template[0], "token=secret-value")),
        replace(base, environment_names=("TOKEN=secret-value",)),
        replace(base, argv_template=("relative.exe",)),
        replace(base, argv_template=("",)),
        replace(base, cli_version="placeholder"),
        replace(base, model="unknown"),
        replace(base, permission_mode="TODO"),
        replace(base, output_mode="TBD"),
        replace(base, auth_type="api-key"),
        replace(base, environment_names=("bad-name",)),
        replace(base, executable_sha256="0" * 64),
    )
    for config in bad_configs:
        assert validate_manifest(replace(manifest, systems=(config,) + manifest.systems[1:]))
    for task_id in ("T1", "T2", "T3"):
        for component in ("prompt", "project", "visible", "hidden", "reference"):
            hashes = {key: dict(value) for key, value in manifest.task_hashes.items()}
            del hashes[task_id][component]
            assert validate_manifest(replace(manifest, task_hashes=hashes))
            hashes[task_id][component] = "A" * 64
            assert validate_manifest(replace(manifest, task_hashes=hashes))


def test_manifest_requires_explicit_real_systems_and_exact_fixture_hashes() -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, validate_manifest

    with pytest.raises(ValueError, match="explicitly"):
        build_manifest()
    manifest = build_manifest(systems=_test_systems())
    hashes = {key: dict(value) for key, value in manifest.task_hashes.items()}
    hashes["T1"]["prompt"] = "a" * 64
    assert any("task-hash-mismatch:T1:prompt" == violation for violation in validate_manifest(replace(manifest, task_hashes=hashes)))


def test_manifest_track_b_status_has_three_states_and_pilot_gate() -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, freeze_track_b_status, validate_before_pilot, validate_manifest

    manifest = build_manifest(systems=_test_systems())
    assert freeze_track_b_status(True) == "READY"
    assert freeze_track_b_status(False) == "NOT_RUN_MODEL_MISMATCH"
    assert validate_manifest(replace(manifest, track_b_status="later"))
    assert validate_before_pilot(manifest)
    assert validate_manifest(replace(manifest, track_b_status="READY"), pilot_started=True) == ()
    with pytest.raises(ValueError, match="before Pilot"):
        freeze_track_b_status(True, pilot_started=True)


def test_manifest_task_hashes_are_deeply_immutable_and_canonical_stable() -> None:
    from evals.agent_comparison.manifest import build_manifest, canonical_json, canonical_sha256

    manifest = build_manifest(systems=_test_systems())
    before_bytes = canonical_json(manifest)
    before_hash = canonical_sha256(manifest)
    with pytest.raises(TypeError):
        manifest.task_hashes["T1"]["prompt"] = "a" * 64
    with pytest.raises(TypeError):
        manifest.task_hashes["T1"] = {}
    assert canonical_json(manifest) == before_bytes
    assert canonical_sha256(manifest) == before_hash


@pytest.mark.parametrize("field", ("schema_version", "seed", "timeout_seconds"))
def test_manifest_fixed_integer_fields_reject_bool_and_float(field: str) -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, validate_manifest

    manifest = build_manifest(systems=_test_systems())
    assert validate_manifest(replace(manifest, **{field: True}))
    assert validate_manifest(replace(manifest, **{field: 1.0}))


@pytest.mark.parametrize("field", ("cli_version", "model", "permission_mode", "output_mode"))
@pytest.mark.parametrize("value", ("token=secret", "Bearer secret", "api_key=secret", "password=secret"))
def test_manifest_metadata_rejects_credential_like_values(field: str, value: str) -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, validate_manifest

    manifest = build_manifest(systems=_test_systems())
    config = replace(manifest.systems[0], **{field: value})
    assert validate_manifest(replace(manifest, systems=(config,) + manifest.systems[1:]))


def test_manifest_rejects_non_preregistered_seed() -> None:
    from evals.agent_comparison.manifest import build_manifest

    with pytest.raises(ValueError, match="seed"):
        build_manifest(systems=_test_systems(), seed=7)


@pytest.mark.parametrize("model_matches", ("false", 1, 0, None))
def test_freeze_track_b_status_requires_a_real_bool(model_matches: object) -> None:
    from evals.agent_comparison.manifest import freeze_track_b_status

    with pytest.raises((TypeError, ValueError), match="bool"):
        freeze_track_b_status(model_matches)


@pytest.mark.parametrize(
    ("mutation", "expected_tag"),
    (
        ("unknown-system", "unknown-system:bogus-system"),
        ("unknown-task", "unknown-task:T9"),
        ("unknown-replicate", "unknown-replicate:3"),
        ("duplicate-formal", "duplicate-cell"),
        ("missing-formal", "missing-formal-cell"),
    ),
)
def test_manifest_rejects_each_formal_cell_completeness_violation(mutation: str, expected_tag: str) -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, validate_manifest

    manifest = build_manifest(systems=_test_systems())
    if mutation == "unknown-system":
        candidate = replace(manifest, formal=(replace(manifest.formal[0], system_id="bogus-system"),) + manifest.formal[1:])
    elif mutation == "unknown-task":
        candidate = replace(manifest, formal=(replace(manifest.formal[0], task_id="T9"),) + manifest.formal[1:])
    elif mutation == "unknown-replicate":
        candidate = replace(manifest, formal=(replace(manifest.formal[0], replicate=3),) + manifest.formal[1:])
    elif mutation == "duplicate-formal":
        candidate = replace(manifest, formal=(manifest.formal[0], manifest.formal[0]) + manifest.formal[2:])
    else:
        candidate = replace(manifest, formal=manifest.formal[:-1])
    assert expected_tag in validate_manifest(candidate)


def test_task3_workspace_builder_creates_clean_visible_only_git_baseline() -> None:
    from dataclasses import FrozenInstanceError, is_dataclass

    from evals.agent_comparison.workspace import RunWorkspace, create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-workspace-") as directory:
        workspace = create_run_workspace("T3", Path(directory) / "run")
        assert isinstance(workspace, RunWorkspace)
        assert is_dataclass(workspace)
        assert workspace.root == (Path(directory) / "run").resolve()
        assert workspace.baseline_commit
        assert len(workspace.initial_tree_sha256) == 64
        assert set(workspace.initial_file_sha256) == {
            "events.py",
            "levels.py",
            "tests/test_events.py",
        }
        assert not list(workspace.root.rglob("*grader*"))
        assert not (workspace.root / "tests" / "test_hidden_events.py").exists()
        assert (workspace.root / ".git").is_dir()
        assert (
            __import__("subprocess").run(
                ["git", "-C", str(workspace.root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            == ""
        )
        with pytest.raises(FrozenInstanceError):
            workspace.root = Path(directory)  # type: ignore[misc]


def test_task3_grader_marks_reference_solution_resolved() -> None:
    from evals.agent_comparison.fixture_validator import REFERENCE_BYTES
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-grade-") as directory:
        workspace = create_run_workspace("T3", Path(directory) / "run")
        for relative, content in REFERENCE_BYTES["T3"].items():
            (workspace.root / relative).write_bytes(content)
        result = grade_workspace("T3", workspace)
        assert result.resolved
        assert result.primary_failure is None
        assert result.forbidden_changes == ()
        assert result.regression is False
        assert result.tests_observed
        assert result.changed_files == ("events.py", "levels.py")


def test_task1_grader_ignores_pytest_generated_cache_files() -> None:
    import subprocess
    import sys

    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-cache-") as directory:
        workspace = create_run_workspace("T1", Path(directory) / "run")
        (workspace.root / "ranges.py").write_bytes(fixture_validator.REFERENCE_BYTES["T1"]["ranges.py"])
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_ranges.py"],
            cwd=workspace.root,
            check=True,
            capture_output=True,
            text=True,
        )
        cache_paths = {
            path.relative_to(workspace.root).as_posix()
            for cache_name in ("__pycache__", ".pytest_cache")
            for path in (workspace.root / cache_name,)
            if path.exists()
        }
        assert cache_paths

        result = grade_workspace("T1", workspace)

        assert result.primary_failure != "SCOPE_VIOLATION"
        assert result.forbidden_changes == ()
        assert not any(
            "__pycache__" in path.split("/")
            or ".pytest_cache" in path.split("/")
            or path.endswith(".pyc")
            for path in result.changed_files
        )


def test_task3_grader_prioritizes_forbidden_visible_test_change() -> None:
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-scope-") as directory:
        workspace = create_run_workspace("T3", Path(directory) / "run")
        visible = workspace.root / "tests" / "test_events.py"
        visible.write_text(visible.read_text(encoding="utf-8") + "\n# forbidden\n", encoding="utf-8")
        result = grade_workspace("T3", workspace)
        assert result.primary_failure == "SCOPE_VIOLATION"
        assert "tests/test_events.py" in result.forbidden_changes


@pytest.mark.parametrize("task_id", ("T1", "T2", "T3"))
def test_task3_reference_grading_is_resolved_for_every_task(task_id: str) -> None:
    from evals.agent_comparison.fixture_validator import REFERENCE_BYTES
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-reference-") as directory:
        workspace = create_run_workspace(task_id, Path(directory) / "run")
        for relative, content in REFERENCE_BYTES[task_id].items():
            target = workspace.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if task_id == "T2":
            (workspace.root / "tests" / "test_first_attempt.py").write_text(
                "from retry import retry_delay\n\ndef test_first_attempt_regression():\n    assert retry_delay(1) == 0.5\n",
                encoding="utf-8",
            )
        assert grade_workspace(task_id, workspace).resolved


def test_task3_grade_result_is_frozen_and_file_hashes_are_deeply_frozen() -> None:
    from dataclasses import FrozenInstanceError

    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-frozen-") as directory:
        workspace = create_run_workspace("T1", Path(directory) / "run")
        with pytest.raises(TypeError):
            workspace.initial_file_sha256["ranges.py"] = "x"  # type: ignore[index]
        with pytest.raises(FrozenInstanceError):
            workspace.baseline_commit = "x"  # type: ignore[misc]


@pytest.mark.parametrize("mutation", ("unstaged", "staged", "untracked"))
def test_task3_patch_hash_changes_for_every_git_visible_mutation(mutation: str) -> None:
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-patch-hash-") as directory:
        workspace = create_run_workspace("T3", Path(directory) / "run")
        before = grade_workspace("T3", workspace).patch_sha256
        if mutation == "untracked":
            (workspace.root / "tests" / "test_added.py").write_text("def test_added(): assert True\n", encoding="utf-8")
        else:
            target = workspace.root / "levels.py"
            target.write_text(target.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
            if mutation == "staged":
                __import__("subprocess").run(["git", "-C", str(workspace.root), "add", "levels.py"], check=True)
        status_before_grade = __import__("subprocess").run(
            ["git", "-C", str(workspace.root), "status", "--porcelain=v1", "-z"],
            capture_output=True,
            check=True,
        ).stdout
        after = grade_workspace("T3", workspace).patch_sha256
        assert before != after
        status_after = __import__("subprocess").run(
            ["git", "-C", str(workspace.root), "status", "--porcelain=v1", "-z"],
            capture_output=True,
            check=True,
        ).stdout
        assert status_after == status_before_grade


@pytest.mark.parametrize("bad", (None, [], "missing-baseline"))
def test_task3_grader_rejects_malformed_workspace_without_raising(bad: object) -> None:
    from dataclasses import replace

    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-invalid-workspace-") as directory:
        workspace = create_run_workspace("T1", Path(directory) / "run")
        candidate = replace(workspace, root=bad) if bad is None or isinstance(bad, list) else replace(workspace, baseline_commit=bad)
        assert grade_workspace("T1", candidate).primary_failure == "TOOL_OR_INFRA_FAILURE"


def test_task3_t2_final_failing_added_test_prevents_resolution() -> None:
    from evals.agent_comparison.fixture_validator import REFERENCE_BYTES
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-t2-final-fail-") as directory:
        workspace = create_run_workspace("T2", Path(directory) / "run")
        (workspace.root / "retry.py").write_bytes(REFERENCE_BYTES["T2"]["retry.py"])
        (workspace.root / "tests" / "test_good.py").write_text(
            "from retry import retry_delay\n\ndef test_first(): assert retry_delay(1) == 0.5\n",
            encoding="utf-8",
        )
        (workspace.root / "tests" / "test_final_failure.py").write_text(
            "from retry import retry_delay\n\ndef test_unresolved(): assert retry_delay(1) == 0.6\n",
            encoding="utf-8",
        )
        result = grade_workspace("T2", workspace)
        assert not result.resolved
        assert result.primary_failure == "DID_NOT_TEST"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (("incorrect", "INCORRECT_PATCH"), ("nochange", "LOCALIZATION_FAILURE")),
)
def test_task3_grader_primary_for_t1_incorrect_and_nochange(mutation: str, expected: str) -> None:
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-t1-primary-") as directory:
        workspace = create_run_workspace("T1", Path(directory) / "run")
        if mutation == "incorrect":
            target = workspace.root / "ranges.py"
            target.write_text(target.read_text(encoding="utf-8").replace("start + size < total", "start + size <= total"), encoding="utf-8")
        result = grade_workspace("T1", workspace)
        assert result.primary_failure == expected


def test_task3_grader_primary_for_deleted_visible_test_is_scope_violation() -> None:
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-delete-visible-") as directory:
        workspace = create_run_workspace("T1", Path(directory) / "run")
        (workspace.root / "tests" / "test_ranges.py").unlink()
        result = grade_workspace("T1", workspace)
        assert result.primary_failure == "SCOPE_VIOLATION"


def test_task3_grader_primary_for_allowed_syntax_error_is_incorrect_patch() -> None:
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-syntax-") as directory:
        workspace = create_run_workspace("T1", Path(directory) / "run")
        (workspace.root / "ranges.py").write_text("def broken(:\n", encoding="utf-8")
        result = grade_workspace("T1", workspace)
        assert result.primary_failure == "INCORRECT_PATCH"


def test_task3_grader_primary_for_t2_correct_retry_without_added_test_is_did_not_test() -> None:
    from evals.agent_comparison.fixture_validator import REFERENCE_BYTES
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-t2-no-test-") as directory:
        workspace = create_run_workspace("T2", Path(directory) / "run")
        (workspace.root / "retry.py").write_bytes(REFERENCE_BYTES["T2"]["retry.py"])
        result = grade_workspace("T2", workspace)
        assert result.primary_failure == "DID_NOT_TEST"


def test_task3_grader_primary_for_t2_cap_only_test_is_did_not_test() -> None:
    from evals.agent_comparison.fixture_validator import REFERENCE_BYTES
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-t2-toothless-") as directory:
        workspace = create_run_workspace("T2", Path(directory) / "run")
        (workspace.root / "retry.py").write_bytes(REFERENCE_BYTES["T2"]["retry.py"])
        (workspace.root / "tests" / "test_cap_only.py").write_text(
            "from retry import retry_delay\n\ndef test_cap_only(): assert retry_delay(10) == 8.0\n",
            encoding="utf-8",
        )
        result = grade_workspace("T2", workspace)
        assert result.primary_failure == "DID_NOT_TEST"


def test_task3_grader_scope_precedes_regression() -> None:
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-precedence-scope-") as directory:
        workspace = create_run_workspace("T1", Path(directory) / "run")
        (workspace.root / "ranges.py").write_text("def broken(:\n", encoding="utf-8")
        (workspace.root / "README.md").write_text("forbidden\n", encoding="utf-8")
        result = grade_workspace("T1", workspace)
        assert result.primary_failure == "SCOPE_VIOLATION"


def test_task3_grader_timeout_precedes_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-precedence-timeout-") as directory:
        workspace = create_run_workspace("T1", Path(directory) / "run")
        monkeypatch.setattr(
            "evals.agent_comparison.grader._run_grading",
            lambda task_id, root: (_ for _ in ()).throw(subprocess.TimeoutExpired("pytest", 30)),
        )
        result = grade_workspace("T1", workspace)
        assert result.primary_failure == "TIMEOUT"


def test_task3_t2_artifact_timeout_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-artifact-timeout-") as directory:
        workspace = create_run_workspace("T2", Path(directory) / "run")
        monkeypatch.setattr(
            "evals.agent_comparison.grader.fixture_validator.validate_retry_artifact",
            lambda root: (_ for _ in ()).throw(subprocess.TimeoutExpired("pytest", 30)),
        )
        result = grade_workspace("T2", workspace)
        assert result.primary_failure == "TIMEOUT"


@pytest.mark.parametrize(
    ("flags", "expected"),
    (
        ({"scope": True, "infra": True, "timeout": True, "regression": True, "did_not_test": True, "localization": True, "incorrect": True}, "SCOPE_VIOLATION"),
        ({"infra": True, "timeout": True, "regression": True, "did_not_test": True, "localization": True, "incorrect": True}, "TOOL_OR_INFRA_FAILURE"),
        ({"timeout": True, "regression": True, "did_not_test": True, "localization": True, "incorrect": True}, "TIMEOUT"),
        ({"regression": True, "did_not_test": True, "localization": True, "incorrect": True}, "REGRESSION"),
        ({"did_not_test": True, "localization": True, "incorrect": True}, "DID_NOT_TEST"),
        ({"localization": True, "incorrect": True}, "LOCALIZATION_FAILURE"),
        ({"incorrect": True}, "INCORRECT_PATCH"),
        ({}, None),
    ),
)
def test_task3_primary_failure_selector_has_frozen_precedence(flags: dict[str, bool], expected: str | None) -> None:
    from evals.agent_comparison.grader import _select_primary_failure

    assert _select_primary_failure(**flags) == expected


def test_task3_t2_invalid_utf8_added_test_is_scope_violation() -> None:
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-invalid-utf8-") as directory:
        workspace = create_run_workspace("T2", Path(directory) / "run")
        (workspace.root / "tests" / "test_invalid.py").write_bytes(b"def test_invalid():\n    \xff\n")
        result = grade_workspace("T2", workspace)
        assert result.primary_failure == "SCOPE_VIOLATION"


@pytest.mark.parametrize("task_id", ("T1", "T2", "T3"))
def test_task3_builder_has_exact_visible_set_local_identity_and_one_commit(task_id: str) -> None:
    import subprocess

    from evals.agent_comparison.workspace import _VISIBLE_FILES, create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-builder-matrix-") as directory:
        destination = Path(directory) / "run"
        workspace = create_run_workspace(task_id, destination)
        visible = tuple(sorted(path.relative_to(workspace.root).as_posix() for path in workspace.root.rglob("*") if path.is_file() and ".git" not in path.parts))
        assert visible == tuple(sorted(_VISIBLE_FILES[task_id]))
        assert subprocess.run(["git", "-C", str(workspace.root), "config", "--local", "user.name"], capture_output=True, text=True, check=True).stdout.strip() == "agent-comparison-local"
        assert subprocess.run(["git", "-C", str(workspace.root), "config", "--local", "user.email"], capture_output=True, text=True, check=True).stdout.strip() == "agent-comparison@localhost"
        assert subprocess.run(["git", "-C", str(workspace.root), "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() == "1"
        assert subprocess.run(["git", "-C", str(workspace.root), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout == ""
        for artifact in ("grader.py", "hidden_test.py", "reference.patch", ".env", "AGENTS.txt", "instructions.md", "plugin.json", "mcp.json", "session.json", "transcript.txt"):
            assert not (workspace.root / artifact).exists()


def test_task3_builder_rejects_nonempty_destination_and_targeted_root_link_mock() -> None:
    from unittest.mock import patch

    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-builder-safety-") as directory:
        destination = Path(directory) / "run"
        destination.mkdir()
        (destination / "existing.txt").write_text("x", encoding="utf-8")
        with pytest.raises(ValueError, match="new or empty"):
            create_run_workspace("T1", destination)
        empty = Path(directory) / "empty"
        empty.mkdir()
        import evals.agent_comparison.workspace as workspace_module

        original = workspace_module._is_reparse_or_symlink
        with patch.object(workspace_module, "_is_reparse_or_symlink", side_effect=lambda path: path == empty or original(path)):
            with pytest.raises(ValueError, match="symlink"):
                create_run_workspace("T1", empty)


def test_task3_grader_detects_gitignored_scope_file() -> None:
    from evals.agent_comparison.grader import grade_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    with tempfile.TemporaryDirectory(prefix="agent-comparison-ignored-scope-") as directory:
        workspace = create_run_workspace("T1", Path(directory) / "run")
        exclude = workspace.root / ".git" / "info" / "exclude"
        exclude.write_text(exclude.read_text(encoding="utf-8") + "\nconftest.py\n", encoding="utf-8")
        (workspace.root / "conftest.py").write_text("def pytest_configure(): pass\n", encoding="utf-8")
        result = grade_workspace("T1", workspace)
        assert result.primary_failure == "SCOPE_VIOLATION"
        assert "conftest.py" in result.forbidden_changes


def test_adapter_materializes_only_frozen_placeholders(tmp_path: Path) -> None:
    from evals.agent_comparison.adapters import materialize_argv
    from evals.agent_comparison.schema import SystemConfig

    executable = Path(__import__("sys").executable).resolve()
    config = SystemConfig(
        "code-operator", "1", _executable_hash(str(executable)), "m", "none",
        (str(executable), "--workspace={workspace}", "{task}"), (), "p", "jsonl",
    )
    assert materialize_argv(config, workspace=tmp_path, task="T1; echo unsafe") == (
        str(executable), f"--workspace={tmp_path}", "T1; echo unsafe"
    )


def test_adapter_uses_literal_cwd_environment_and_structured_usage(tmp_path: Path) -> None:
    import json
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    code = (
        "import json, os, pathlib, sys; "
        "pathlib.Path('probe.json').write_text('|'.join((os.getcwd(), os.getenv('SAFE_ENV',''), "
        "os.getenv('BAD_ENV',''), sys.argv[1]))); "
        "print(json.dumps(dict(event='tool', tool='run_command', "
        "arguments=json.dumps(dict(argv=['python','-m','pytest','-q']))))); "
        "print(json.dumps(dict(event='result', usage=dict(total_tokens=7))))"
    )
    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", code, "{task}"), ("SAFE_ENV",), "p", "jsonl",
    )
    result = run_adapter(
        config,
        workspace=tmp_path,
        task="literal; & not shell",
        timeout_seconds=2,
        source_environment={"SAFE_ENV": "allowed", "BAD_ENV": "blocked"},
    )
    probe = (tmp_path / "probe.json").read_text(encoding="utf-8").split("|")
    assert result.stop_reason == "COMPLETED"
    assert result.tests_observed is True
    assert result.usage == {"total_tokens": 7}
    assert probe == [str(tmp_path), "allowed", "", "literal; & not shell"]


def test_adapter_does_not_infer_pytest_from_final_prose_or_return_output(tmp_path: Path) -> None:
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", "print('final answer: pytest passed; raw-secret')"),
        (), "p", "jsonl",
    )
    result = run_adapter(
        config,
        workspace=tmp_path,
        task="T1",
        timeout_seconds=2,
        source_environment={"SECRET_VALUE": "raw-secret"},
    )
    assert result.stop_reason == "INVALID_OUTPUT"
    assert result.tests_observed is False
    assert result.usage == "unavailable"
    assert "raw-secret" not in repr(result)


def test_adapter_adds_explicit_credential_name_but_not_unapproved_secret(tmp_path: Path) -> None:
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    code = (
        "import os, pathlib; pathlib.Path('env.txt').write_text('|'.join(("
        "os.getenv('CODE_OPERATOR_API_KEY',''), os.getenv('UNAPPROVED_SECRET','')))); "
        "print('not-json')"
    )
    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", code), ("CODE_OPERATOR_API_KEY",), "p", "jsonl",
    )
    result = run_adapter(
        config,
        workspace=tmp_path,
        task="T1",
        timeout_seconds=2,
        source_environment={
            "CODE_OPERATOR_API_KEY": "approved-secret",
            "UNAPPROVED_SECRET": "unapproved-secret",
        },
    )
    assert (tmp_path / "env.txt").read_text(encoding="utf-8") == "approved-secret|"
    assert result.stop_reason == "INVALID_OUTPUT"
    assert "approved-secret" not in repr(result)
    assert "unapproved-secret" not in repr(result)


@pytest.mark.parametrize("token", ("{{task}}", "{workspace:}", "{task}}", "{unknown}", "prefix{"))
def test_adapter_rejects_malformed_or_unknown_placeholders(tmp_path: Path, token: str) -> None:
    import sys

    from evals.agent_comparison.adapters import materialize_argv
    from evals.agent_comparison.schema import SystemConfig

    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, token), (), "p", "jsonl",
    )
    with pytest.raises(ValueError, match="placeholder"):
        materialize_argv(config, workspace=tmp_path, task="T1{literal}")


def test_adapter_rejects_nonexistent_executable(tmp_path: Path) -> None:
    import sys

    from evals.agent_comparison.adapters import materialize_argv
    from evals.agent_comparison.schema import SystemConfig

    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (str(tmp_path / "missing-executable"),), (), "p", "jsonl",
    )
    with pytest.raises(ValueError, match="executable"):
        materialize_argv(config, workspace=tmp_path, task="T1")


def test_adapter_nonzero_machine_event_and_secret_usage_are_safe(tmp_path: Path) -> None:
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    code = (
        "import json, sys; print(json.dumps(dict(event='result', usage="
        "dict(model='approved-secret', total_tokens=9)))); sys.exit(4)"
    )
    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", code), ("SAFE_ENV",), "p", "jsonl",
    )
    result = run_adapter(
        config,
        workspace=tmp_path,
        task="T1",
        timeout_seconds=2,
        source_environment={"SAFE_ENV": "approved-secret"},
    )
    assert result.stop_reason == "NONZERO_EXIT"
    assert result.usage == {"total_tokens": 9}
    assert "approved-secret" not in repr(result)


@pytest.mark.parametrize("code", ("print(chr(123)+chr(125))", "print(chr(123)+chr(34)+'command'+chr(34)+':'+chr(34)+'pytest'+chr(34)+chr(125))"))
def test_adapter_rejects_json_without_frozen_event_shape(tmp_path: Path, code: str) -> None:
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", code), (), "p", "jsonl-v1",
    )
    result = run_adapter(config, workspace=tmp_path, task="T1", timeout_seconds=2, source_environment={})
    assert result.stop_reason == "INVALID_OUTPUT"
    assert result.tests_observed is False
    assert result.usage == "unavailable"


def test_adapter_timeout_kills_parent_and_child(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys
    import time

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig
    from tests.test_golden_eval import _pid_is_alive

    marker = tmp_path / "child.pid"
    child_code = (
        "import pathlib, time; pathlib.Path(" + repr(str(marker)) + ").write_text(str(__import__('os').getpid())); time.sleep(30)"
    )
    parent_code = (
        "import pathlib, subprocess, sys, time; child=subprocess.Popen([sys.executable, '-c', "
        + repr(child_code)
        + "]); time.sleep(30)"
    )
    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", parent_code), (), "p", "jsonl",
    )
    child_pid: int | None = None
    try:
        result = run_adapter(
            config,
            workspace=tmp_path,
            task="T1",
            timeout_seconds=1,
            source_environment={},
        )
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        child_pid = int(marker.read_text(encoding="utf-8"))
        assert result.timed_out is True
        assert result.stop_reason == "TIMEOUT"
        deadline = time.monotonic() + 2
        while _pid_is_alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _pid_is_alive(child_pid)
    finally:
        if child_pid is not None and _pid_is_alive(child_pid):
            subprocess.run(
                (["taskkill.exe", "/PID", str(child_pid), "/T", "/F"] if os.name == "nt" else ["kill", "-KILL", str(child_pid)]),
                shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=2,
            )


def test_code_operator_audit_is_trusted_and_cleaned_after_human_output(tmp_path: Path) -> None:
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    code = (
        "import json, pathlib; d=pathlib.Path('.code-operator'); d.mkdir(); "
        "tool=dict(timestamp='t', event='tool', tool='run_command', "
        "arguments=json.dumps(dict(argv=['python','-m','pytest','-q'])), ok=True, error_code=None, exit_code=0); "
        "run=dict(timestamp='t', event='run', usage_available=False, stop_reason='COMPLETED'); "
        "(d/'audit.jsonl').write_text(json.dumps(tool)+'\\n'+json.dumps(run)); print('human final answer')"
    )
    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", code), (), "p", "code-operator-audit-v1",
    )
    result = run_adapter(config, workspace=tmp_path, task="T1", timeout_seconds=2, source_environment={})
    assert result.stop_reason == "COMPLETED"
    assert result.tests_observed is True
    assert not (tmp_path / ".code-operator").exists()


def test_code_operator_malformed_audit_is_not_trusted(tmp_path: Path) -> None:
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    code = (
        "import pathlib; d=pathlib.Path('.code-operator'); d.mkdir(); "
        "(d/'audit.jsonl').write_text('malformed-audit'); print('human final')"
    )
    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", code), (), "p", "code-operator-audit-v1",
    )
    result = run_adapter(config, workspace=tmp_path, task="T1", timeout_seconds=2, source_environment={})
    assert result.stop_reason == "INVALID_OUTPUT"
    assert result.tests_observed is False
    assert not (tmp_path / ".code-operator").exists()


def test_code_operator_nonzero_exit_precedes_completed_audit(tmp_path: Path) -> None:
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    code = (
        "import json, pathlib, sys; d=pathlib.Path('.code-operator'); d.mkdir(); "
        "run=dict(timestamp='t', event='run', usage_available=False, stop_reason='COMPLETED'); "
        "(d/'audit.jsonl').write_text(json.dumps(run)); sys.exit(4)"
    )
    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", code), (), "p", "code-operator-audit-v1",
    )
    result = run_adapter(config, workspace=tmp_path, task="T1", timeout_seconds=2, source_environment={})
    assert result.returncode == 4
    assert result.stop_reason == "NONZERO_EXIT"


def test_code_operator_accepts_context_limit_audit_on_zero_exit(tmp_path: Path) -> None:
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    code = (
        "import json, pathlib; d=pathlib.Path('.code-operator'); d.mkdir(); "
        "run=dict(timestamp='t', event='run', usage_available=False, stop_reason='CONTEXT_LIMIT'); "
        "(d/'audit.jsonl').write_text(json.dumps(run))"
    )
    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", code), (), "p", "code-operator-audit-v1",
    )
    result = run_adapter(config, workspace=tmp_path, task="T1", timeout_seconds=2, source_environment={})
    assert result.returncode == 0
    assert result.stop_reason == "CONTEXT_LIMIT"


@pytest.mark.parametrize("ok, error_code, exit_code, expected", ((False, "COMMAND_DENIED", None, False), (False, None, 1, True)))
def test_code_operator_audit_only_counts_actual_pytest_invocation(
    tmp_path: Path, ok: bool, error_code: str | None, exit_code: int | None, expected: bool
) -> None:
    import sys

    from evals.agent_comparison.adapters import run_adapter
    from evals.agent_comparison.schema import SystemConfig

    code = (
        "import json, pathlib; d=pathlib.Path('.code-operator'); d.mkdir(); "
        f"tool=dict(timestamp='t', event='tool', tool='run_command', arguments=json.dumps(dict(argv=['python','-m','pytest','-q'])), ok={ok!r}, error_code={error_code!r}, exit_code={exit_code!r}); "
        "run=dict(timestamp='t', event='run', usage_available=False, stop_reason='COMPLETED'); "
        "(d/'audit.jsonl').write_text(json.dumps(tool)+'\\n'+json.dumps(run))"
    )
    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", code), (), "p", "code-operator-audit-v1",
    )
    result = run_adapter(config, workspace=tmp_path, task="T1", timeout_seconds=2, source_environment={})
    assert result.tests_observed is expected


def test_adapter_exception_does_not_expose_secret(monkeypatch, tmp_path: Path) -> None:
    import sys

    import evals.agent_comparison.adapters as adapters
    from evals.agent_comparison.schema import SystemConfig

    config = SystemConfig(
        "code-operator", "1", _executable_hash(sys.executable), "m", "none",
        (sys.executable, "-c", "print('ok')"), (), "p", "jsonl-v1",
    )
    monkeypatch.setattr(adapters, "run_process", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret-value")))
    result = adapters.run_adapter(config, workspace=tmp_path, task="T1", timeout_seconds=2, source_environment={"X_SECRET": "secret-value"})
    assert result.stop_reason == "INFRA_ERROR"
    assert "secret-value" not in repr(result)


def test_orchestration_runs_exact_pilot_serially_grades_and_cleans(tmp_path: Path) -> None:
    import json

    from evals.agent_comparison.adapters import AdapterResult
    from evals.agent_comparison.grader import GradeResult
    from evals.agent_comparison.manifest import build_manifest, canonical_sha256
    from evals.agent_comparison.run_study import run_phase
    from evals.agent_comparison.workspace import RunWorkspace

    manifest = build_manifest(
        systems=_test_systems(),
        track_b_status="NOT_RUN_MODEL_MISMATCH",
    )
    report_path = tmp_path / "pilot.json"
    workspace_parent = tmp_path / "runs"
    active: set[Path] = set()
    events: list[tuple[str, str]] = []
    adapter_calls = 0

    def workspace_factory(task_id: str, destination: Path) -> RunWorkspace:
        assert task_id == "T1"
        assert destination not in active
        assert destination.is_dir() and not any(destination.iterdir())
        (destination / "synthetic.py").write_text("visible fixture", encoding="utf-8")
        active.add(destination)
        events.append(("create", destination.name))
        return RunWorkspace(destination, "baseline", "tree", {"synthetic.py": "file"})

    def adapter_runner(config, *, workspace, task, timeout_seconds, source_environment):
        nonlocal adapter_calls
        assert active == {workspace}
        assert task == (TASK_ROOT / "T1" / "task.txt").read_text(encoding="utf-8")
        assert timeout_seconds == 360
        assert source_environment == {"APPROVED_TOKEN": "do-not-report-this-secret"}
        events.append(("adapter", workspace.name))
        adapter_calls += 1
        if adapter_calls == 2:
            return AdapterResult(7, False, 0.25, False, "NONZERO_EXIT", "unavailable")
        return AdapterResult(0, False, 0.5, True, "COMPLETED", {"total_tokens": 12, "model": "unsafe-free-text"})

    def grader(task_id: str, workspace: RunWorkspace) -> GradeResult:
        assert task_id == "T1"
        assert active == {workspace.root}
        assert events[-1] == ("adapter", workspace.root.name)
        events.append(("grade", workspace.root.name))
        resolved = adapter_calls != 2
        return GradeResult(
            resolved, 2 if resolved else 0, 2, 2, 2, (), False, True,
            ("ranges.py",), 1, 1, f"{'a' if resolved else 'b'}" * 64,
            None if resolved else "INCORRECT_PATCH",
            ("F2P_2_OF_2",) if resolved else ("F2P_0_OF_2",),
        )

    def cleanup_workspace(path: Path) -> None:
        assert events[-1] == ("grade", path.name)
        assert path in active
        events.append(("cleanup", path.name))
        active.remove(path)
        shutil.rmtree(path)

    report = run_phase(
        manifest,
        phase="pilot",
        report_path=report_path,
        source_environment={"APPROVED_TOKEN": "do-not-report-this-secret"},
        workspace_parent=workspace_parent,
        workspace_factory=workspace_factory,
        adapter_runner=adapter_runner,
        grader=grader,
        cleanup_workspace=cleanup_workspace,
    )

    assert adapter_calls == 3
    assert not active
    assert [kind for kind, _ in events] == [
        "create", "adapter", "grade", "cleanup",
        "create", "adapter", "grade", "cleanup",
        "create", "adapter", "grade", "cleanup",
    ]
    workspace_names = [name for kind, name in events if kind == "create"]
    assert len(set(workspace_names)) == 3
    assert all(not (workspace_parent / name).exists() for name in workspace_names)
    assert [(row["system_id"], row["task_id"], row["replicate"], row["order_index"]) for row in report["rows"]] == [
        (cell.system_id, "T1", 1, cell.order_index) for cell in manifest.pilot
    ]
    assert report["manifest_sha256"] == canonical_sha256(manifest)
    assert all(row["manifest_sha256"] == report["manifest_sha256"] for row in report["rows"])
    assert report["rows"][1]["adapter"]["stop_reason"] == "NONZERO_EXIT"
    assert report["rows"][1]["grade"]["resolved"] is False
    assert report["rows"][0]["adapter"]["usage"] == {"total_tokens": 12}
    assert report["rows"][0]["task_hashes"] == dict(manifest.task_hashes["T1"])

    raw = report_path.read_text(encoding="utf-8")
    assert json.loads(raw) == report
    assert raw == json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    assert "do-not-report-this-secret" not in raw
    assert str(tmp_path) not in raw
    assert (TASK_ROOT / "T1" / "task.txt").read_text(encoding="utf-8") not in raw
    assert "visible fixture" not in raw
    assert "unsafe-free-text" not in raw


def test_orchestration_runs_nine_formal_cells_and_keeps_grader_as_primary(tmp_path: Path) -> None:
    from evals.agent_comparison.adapters import AdapterResult
    from evals.agent_comparison.grader import GradeResult
    from evals.agent_comparison.manifest import build_manifest
    from evals.agent_comparison.run_study import run_phase
    from evals.agent_comparison.workspace import RunWorkspace

    manifest = build_manifest(
        systems=_test_systems(),
        track_b_status="NOT_RUN_REDUNDANT_UNIFIED_TRACK_A",
    )
    calls: list[tuple[str, str]] = []

    def workspace_factory(task_id: str, destination: Path) -> RunWorkspace:
        calls.append(("create", task_id))
        return RunWorkspace(destination, "baseline", "tree", {"fixture.py": "hash"})

    def adapter_runner(config, *, workspace, task, timeout_seconds, source_environment):
        calls.append(("adapter", config.system_id))
        return AdapterResult(0, False, 0.1, False, "INVALID_OUTPUT", "unavailable")

    def grader(task_id: str, workspace: RunWorkspace) -> GradeResult:
        calls.append(("grade", task_id))
        return GradeResult(
            True, 2, 2, 2, 2, (), False, True, ("fixture.py",), 1, 0,
            "a" * 64, None, ("F2P_2_OF_2", "P2P_2_OF_2"),
        )

    report = run_phase(
        manifest,
        phase="formal",
        report_path=tmp_path / "formal.json",
        source_environment={},
        workspace_parent=tmp_path / "runs",
        workspace_factory=workspace_factory,
        adapter_runner=adapter_runner,
        grader=grader,
    )

    assert len(report["rows"]) == 9
    assert [
        (row["system_id"], row["task_id"], row["replicate"], row["order_index"])
        for row in report["rows"]
    ] == [
        (cell.system_id, cell.task_id, 1, cell.order_index)
        for cell in manifest.formal
    ]
    assert all(row["adapter"]["stop_reason"] == "INVALID_OUTPUT" for row in report["rows"])
    assert all(row["grade"]["resolved"] is True for row in report["rows"])
    assert [kind for kind, _ in calls].count("create") == 9
    assert [kind for kind, _ in calls].count("adapter") == 9
    assert [kind for kind, _ in calls].count("grade") == 9
    assert not tuple((tmp_path / "runs").glob("agent-comparison-formal-*"))


def test_orchestration_cli_accepts_formal_phase(tmp_path: Path) -> None:
    from evals.agent_comparison.manifest import build_manifest, canonical_json
    from evals.agent_comparison.run_study import main

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(canonical_json(build_manifest(
        systems=_test_systems(),
        track_b_status="NOT_RUN_REDUNDANT_UNIFIED_TRACK_A",
    )))
    observed: dict[str, object] = {}

    def phase_runner(manifest, **kwargs):
        observed.update(kwargs)
        return {"rows": []}

    assert main([
        "--manifest", str(manifest_path),
        "--phase", "formal",
        "--report", str(tmp_path / "formal.json"),
    ], phase_runner=phase_runner) == 0
    assert observed["phase"] == "formal"


def test_orchestration_refuses_existing_report_without_executing(tmp_path: Path) -> None:
    from evals.agent_comparison.manifest import build_manifest
    from evals.agent_comparison.run_study import run_phase

    report_path = tmp_path / "pilot.json"
    report_path.write_text("existing", encoding="utf-8")
    calls: list[str] = []

    def forbidden(*args, **kwargs):
        calls.append("called")
        raise AssertionError("execution must not start")

    with pytest.raises(FileExistsError):
        run_phase(
            build_manifest(systems=_test_systems(), track_b_status="READY"),
            phase="pilot",
            report_path=report_path,
            source_environment={},
            workspace_factory=forbidden,
            adapter_runner=forbidden,
            grader=forbidden,
            cleanup_workspace=forbidden,
        )
    assert not calls
    assert report_path.read_text(encoding="utf-8") == "existing"


def test_orchestration_cleanup_removes_real_git_workspace(tmp_path: Path) -> None:
    from evals.agent_comparison.run_study import _remove_workspace
    from evals.agent_comparison.workspace import create_run_workspace

    destination = tmp_path / "real-run"
    create_run_workspace("T1", destination)

    _remove_workspace(destination)

    assert not destination.exists()


def test_orchestration_cli_loads_only_canonical_manifest_and_scopes_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dataclasses import replace

    from evals.agent_comparison.manifest import build_manifest, canonical_json
    from evals.agent_comparison.run_study import load_manifest, main

    base = _test_systems()
    systems = (
        replace(base[0], environment_names=("SAFE_ENV", "PYTHONPATH")),
        replace(base[1], environment_names=("CLAUDE_MARKER",)),
        replace(base[2], environment_names=("KIMI_MARKER",)),
    )
    manifest = build_manifest(systems=systems, track_b_status="READY")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(canonical_json(manifest))
    report_path = tmp_path / "pilot.json"
    monkeypatch.setenv("SAFE_ENV", "safe-value")
    monkeypatch.setenv("PYTHONPATH", "ambient-path-must-not-pass")
    monkeypatch.setenv("CLAUDE_MARKER", "claude-value")
    monkeypatch.setenv("KIMI_MARKER", "kimi-value")
    monkeypatch.setenv("UNAPPROVED_SECRET", "must-not-pass")
    minimal_os = {
        "PATH": "synthetic-path",
        "PATHEXT": ".EXE;.CMD",
        "SYSTEMROOT": "C:\\Windows",
        "WINDIR": "C:\\Windows",
        "TEMP": "C:\\Temp",
        "TMP": "C:\\Temp",
        "HOME": "C:\\Users\\synthetic",
        "USERPROFILE": "C:\\Users\\synthetic",
        "LANG": "zh_CN.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    for name, value in minimal_os.items():
        monkeypatch.setenv(name, value)
    observed: dict[str, object] = {}

    def phase_runner(received, **kwargs):
        observed["manifest"] = received
        observed.update(kwargs)
        return {"rows": []}

    assert main(
        ["--manifest", str(manifest_path), "--phase", "pilot", "--report", str(report_path)],
        phase_runner=phase_runner,
    ) == 0
    assert load_manifest(manifest_path) == manifest
    assert observed["manifest"] == manifest
    assert observed["phase"] == "pilot"
    assert observed["report_path"] == report_path
    source = observed["source_environment"]
    assert isinstance(source, dict)
    assert source == {
        **minimal_os,
        "SAFE_ENV": "safe-value",
        "PYTHONPATH": str(Path(__file__).parents[1]),
        "CLAUDE_MARKER": "claude-value",
        "KIMI_MARKER": "kimi-value",
    }


@pytest.mark.parametrize(
    "payload",
    (
        b"{}",
        b'{"schema_version":1}',
        b'{\n  "schema_version": 1\n}',
        b'[]',
        b'not-json',
    ),
)
def test_orchestration_manifest_loader_fails_closed(payload: bytes, tmp_path: Path) -> None:
    from evals.agent_comparison.run_study import load_manifest

    path = tmp_path / "manifest.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="manifest"):
        load_manifest(path)
