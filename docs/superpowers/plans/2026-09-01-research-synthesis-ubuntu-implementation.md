# Research Synthesis and Conditional Ubuntu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从已验证 JSON 自动生成研究表格与结论边界，处理实验暴露的可复现根因，并仅在 16:00 门槛满足时补充最多 60 分钟的 Ubuntu 离线证据，最终于 18:00 冻结研究与功能变更。

**Architecture:** 分析器把 O1、内部消融、Track A/B 和 Ubuntu 作为独立命名空间读取，先验证 schema/哈希/完整性，再输出确定性 Markdown 片段。生产缺陷修复不进入分析提交；Ubuntu 只运行离线套件且不接收凭据。README/DESIGN/REFERENCES/根计划只引用已经存在的证据，不补写推测结果。

**Tech Stack:** Python 3.11、pytest、标准库 `argparse/dataclasses/json/pathlib/statistics`（仅描述性中位数）、现有提交预检、Git、可选 WSL/Ubuntu 或 GitHub Actions。

---

## Files

**Create:**

- `evals/analyze_research.py`
- `tests/test_research_analysis.py`
- `docs/research/reliability-study.md`
- `docs/evidence/platform-decision.json`

**Modify only after verified evidence exists:**

- `README.md`
- `README.txt`
- `DESIGN.md`
- `REFERENCES.md`
- `REVIEW_LOG.md`
- repository-external `../code-operator 执行计划 v4.5（开源经验校准、核心闭环与交付收敛版）.md`
- `.github/workflows/test.yml` only if the conditional Ubuntu CI route is separately approved

## Task 1: Schema validation before analysis

**Files:** Create `evals/analyze_research.py`; create `tests/test_research_analysis.py`.

- [ ] **Step 1: Write RED tests for missing, duplicate and mixed evidence**

Public API:

```python
@dataclass(frozen=True)
class EvidenceBundle:
    session_probe: dict[str, object]
    internal_ablation: dict[str, object]
    external_track_a: dict[str, object]
    external_track_b: dict[str, object] | None
    platform_decision: dict[str, object] | None
    ubuntu_offline: dict[str, object] | None


```

The exact interfaces are `load_evidence(evidence_dir: Path, *, phase: Literal["preview", "final"]) -> EvidenceBundle` and `validate_evidence(bundle: EvidenceBundle, *, phase: Literal["preview", "final"]) -> tuple[str, ...]`. Preview permits a missing platform decision and renders `PENDING_TIME_GATE`; final validation forbids it.

Tests must reject wrong `study_id`, unknown schema versions, duplicate formal cells, fewer/more than 18 Track A cells, manifest/task hash mismatch, impossible pass counts, Pilot rows mixed into formal, O1 rows mixed into Track A, Track B marked ready without evidence, and reports containing absolute temporary paths or credential-like fields. In final phase, `ubuntu_offline` may be absent only when `platform_decision.status` is `NOT_RUN_TIME_GATE` or `INVALID_INFRA`; it is required when that status is `RUN_REQUIRED`.

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_research_analysis.py -q
```

Expected: analyzer module is missing.

- [ ] **Step 3: Implement strict validation**

Do not coerce malformed values or silently drop rows. Return a stable tuple of validation errors for library use; the CLI prints error codes and exits nonzero without dumping evidence contents.

- [ ] **Step 4: Confirm GREEN**

```powershell
python -m pytest tests/test_research_analysis.py -q
```

## Task 2: Deterministic tables and claim-safe wording

**Files:** Modify analyzer and tests.

- [ ] **Step 1: Write RED rendering tests**

Expose the exact interface `render_markdown(bundle: EvidenceBundle, *, phase: Literal["preview", "final"]) -> str`.

Golden assertions require these sections in order:

```text
研究问题与预注册
O1 E4 真实 Session 探针
内部确定性不变量
Track A 逐运行原始结果
Track A 系统汇总
Track B 同模型配对或未运行原因
条件性 Ubuntu 验证或未运行原因
设计决策证据与反例
有效性威胁
未实现能力与后续工作
```

Assert each task-system cell is rendered as `0/2`, `1/2`, or `2/2`; each required system is `x/6`; no percentage, p-value, confidence interval, “总体成功率”, “显著”, “领先行业”, “优于 Claude/Kimi”, or “SWE-bench 成绩” appears. Elapsed time may use median and range only when at least two valid values exist; missing usage remains `unavailable`.

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/test_research_analysis.py -k render -q
```

