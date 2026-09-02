# Reliability Research Execution Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 2026-09-01 18:00 前，以可复现证据完成 E4 真实会话探针、内部可靠性消融、code-operator/Claude Code/Kimi Code 三系统横评、失败驱动收敛及条件性 Ubuntu 验证。

**Architecture:** 本总控计划只协调四个互相独立、各自可测试和回退的子计划。真实数据阶段与离线基础设施分离；每个外部调用都由冻结 manifest、全新工作区、最小环境和排他脱敏报告驱动，任何生产缺陷另立 TDD 修复，不夹入研究基础设施提交。

**Tech Stack:** Python 3.11、pytest、标准库 `argparse/dataclasses/hashlib/json/pathlib/random/shutil/subprocess/tempfile/time`、现有 `httpx`/`AgentSession`/`ContextManager`/`AgentLoop`、Git、可选 WSL/Ubuntu 或 GitHub Actions。

---

## Approved baseline and authority boundaries

- 设计基线：`3e8542c docs(research): design reliability and agent comparison study`。
- 人工审核：`RESEARCH-DESIGN-002`，只授权设计提交和实施计划编写。
- 当前生产基线：`ca064ce feat(session): add bounded interactive session and file undo`。
- 本计划不授权安装 Kimi Code、登录外部产品、向 Moonshot/Anthropic/OpenAI/DeepSeek 发送任务数据、创建实现提交或远端 push。
- 真实 O1、Pilot、正式横评、可选系统和 push 分别使用新的明确授权；旧授权不得复用。
- API Key 只从环境变量或官方安全登录读取；不得进入命令行参数、manifest、JSON、stdout/stderr 持久化、Git 或视频。
- 已推送历史不得 amend、rebase、squash 或强推。

**2026-09-02 resumed execution:** 项目作者在 O1b 与 CI 修复完成后明确要求“完成科研横评”，因此重新开放内部确定性消融、三系统 Pilot、18 个正式 Track A 单元、结果综合和条件性 Ubuntu 决策。原 2026-09-01 18:00 冻结不再阻止这些已批准研究任务，但不改变三系统、三任务、两次重复、固定种子、失败保留、统一评分和完整平衡区组规则。该恢复指令不授权安装/登录外部工具、数据出境、可选 Codex/DeepSeek、真实 Track B、生产修复、提交或 push；这些边界继续分别取得明确授权。题目 PDF 的 2026-09-02 24:00 官方截止仍为绝对上限，必做 Track A 与可复现报告优先于 Ubuntu和所有可选系统。

## Plan decomposition and fixed order

1. [O1 E4 真实 Session 探针](2026-09-01-e4-session-probe-implementation.md)
2. [内部可靠性消融](2026-09-01-reliability-ablation-implementation.md)
3. [三系统 Agent 横评](2026-09-01-agent-comparison-implementation.md)
4. [研究综合、条件性 Ubuntu 与交付收敛](2026-09-01-research-synthesis-ubuntu-implementation.md)

执行顺序不可因中间结果改变：

```text
O1 real Session probe
-> deterministic reliability ablations
-> frozen three-system Pilot
-> 18 formal runs
-> failure-driven production fixes, only if reproducible
-> analysis/report
-> conditional Ubuntu verification
-> 18:00 freeze
```

## Global pre-registration gates

- [ ] **Gate 1: Confirm exact Git baseline**

Run:

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

Expected before implementation: only approved plan files and the repository-external v4.5 plan may differ; no unrelated user changes. Record the exact baseline in every evidence file.

- [ ] **Gate 2: Freeze study IDs and randomization seed before results**

Use these exact identifiers:

```text
study_id = reliability-2026-09-01-preregistered
track_a_seed = 20260901
required_systems = code-operator, claude-code, kimi-code
tasks = T1, T2, T3
replicates = 1, 2
timeout_seconds = 900
```

No later task may change them after seeing Pilot or formal outcomes. Pilot records use `phase=pilot` and never enter Track A totals.

- [ ] **Gate 3: Preserve the three levels of evidence**

Every report must keep these namespaces separate:

```text
e4_session_probe     # one real integration probe, not a benchmark
internal_ablation    # deterministic mechanism evidence
external_track_a     # end-to-end product comparison
external_track_b     # conditional same-model pairing
ubuntu_offline       # conditional platform evidence
```

- [ ] **Gate 4: Apply the fixed degradation order**

At any schedule slip, remove work only in this order:

