from __future__ import annotations

import argparse
import json
import lzma
import math
import shutil
import subprocess
import sys
import tempfile
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


MAX_README_CHARACTERS = 1_000
MAX_README_UTF8_BYTES = MAX_README_CHARACTERS * 4 + 3
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_VIDEO_SECONDS = 120.0
_AUTO_FFPROBE = object()


@dataclass(frozen=True)
class PreflightReport:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_top_level(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name


def _video_size_allowed(size: int) -> bool:
    return size <= MAX_VIDEO_BYTES


def validate_entry_metadata(infos: Sequence[zipfile.ZipInfo]) -> list[str]:
    errors: list[str] = []
    names = [info.filename for info in infos]
    if len(infos) != 2:
        errors.append("ZIP 必须恰好包含 README.txt 和一个 MP4 文件")
    if len(names) != len(set(names)):
        errors.append("ZIP 不得包含重复文件名")
    if any(info.is_dir() or not _is_top_level(info.filename) for info in infos):
        errors.append("ZIP 内不得包含目录层级或目录项")
    if any(Path(info.filename).name.startswith(".") for info in infos):
        errors.append("ZIP 内不得包含隐藏文件")
    if any(info.flag_bits & 0x1 for info in infos):
        errors.append("ZIP 内文件不得加密")

    if names.count("README.txt") != 1:
        errors.append("ZIP 内必须恰好包含一个区分大小写的 README.txt")
    videos = [info for info in infos if info.filename.endswith(".mp4")]
    if len(videos) != 1:
        errors.append("ZIP 内必须恰好包含一个 MP4 文件（.mp4）")
    elif not _video_size_allowed(videos[0].file_size):
        errors.append("视频文件必须不超过 200 MB")
    return errors


def _probe_duration_seconds(
    bundle: zipfile.ZipFile,
    video: zipfile.ZipInfo,
    ffprobe_path: str,
) -> float:
    with tempfile.TemporaryDirectory(prefix="code-operator-preflight-") as directory:
        video_path = Path(directory) / "submission.mp4"
        with bundle.open(video, "r") as source, video_path.open("xb") as target:
            shutil.copyfileobj(source, target)
        completed = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError("ffprobe 无法读取视频时长")
        try:
            duration = float(completed.stdout.strip())
        except ValueError as error:
            raise ValueError("ffprobe 返回了无效视频时长") from error
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("ffprobe 返回了无效视频时长")
        return duration


def inspect_submission(
    archive_path: str | Path,
    *,
    expected_name: str,
    ffprobe_path: str | None | object = _AUTO_FFPROBE,
) -> PreflightReport:
    archive = Path(archive_path)
    errors: list[str] = []
    warnings: list[str] = []
    normalized_name = expected_name.strip()
    if (
        not normalized_name
        or normalized_name != expected_name
        or Path(normalized_name).name != normalized_name
        or "/" in normalized_name
        or "\\" in normalized_name
    ):
        errors.append("--expected-name 必须是非空且不含路径的本人姓名")
    elif archive.name != f"{normalized_name}.zip":
        errors.append(f"ZIP 文件名必须严格为 {normalized_name}.zip")

    if not archive.is_file():
        errors.append("提交 ZIP 不存在或不是文件")
        return PreflightReport(tuple(errors), tuple(warnings))

    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = bundle.infolist()
            errors.extend(validate_entry_metadata(infos))
            readmes = [info for info in infos if info.filename == "README.txt"]
            if len(readmes) == 1:
                if readmes[0].file_size > MAX_README_UTF8_BYTES:
                    errors.append("README.txt 必须不超过 1000 个 Unicode 字符")
                else:
                    try:
                        readme = bundle.read(readmes[0]).decode("utf-8-sig")
                    except UnicodeDecodeError:
                        errors.append("README.txt 必须使用 UTF-8 编码")
                    except (
                        EOFError,
                        OSError,
                        RuntimeError,
                        lzma.LZMAError,
                        zipfile.BadZipFile,
                        zlib.error,
                    ):
                        errors.append("README.txt 无法读取或校验失败")
                    else:
                        characters = len(readme)
                        if characters > MAX_README_CHARACTERS:
                            errors.append(
                                "README.txt 必须不超过 1000 个 Unicode 字符"
                                f"（当前 {characters}）"
                            )

            videos = [info for info in infos if info.filename.endswith(".mp4")]
            metadata_allows_probe = (
                len(videos) == 1
                and _is_top_level(videos[0].filename)
                and not Path(videos[0].filename).name.startswith(".")
                and _video_size_allowed(videos[0].file_size)
                and not (videos[0].flag_bits & 0x1)
            )
            selected_ffprobe = (
                shutil.which("ffprobe")
                if ffprobe_path is _AUTO_FFPROBE
                else ffprobe_path
            )
            if metadata_allows_probe:
                if selected_ffprobe is None:
                    warnings.append(
                        "未找到 ffprobe，请人工确认视频时长不超过 120 秒"
                    )
                elif isinstance(selected_ffprobe, str):
                    try:
                        duration = _probe_duration_seconds(
                            bundle,
                            videos[0],
                            selected_ffprobe,
                        )
                    except (
                        EOFError,
                        OSError,
                        RuntimeError,
                        lzma.LZMAError,
                        subprocess.SubprocessError,
                        ValueError,
                        zipfile.BadZipFile,
                        zlib.error,
                    ) as error:
                        errors.append(str(error))
                    else:
                        if duration > MAX_VIDEO_SECONDS:
                            errors.append(
                                "视频时长必须不超过 120 秒"
                                f"（当前 {duration:.2f} 秒）"
                            )
                else:
                    errors.append("ffprobe 路径必须是字符串")
    except zipfile.BadZipFile:
        return PreflightReport(("提交文件不是合法 ZIP",), tuple(warnings))
    except OSError:
        errors.append("提交 ZIP 无法读取")
    return PreflightReport(tuple(dict.fromkeys(errors)), tuple(warnings))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查南京大学软件学院推免考核 ZIP 提交物。",
    )
    parser.add_argument(
        "--expected-name",
        required=True,
        help="本人姓名；ZIP 必须严格命名为 <姓名>.zip",
    )
    parser.add_argument("archive", help="待检查的 ZIP 文件")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    ffprobe_path: str | None | object = _AUTO_FFPROBE,
) -> int:
    args = build_parser().parse_args(argv)
    report = inspect_submission(
        args.archive,
        expected_name=args.expected_name,
        ffprobe_path=ffprobe_path,
    )
    print(
        json.dumps(
            {
                "ok": report.ok,
                "errors": list(report.errors),
                "warnings": list(report.warnings),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