- [ ] **Step 3: Implement deterministic rendering**

Sort raw rows by task, replicate, order index, then system. Keep automated grade, failure category, and manual qualitative observations in separate columns/sections. Qualitative notes may reference only reviewed evidence tags or minimal patch metadata; never insert model reasoning or full product responses.

- [ ] **Step 4: Confirm GREEN and byte stability**

```powershell
python -m pytest tests/test_research_analysis.py -k render -q
```

Two renders of the same bundle must be byte-identical.

## Task 3: Failure-driven production repair gate

This is a decision gate, not automatic authorization to edit production.

- [ ] Build a finding table from O1/internal/Pilot/formal evidence with fields `finding_id`, `source_run`, `reproducible_offline`, `severity`, `production_component`, `balanced_block_impact`, `decision`.
- [ ] Before 14:00, only a reproducible production root cause may enter a separate fix cycle. First add a minimal failing production test and confirm RED; write a short fix design; obtain scope approval; implement the smallest fix; rerun focused/full tests; invalidate every affected formal balanced block.
- [ ] At or after 14:00, fix only submission-blocking security, credential, protocol or core-execution defects. Record other findings as limitations/future work.
- [ ] Maximum two fix cycles. Do not perform unrelated refactors, benchmark-specific prompt tuning, task exceptions, grader-aware logic or product-specific handicaps.
- [ ] Each fix needs its own `REVIEW_LOG.md` entry, human approval and semantic `fix` commit named after the verified root cause. It must never be hidden inside the harness/report commit.

## Task 4: Implement and preview the report from evidence

**Files:** Modify analyzer and tests. Do not create the final Markdown until the Ubuntu decision has been frozen.

- [ ] **Step 1: Write RED CLI tests**

CLI contract:

```text
python -m evals.analyze_research --evidence-dir docs/evidence --preview
python -m evals.analyze_research --evidence-dir docs/evidence --output docs/research/reliability-study.md
```

`--preview` validates all currently mandatory evidence and writes the deterministic Markdown only to stdout; it never creates a file. The output mode refuses overwrite unless `--check` is used to compare existing bytes. `--check` exits zero only when the file exactly equals current deterministic rendering.

- [ ] **Step 2: Confirm RED, implement, confirm GREEN**

```powershell
python -m pytest tests/test_research_analysis.py -q
```

- [ ] **Step 3: Preview and inspect every claim before the platform decision**

```powershell
python -m evals.analyze_research --evidence-dir docs/evidence --preview
```

Review all `x/2`, `x/6`, failure classes, unavailable fields, invalid-infra/not-run reasons and validity threats against source JSON.

- [ ] **Step 4: Confirm the preview is sufficient for the 16:00 decision**

The preview must already contain the complete mandatory O1, deterministic and Track A sections. It may show Ubuntu as pending only before `platform-decision.json` exists; final file generation rejects pending status.

## Task 5: Conditional Ubuntu start gate at 16:00

- [ ] Record the decision timestamp and evaluate both prerequisites:

```text
mandatory O1 disposition exists
mandatory deterministic study complete
valid 18-cell Track A report complete
research Markdown preview generated and reviewed
Windows full suite green
current time is before 16:00 Asia/Shanghai
at least one hour plus final-review buffer remains
```

- [ ] If any prerequisite fails, exclusively create `docs/evidence/platform-decision.json` with `status=NOT_RUN_TIME_GATE` and one fixed reason code; skip all Ubuntu work. This does not invalidate mandatory research.
- [ ] If all pass, first perform a read-only discovery of WSL/container availability. Prefer an existing local ephemeral Ubuntu environment; do not install a distribution, enable Windows features, change virtualization, create a remote runner, or send data without separate approval.
- [ ] After discovery, exclusively create `platform-decision.json` with `status=RUN_REQUIRED`, or `INVALID_INFRA` plus a fixed reason code. Store the decision time in ISO 8601 with `+08:00`; store no usernames, host paths or environment values. Never overwrite the decision file.

