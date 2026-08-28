from __future__ import annotations

import json
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.preflight_submission import (
    MAX_VIDEO_BYTES,
    build_parser,
    inspect_submission,
    main,
    validate_entry_metadata,
)


EXPECTED_NAME = "张三"


def make_archive(
    tmp_path: Path,
    *,
    archive_name: str = f"{EXPECTED_NAME}.zip",
    readme: str | None = "项目说明",
    video_name: str | None = "demo.mp4",
    extras: dict[str, bytes] | None = None,
) -> Path:
    archive = tmp_path / archive_name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        if readme is not None:
            bundle.writestr("README.txt", readme.encode("utf-8"))
        if video_name is not None:
            bundle.writestr(video_name, b"fake-mp4")
        for name, content in (extras or {}).items():
            bundle.writestr(name, content)
    return archive


def test_cli_requires_explicit_expected_name(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)

    with pytest.raises(SystemExit) as captured:
        build_parser().parse_args([str(archive)])

    assert captured.value.code == 2


def test_valid_archive_passes_with_manual_duration_warning_without_ffprobe(
    tmp_path: Path,
) -> None:
    archive = make_archive(tmp_path)

    report = inspect_submission(
        archive,
        expected_name=EXPECTED_NAME,
        ffprobe_path=None,
    )

    assert report.ok is True
    assert report.errors == ()
    assert report.warnings == ("未找到 ffprobe，请人工确认视频时长不超过 120 秒",)


def test_readme_over_1000_unicode_characters_is_rejected(tmp_path: Path) -> None:
    archive = make_archive(tmp_path, readme="中" * 1_001)

    report = inspect_submission(
        archive,
        expected_name=EXPECTED_NAME,
        ffprobe_path=None,
    )

    assert any("1000" in error and "1001" in error for error in report.errors)


def test_oversized_readme_is_rejected_before_full_member_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = make_archive(tmp_path, readme="x" * 4_004)

    def fail_if_read(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("oversized README must not be fully decompressed")

    monkeypatch.setattr(zipfile.ZipFile, "read", fail_if_read)

    report = inspect_submission(
        archive,
        expected_name=EXPECTED_NAME,
        ffprobe_path=None,
    )

    assert any("README.txt" in error and "1000" in error for error in report.errors)


def test_archive_filename_must_exactly_match_expected_name(tmp_path: Path) -> None:
    archive = make_archive(tmp_path, archive_name="错误姓名.zip")

    report = inspect_submission(
        archive,
        expected_name=EXPECTED_NAME,
        ffprobe_path=None,
    )

    assert any(f"{EXPECTED_NAME}.zip" in error for error in report.errors)


@pytest.mark.parametrize(
    ("readme", "video_name", "extras", "expected_fragment"),
    [
        (None, "demo.mp4", None, "恰好包含"),
        ("说明", None, None, "恰好包含"),
        ("说明", "demo.mp4", {"extra.txt": b"extra"}, "恰好包含"),
        ("说明", "demo.mov", None, "MP4"),
        ("说明", "nested/demo.mp4", None, "目录层级"),
        ("说明", ".hidden.mp4", None, "隐藏文件"),
    ],
)
def test_archive_rejects_wrong_count_type_nested_or_hidden_entries(
    tmp_path: Path,
    readme: str | None,
    video_name: str | None,
    extras: dict[str, bytes] | None,
    expected_fragment: str,
) -> None:
    archive = make_archive(
        tmp_path,
        readme=readme,
        video_name=video_name,
        extras=extras,
    )

    report = inspect_submission(
        archive,
        expected_name=EXPECTED_NAME,
        ffprobe_path=None,
    )

    assert any(expected_fragment in error for error in report.errors)


def test_internal_readme_filename_is_case_sensitive(tmp_path: Path) -> None:
    archive = tmp_path / f"{EXPECTED_NAME}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("readme.txt", "说明")
        bundle.writestr("demo.mp4", b"fake-mp4")

    report = inspect_submission(
        archive,
        expected_name=EXPECTED_NAME,
        ffprobe_path=None,
    )

    assert any("README.txt" in error for error in report.errors)


def test_video_at_200_mb_boundary_is_allowed_without_allocating_file() -> None:
    readme = zipfile.ZipInfo("README.txt")
    readme.file_size = 10
    video = zipfile.ZipInfo("demo.mp4")
    video.file_size = MAX_VIDEO_BYTES

    errors = validate_entry_metadata([readme, video])

    assert not any("200 MB" in error for error in errors)


def test_video_over_200_mb_is_rejected_without_allocating_file() -> None:
    readme = zipfile.ZipInfo("README.txt")
    readme.file_size = 10
    video = zipfile.ZipInfo("demo.mp4")
    video.file_size = MAX_VIDEO_BYTES + 1

    errors = validate_entry_metadata([readme, video])

    assert any("不超过 200 MB" in error for error in errors)


def test_ffprobe_duration_over_120_seconds_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = make_archive(tmp_path)

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout="120.01\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    report = inspect_submission(
        archive,
        expected_name=EXPECTED_NAME,
        ffprobe_path="ffprobe",
    )

    assert any("120 秒" in error for error in report.errors)