```text
Deep Code/DeepSeek
Codex
real-model error-feedback pairing
Ubuntu verification
all new experience features (already excluded)
```

Never reduce the required three systems, three tasks, two formal replicates, uniform grading, or honest failure retention merely to improve the table.

## Milestone checkpoints

### M-R0: Design and plan freeze

- [ ] All five plan documents have no `TBD`, `TODO`, undefined signature, or conflicting cutoff.
- [ ] The root v4.5 plan contains the research milestone without marking O1, experiments, Ubuntu, video, ZIP, or upload complete.
- [ ] Project author reviews the complete written plan as `RESEARCH-PLAN-001` before any implementation.
- [ ] Create one local plan commit only after that review; do not push without section 9.5 authorization.

### M-R1: O1 real Session probe

- [ ] Finish all offline fixture/schema/runner tests first.
- [ ] Display the exact two prompts and fixture file list.
- [ ] Obtain explicit Moonshot data-transfer authorization for this probe.
- [ ] Run exactly one new probe and record either a valid result or `INVALID_INFRA`.
- [ ] If a production defect is reproducible, stop and create a separate red-green fix candidate.

### M-R2: Deterministic mechanism study

- [ ] Freeze scenario matrix before running the study.
- [ ] Compare experimental weak baselines against current production behavior.
- [ ] Generate `docs/evidence/reliability-study.json` from deterministic inputs.
- [ ] Do not call any provider in the mandatory portion.

### M-R3: External three-system study

- [ ] Install/configure Kimi Code only after explicit approval and only from Moonshot AI's official channel.
- [ ] Capture exact `--version`/`--help` evidence without tokens or account identifiers.
- [ ] Complete three excluded Pilot runs and review infrastructure outcomes.
- [ ] Display exact task files/prompts/providers and obtain fresh Claude+Kimi data authorization.
- [ ] Freeze manifest hashes and run all 18 formal cells once, retaining failures.
- [ ] Do not rerun an individual poor result; rerun only an entire invalid balanced block after a documented harness defect.

### M-R4: Synthesis and conditional platform evidence

- [ ] Generate tables from JSON rather than hand-copying results.
- [ ] Separate automated outcomes, qualitative observations, failures, and validity threats.
- [ ] Start Ubuntu only if all mandatory research/report work is complete by 16:00 and Windows full regression passes.
- [ ] Stop Ubuntu after 60 minutes and record pass, fail, or not-run honestly.
- [ ] Freeze all feature/research implementation at 18:00.

## Global verification gate

Run after every implementation subproject and again before the final research commit:

```powershell
python -m pytest -q
python -m compileall -q code_operator evals scripts tests
python -m pytest tests/test_submission_preflight.py -q
git diff --check
git status --short --branch
```

Expected: all tests pass, compile/check exits 0, and status contains only the reviewed milestone scope.

Scan the candidate and full history without printing secrets:

```text
current API Key exact matches = 0
private-key headers = 0
sk-like credentials = 0
local absolute user paths = 0
tracked .env/log/cache/PID/tmp/session/transcript/checkpoint = 0
```

Synthetic credential fixtures must be identified by file and purpose; a pattern count alone is not a leak conclusion.

## Git and human-review checkpoints

Use semantic commits only at independently reviewable boundaries:

```text
docs(research): plan reliability and agent comparison study
test(research): add real session probe evidence
test(research): add deterministic reliability ablations
test(research): add reproducible agent comparison harness
docs(research): report reliability and agent comparison results
ci: verify offline tests on ubuntu          # conditional only
fix(runtime): preserve abort result pairing # example only; use the verified root cause
```

Before each local commit, add a matching `REVIEW_LOG.md` entry with exact scope, TDD red/green evidence, tests, scans, limits, and the user's explicit approval. Before each push, show the full v4.5 section 9.5 package and wait for a new `允许推送` or `可以 push`.

## Stop conditions

- Stop O1 after 60 minutes of provider/auth/terminal infrastructure investigation.
- Stop Kimi installation/auth investigation after 60 minutes.
- Stop and invalidate the whole balanced block if the grader, fixture hash, timeout enforcement, or environment isolation is wrong.
- Stop production work after 14:00 except submission-blocking security/protocol/core-execution fixes.
- Stop Ubuntu at 17:00 even if incomplete.
- Stop all feature and research changes at 18:00; continue only video, ZIP, scans, submission verification, and blocking fixes.