## Task 6: Run at most 60 minutes of Ubuntu offline validation

**Files:** Generate `docs/evidence/ubuntu-offline.json`; modify CI only under the conditional branch below.

- [ ] Use no API Key and remove provider/auth variables from the child environment. Copy the reviewed Git tree to an ephemeral Linux path; do not expose unrelated Windows directories.
- [ ] Record Python/pytest/Git versions and run:

```bash
python -m pytest -q
python -m compileall -q code_operator evals tests
python -m pytest tests/test_submission_preflight.py -q
git diff --check
```

- [ ] Specifically retain focused results for path guardrails, UTF-8, command policy and Unix process-group termination. Store counts, exit codes, durations and failing test node IDs only—not full environment, usernames, home paths or output payloads.
- [ ] Stop at 17:00 or 60 elapsed minutes, whichever comes first. Record `PASS`, `FAIL`, or `INVALID_INFRA`; do not start a broad portability rewrite.
- [ ] Only if local Ubuntu is unavailable before the start gate and enough review time remains, propose a separate `.github/workflows/test.yml` Ubuntu job. Do not edit/push CI merely to bypass the local limit; it requires its own design, human review and section 9.5 authorization.

## Task 7: Materialize the final report and update public docs

- [ ] Once `platform-decision.json` and any required `ubuntu-offline.json` validate, create the final report exactly once:

```powershell
python -m evals.analyze_research --evidence-dir docs/evidence --output docs/research/reliability-study.md
python -m evals.analyze_research --evidence-dir docs/evidence --output docs/research/reliability-study.md --check
```

- [ ] Update public docs without overstating results. `README.md` gets a short “research evidence” link and factual one-paragraph result boundary. `README.txt` changes only if the brief submission summary still fits its declared character constraints. `DESIGN.md` maps each mechanism to supporting/contrary evidence. `REFERENCES.md` retains public method references and explicitly distinguishes external methodology from independently implemented production code. The root v4.5 plan marks only actually completed checkboxes.
- [ ] Review every public count, model/provider/platform statement and limitation against the generated Markdown and source JSON. Do not hand-edit generated tables; change the analyzer/test or evidence only through its own approved correction path.

## Task 8: Final verification and research implementation review

Run on Windows after every conditional branch:

```powershell
python -m pytest tests/test_research_analysis.py tests/test_session_probe.py tests/test_reliability_study.py tests/test_agent_comparison.py -q
python -m pytest -q
python -m compileall -q code_operator evals scripts tests
python -m pytest tests/test_submission_preflight.py -q
python -m evals.analyze_research --evidence-dir docs/evidence --output docs/research/reliability-study.md --check
git diff --check
git status --short --branch
```

- [ ] Run candidate and full-history scans: exact current API Key 0, Bearer credential 0, private-key header 0, sk-like secret 0, local absolute user path 0, tracked `.env`/auth/cache/log/PID/tmp/session/transcript/checkpoint 0. Identify synthetic fixtures separately.
- [ ] Confirm every evidence JSON parses, all generated Markdown matches it, public docs share the same model/provider/platform/limitations, and no third-party Agent source/SDK entered production.
- [ ] At 18:00 freeze all feature and research changes. After the cutoff, only submission-blocking fixes, video, ZIP, scans, upload rehearsal and final submission work remain.
- [ ] Present complete candidate as `RESEARCH-RESULTS-001` for human review. Only after approval add `REVIEW_LOG.md` and create local commit `docs(research): report reliability and agent comparison results` (plus a conditional independent `ci: verify offline tests on ubuntu` commit if separately approved).
- [ ] Do not push either commit until the complete v4.5 section 9.5 gate is displayed and the project author gives a new exact push authorization.

## Completion boundary

Completion means the report can be regenerated from validated evidence and every non-run/failed branch is explicit. It does not mean the project beats a mature product, proves population-level reliability, completes the delayed recording/ZIP/upload, or authorizes additional feature work.