def test_ffprobe_nan_duration_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = make_archive(tmp_path)

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout="nan\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    report = inspect_submission(
        archive,
        expected_name=EXPECTED_NAME,
        ffprobe_path="ffprobe",
    )

    assert any("无效视频时长" in error for error in report.errors)


def test_main_returns_nonzero_and_json_for_invalid_archive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = make_archive(tmp_path, readme="字" * 1_001)

    exit_code = main(
        [
            "--expected-name",
            EXPECTED_NAME,
            str(archive),
        ],
        ffprobe_path=None,
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["errors"]


def test_corrupt_zip_returns_stable_error(tmp_path: Path) -> None:
    archive = tmp_path / f"{EXPECTED_NAME}.zip"
    archive.write_bytes(struct.pack("<I", 0xDEADBEEF))

    report = inspect_submission(
        archive,
        expected_name=EXPECTED_NAME,
        ffprobe_path=None,
    )

    assert report.ok is False
    assert report.errors == ("提交文件不是合法 ZIP",)


def test_unsupported_member_compression_returns_stable_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = make_archive(tmp_path)
    payload = bytearray(archive.read_bytes())
    local_header = payload.index(b"PK\x03\x04")
    central_header = payload.index(b"PK\x01\x02")
    payload[local_header + 8 : local_header + 10] = (99).to_bytes(2, "little")
    payload[central_header + 10 : central_header + 12] = (99).to_bytes(2, "little")
    archive.write_bytes(payload)

    exit_code = main(
        ["--expected-name", EXPECTED_NAME, str(archive)],
        ffprobe_path=None,
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert result["ok"] is False
    assert any("无法读取" in error for error in result["errors"])


def test_damaged_compressed_member_returns_stable_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = tmp_path / f"{EXPECTED_NAME}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("README.txt", "说明" * 500)
        bundle.writestr("demo.mp4", b"fake-mp4")
    payload = bytearray(archive.read_bytes())
    local_header = payload.index(b"PK\x03\x04")
    name_length = int.from_bytes(payload[local_header + 26 : local_header + 28], "little")
    extra_length = int.from_bytes(payload[local_header + 28 : local_header + 30], "little")
    data_start = local_header + 30 + name_length + extra_length
    payload[data_start] ^= 0xFF
    archive.write_bytes(payload)

    exit_code = main(
        ["--expected-name", EXPECTED_NAME, str(archive)],
        ffprobe_path=None,
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert result["ok"] is False
    assert any("无法读取" in error for error in result["errors"])


def test_damaged_lzma_member_returns_stable_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = tmp_path / f"{EXPECTED_NAME}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_LZMA) as bundle:
        bundle.writestr("README.txt", "说明" * 500)
        bundle.writestr("demo.mp4", b"fake-mp4")
    payload = bytearray(archive.read_bytes())
    local_header = payload.index(b"PK\x03\x04")
    name_length = int.from_bytes(payload[local_header + 26 : local_header + 28], "little")
    extra_length = int.from_bytes(payload[local_header + 28 : local_header + 30], "little")
    data_start = local_header + 30 + name_length + extra_length
    payload[data_start + 4] ^= 0xFF
    archive.write_bytes(payload)

    exit_code = main(
        ["--expected-name", EXPECTED_NAME, str(archive)],
        ffprobe_path=None,
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert result["ok"] is False
    assert any("无法读取" in error for error in result["errors"])


def test_truncated_member_eof_returns_stable_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = tmp_path / f"{EXPECTED_NAME}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("README.txt", "说明")
        bundle.writestr("demo.mp4", b"fake-mp4")
    payload = bytearray(archive.read_bytes())
    local_header = payload.index(b"PK\x03\x04")
    central_header = payload.index(b"PK\x01\x02")
    payload[local_header + 18 : local_header + 22] = (1_000).to_bytes(4, "little")
    payload[local_header + 22 : local_header + 26] = (1_000).to_bytes(4, "little")
    payload[central_header + 20 : central_header + 24] = (1_000).to_bytes(4, "little")
    payload[central_header + 24 : central_header + 28] = (1_000).to_bytes(4, "little")
    archive.write_bytes(payload)

    exit_code = main(
        ["--expected-name", EXPECTED_NAME, str(archive)],
        ffprobe_path=None,
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert result["ok"] is False
    assert any("无法读取" in error for error in result["errors"])
