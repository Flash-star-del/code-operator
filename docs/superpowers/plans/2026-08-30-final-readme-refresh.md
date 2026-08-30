# Final Submission README Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the final `README.txt` accurately describe the already verified E2/E3 state while remaining within the official 1000-character limit, and correct the workspace-root preflight invocation to the script's actual ZIP-inspection interface.

**Architecture:** This is a documentation-only closeout. `README.txt` remains the sole file placed beside the final video; the existing preflight implementation and production code are unchanged. The root v4.5 plan records the exact UTF-8-safe command used to inspect an already-created ZIP.

**Tech Stack:** UTF-8 plain text, Python 3.11, existing pytest submission-preflight tests, Git staged review.

---

### Task 1: Refresh only verified README.txt facts

**Files:**
- Modify: `README.txt`
- Test: inline read-only README contract probe

- [x] **Step 1: Run the factual contract probe and confirm the expected red state**

Run:

```powershell
python -c "from pathlib import Path; text=Path('README.txt').read_text(encoding='utf-8'); assert '348 项离线测试通过' in text; assert '三个全新工作区 3/3 完成' in text; assert '普通终端轨迹' in text; assert '240 项离线测试通过' not in text"
```

Expected: non-zero exit because the current file still contains `240 项离线测试通过` and does not yet contain the three new verified statements.

- [x] **Step 2: Replace only the final status paragraph**

Keep sections one through three unchanged. Replace section four with this exact text:

```text
四、当前状态
基础版 v0.1.0 已发布；当前 main 在 Windows/Python 3.11 有 348 项离线测试通过，GitHub Actions offline-tests 通过。固定订单价格流水线在 kimi-k3 的三个全新工作区 3/3 完成，测试哈希不变且只修改 pricing.py 和 invoice.py；这只是一个固定任务的三次样本，不代表整体成功率。CLI 默认显示脱敏、有界并转义终端控制字符的普通终端轨迹。Ubuntu 未验证。详细证据见 DESIGN.md、DEFENSE.md 和 REVIEW_LOG.md。
```

Do not add the applicant's name, API Key, request headers, local absolute paths, video claims, ZIP claims, or optional E4/E5 features.

- [x] **Step 3: Run the probe again and enforce the official size boundary**

Run:

```powershell
python -c "from pathlib import Path; text=Path('README.txt').read_text(encoding='utf-8'); assert '348 项离线测试通过' in text; assert '三个全新工作区 3/3 完成' in text; assert '普通终端轨迹' in text; assert '240 项离线测试通过' not in text; assert len(text) <= 1000; print(len(text))"
```

Expected: exit 0 and a printed count no greater than 1000.

**执行证据：** 初始事实探针以退出码 1（`AssertionError`）结束；修改后探针通过，Python 字符数为 933，README 前三节与 HEAD 一致，`git diff --check` 通过。

### Task 2: Correct the final preflight command in the workspace plan

**Files:**
- Modify outside repository: `../code-operator 执行计划 v4.5（开源经验校准、核心闭环与交付收敛版）.md`
- Verify: `scripts/preflight_submission.py`

- [x] **Step 1: Confirm the existing command and actual parser disagree**

Run:

```powershell
python -X utf8 scripts/preflight_submission.py --help
```

Expected: the parser requires `--expected-name EXPECTED_NAME archive`; it does not accept `--readme`, `--video`, or `--zip`.

- [x] **Step 2: Replace the obsolete invocation without changing the script**

Change the root-plan command to:

```powershell
python -X utf8 scripts/preflight_submission.py --expected-name "<本人中文姓名>" "<本人中文姓名>.zip"
```

State that the ZIP must already exist and contain only top-level `README.txt` plus one lowercase `.mp4`. Keep the applicant name as a placeholder until final packaging.

- [x] **Step 3: Verify the documented interface**

Run:

```powershell
rg -n -- "--readme|--video|--zip" "../code-operator 执行计划 v4.5（开源经验校准、核心闭环与交付收敛版）.md"
python -X utf8 scripts/preflight_submission.py --help
```

Expected: the first command has no obsolete final-preflight invocation; the second shows readable Chinese and the actual positional `archive` interface.

**执行证据：** 旧参数命中检查作为红灯；真实帮助文本确认接口为 `--expected-name EXPECTED_NAME archive`，修改后旧参数 `rg` 无命中，UTF-8 帮助检查通过。

### Task 3: Verify, review, and create one documentation commit

**Files:**
- Modify after approval: `REVIEW_LOG.md`
- Review: `README.txt`, this plan, and the root v4.5 plan change

- [x] **Step 1: Run focused and full verification**

Run:

```powershell
python -m pytest -q tests/test_submission_preflight.py
python -m pytest -q
python -m compileall -q code_operator evals scripts tests
git diff --check
git status --short
```

Expected: 22 submission-preflight tests pass, the current full suite passes, compileall and diff check exit 0, and the repository contains only the intended README/plan changes.

- [x] **Step 2: Verify privacy and public-claim boundaries**

Check the complete pending diff for the current API Key by exact value without printing it. Scan for private-key headers, Authorization Bearer values, applicant identity, local absolute paths, video/ZIP completion claims, optional E4/E5 claims, and dependency changes. Expected: zero sensitive-value and dependency hits; only verified E2/E3 statements appear.

**执行证据：** 专项测试 `22 passed`；`collect-only` 共 348 tests；子 Agent 全量测试 `348 passed in 48.60s`，主模型全量测试 `348 passed in 45.37s`；`compileall` 与 `git diff --check` 通过。API Key 精确值、私钥头、Bearer 值、本地绝对路径和依赖差异均为 0；暂存仅 README 与本计划。项目作者已明确回复 `FINAL-DOC-001 人工审核通过`；尚未推送。

- [x] **Step 3: Prepare the human review packet**

Create a complete staged patch outside the repository, hash it with SHA-256, report the exact files, factual red/green probe, character count, focused/full tests, scans, remaining video/ZIP limitations, and request `FINAL-DOC-001 人工审核通过`. Do not commit or push before approval.

- [x] **Step 4: Record approval and create the local commit**

After exact approval, append `FINAL-DOC-001` to `REVIEW_LOG.md`, stage it, rerun `git diff --cached --check`, and commit:

```powershell
git commit -m "docs: refresh final submission readme" -m "Human-Review: approved" -m "Review-Scope: FINAL-DOC-001; full-staged-diff" -m "Review-Record: REVIEW_LOG.md#final-doc-001"
```

- [ ] **Step 5: Stop at the separate push gate**

Show a fresh v4.5 §9.5 packet and wait for a new exact `允许推送` or `可以 push`. Use only an ordinary non-force push after authorization, then verify remote hashes and the blocking `offline-tests` CI.
