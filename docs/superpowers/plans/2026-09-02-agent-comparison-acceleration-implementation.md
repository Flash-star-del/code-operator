# Nine-Cell Agent Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Pilot 暴露的缓存误判，将正式 Track A 收敛为三系统、三任务、单次重复的九单元串行微基准，并补齐正式运行入口。

**Architecture:** 保留现有 manifest、workspace、adapter、grader 和 coordinator 边界。只在 grader 的统一 Git 状态入口过滤运行时缓存，将 manifest 的重复集合改为 `(1,)`，并让 `run_phase()` 根据 `phase` 选择已冻结的 `pilot` 或 `formal` cells；独立 grader 始终是能力主判据，adapter 事件状态只作为报告遥测。

**Tech Stack:** Python 3.11、pytest、Git porcelain v1、规范 JSON。

---

### Task 1: 修复 Git 状态缓存误判

**Files:**
- Modify: `evals/agent_comparison/grader.py`
- Test: `tests/test_agent_comparison.py`

- [x] 写一个真实 T1 Git 工作区测试：运行会生成 `__pycache__/*.pyc` 的 Python/pytest 后只修改 `ranges.py`，断言 `grade_workspace()` 的 `changed_files` 不含缓存且不产生 `SCOPE_VIOLATION`。
- [x] 运行该单测并确认它因缓存出现在 `changed_files` 中而失败。
- [x] 新增一个仅识别 `__pycache__`、`.pytest_cache` 和 `*.pyc` 的私有路径谓词，在 `_status_paths()` 向 `changed`/`statuses`/`binary_paths` 加入路径前统一跳过；不改变其他路径的范围判断。
- [x] 重跑单测和 grader 专项测试，确认正确补丁仍由隐藏测试评分，真正的额外文件仍被拒绝。

### Task 2: 将正式清单收敛为九单元

**Files:**
- Modify: `evals/agent_comparison/manifest.py`
- Modify: `tests/test_agent_comparison.py`

- [x] 先把 manifest 断言改为 `REPLICATES == (1,)`、`FORMAL_RUN_COUNT == 9`、每个 T1/T2/T3 块恰好包含三个系统，并确认现实现产生 18 单元而失败。
- [x] 最小修改常量为 `REPLICATES = (1,)`、`FORMAL_RUN_COUNT = 9`；保留相同固定种子和逐任务系统 shuffle，不手写结果顺序。
- [x] 重跑全部 manifest 测试，确认未知 replicate、缺失、重复、顺序篡改、非规范类型和 Track B 门禁仍被拒绝。

### Task 3: 复用串行协调器运行正式阶段

**Files:**
- Modify: `evals/agent_comparison/run_study.py`
- Test: `tests/test_agent_comparison.py`

- [x] 写 formal RED 测试：`run_phase(..., phase="formal")` 必须按 manifest 九个 cells 严格串行，对每个 T1/T2/T3 创建全新工作区、运行 adapter、独立评分、清理，并排他写入九行规范 JSON；adapter 的 `INVALID_OUTPUT` 不得覆盖 grader 结果。
- [x] 写 CLI RED 测试，要求 `--phase formal` 被接受；其他 phase 仍拒绝。
- [x] 最小修改 `run_phase()`：只允许 `pilot/formal`，分别选择 `manifest.pilot/manifest.formal`；Pilot 保持“三个 T1”门禁，formal 要求恰好九个唯一 `system × task × replicate=1` 单元。CLI choices 同步为两种 phase。
- [x] 运行协调器专项和完整 `tests/test_agent_comparison.py`，随后运行完整离线测试、`compileall`、`git diff --check`、凭据/私钥/临时文件扫描。
- [x] 更新既有横评计划记录设计修订、RED/GREEN 证据和新测试计数；生成新 manifest 之前先完成人工审核，任何外部 Pilot 另行展示范围并授权。

完成证据（2026-09-02）：三项任务均按 RED→GREEN 完成——缓存过滤 RED 为 `primary_failure == SCOPE_VIOLATION`，GREEN 后真实缓存测试通过且额外文件仍被拒绝；manifest 缩减 RED 为两个测试对旧 `(1, 2)`/18 单元失败，GREEN 后 manifest 专项 7 passed；formal 入口 RED 为 2 failed（`run_phase` ValueError 与 CLI 拒绝），GREEN 后合并定向 10 passed。横评专项三文件共 149 passed；完整离线套件在全新 `--basetemp` 下 854 passed, 7 skipped（本机 `Temp\pytest-of-86138` ACL 损坏为环境问题，与代码无关）；`compileall`、`git diff --check`、凭据/私钥扫描通过；新九单元 manifest（SHA-256 `bf29d3eb…364e890`）经严格 loader 复核为 3 Pilot、9 formal、360 秒，旧 18 单元 manifest 保留为 `-18-invalid.json`。2026-09-01 既有计划已同步记录本修订；任何外部 Pilot/正式运行仍需重新授权并排他写入新报告文件。
