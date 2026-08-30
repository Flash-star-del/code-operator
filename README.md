# code-operator

code-operator 是一个正在从零实现的命令行编程智能体（coding agent）。目标是通过模型原生 tool calling，在受约束的本地工作区内读取和修改文件、执行命令，并根据真实执行结果继续完成编程任务。

项目不使用 agent 框架或 Agent SDK。AgentLoop、协议处理、工具执行和安全策略均独立实现。M0 至 M4 已完成验收，Windows CI 已通过，基础版 annotated tag `v0.1.0` 已发布。未完成或未验证的能力不视为已经实现。

## 配置约定

运行时只从环境变量读取以下三个必要配置：

```bash
CODE_OPERATOR_API_KEY=<YOUR_API_KEY>
CODE_OPERATOR_BASE_URL=https://api.moonshot.cn/v1
CODE_OPERATOR_MODEL=kimi-k3
```

上述 API 根地址、模型 ID、`max_tokens` 输出限制和非思考模式原生工具调用已于 2026-08-27 通过 P0 真实探针验证。API Key 不得写入仓库、README.txt、日志或演示视频。

## 安装与运行

项目要求 Python 3.11。可先安装运行依赖：

```bash
python -m pip install -r requirements.txt
```

在一个单独的工作区执行任务：

```bash
python -m code_operator --workspace <WORKSPACE> "<TASK>"
```

当前六个工具 `list_dir`、`read_file`、`grep`、`write_file`、`edit_file` 和 `run_command` 均已接入最小安全闭环。文件修改返回受限长度的 unified diff；命令默认显示参数数组和固定工作目录并要求人工批准。只有显式启用 `--auto-approve-tests` 时，未被工作区同名文件遮蔽的裸命令 `pytest` 或 `python -m pytest` 才会自动放行。

### 普通终端执行轨迹

CLI 默认以普通纯文本输出模型轮次、工具名、脱敏后的参数摘要、工具结果、进入人工 ASK 的命令及其 ALLOW/DENY 决策、文件 diff、命令退出码，以及受限长度的 stdout/stderr；自动放行或策略直接拒绝的命令不经过此决策标记；结束时显示供应商 usage 是否可用和停止原因。参数正文只显示有限长度的结构摘要：`read_file`、`grep`、`list_dir` 仍显示工具名、脱敏有界参数摘要与 `ok/error_code`，但不显示读取/搜索/目录结果 payload（正文、匹配项、条目）；文件写入/编辑的正文参数只保留长度摘要。过长的有效 JSON 对象参数摘要、diff 或命令输出保留头尾，并插入 `original_chars` 标记；非法或非对象参数只显示形态和长度。展示文本先脱敏，再把终端控制字符转成可见转义；diff、stdout/stderr 和最终回答只保留普通 LF 换行，元数据保持单行，避免 ANSI、OSC、回车、退格或双向格式字符伪造顶层事件和审批标记。已识别的当前 API Key、Bearer 值和常见凭据会脱敏，但 diff/stdout/stderr 仍需人工检查后再公开；CLI 仍打印最终模型回答。程序不主动持久化 trace，且 trace 与独立的脱敏 JSONL audit 相互独立；终端历史、重定向或录屏仍可能保存输出。

终端输出 sink 失败不会改变 AgentLoop 的执行结果；输出不主动排版，实际换行由终端处理，尚未验证真实窄终端的中文显示宽度与编码行为。2026-08-30 的 UTF-8 合成预览按 100/40 字符宽度用 `textwrap` 查看了模型事件、长中文路径、超过 4000 字符且保留 `HEAD`/`TAIL` 与 `original_chars` 的 edit diff、失败命令、中文 stderr、`[审批] ALLOW` 和 usage/停止原因，并确认 argv/cwd 合成 secret 未出现；该样例不代表真实终端列宽或编码兼容性。该界面不依赖 Rich 或 TUI，不提供动画、鼠标交互或流式 tool-call 展示。

## 安全边界

已实现工作区真实路径限制、敏感文件拒绝、符号链接/目录联接逃逸检查、文件读取状态与哈希覆盖保护、命令审批、`shell=False`、固定工作目录、超时、输出上限、当前 Windows 场景的进程树终止和统一凭据脱敏。请求前会为输出预留空间并按完整工具回合裁剪上下文；极简审计只写脱敏执行摘要。这些措施是应用层防护，不构成操作系统沙箱；用户批准的代码仍可能访问网络或工作区外资源。

## 人工审核

每个提交都必须先展示完整暂存差异、验证证据、风险和敏感信息扫描结果，并由项目作者明确批准。公开审核规则见 [`REVIEWING.md`](REVIEWING.md)，逐次审核结论见 [`REVIEW_LOG.md`](REVIEW_LOG.md)。审核通过与允许远端推送是两个独立门禁。

## 开发状态

执行顺序为 M0 -> P0 -> R0 -> M1 -> M2 -> M3 -> M4 -> E1/E2/E3。当前 Windows/Python 3.11 上完整离线测试通过；脚本化假模型覆盖读文件、搜索、两次修改、首次测试失败、再次测试成功和最终总结，并逐轮核对回放字段与工具 ID 配对。测试进程会拒绝未模拟的真实 socket 连接。

2026-08-28 的隔离 buggy Python 项目真实验收使用 `kimi-k3`：初始独立测试为 2 失败、1 通过，Agent 只修改生产文件，最终独立复跑为 3 通过；状态 `COMPLETED`，共 6 轮、6 次工具调用，供应商报告 10,890 tokens。审计中未出现当前 API Key、Authorization 或 Bearer；脱敏结构化证据见 [`docs/evidence/m4-real-task.json`](docs/evidence/m4-real-task.json)。Ubuntu CI 尚未验证；`/history` 未注册。本地 token 值只是按计划公式计算的粗估，不是供应商 tokenizer 上界。详细设计和已验证边界见 `DESIGN.md`。

## 黄金 Eval

冻结的订单价格流水线任务通过 `python -m evals.run_golden --report <NEW_REPORT.json>` 在三个全新临时工作区运行。该 harness 只自动批准明确的 pytest，测试文件哈希必须保持不变；这不构成 OS 沙箱。

2026-08-29 的最终正式 `kimi-k3` 运行中，该固定任务三次中成功三次。三次初始 pytest 均为非零退出，最终 pytest 均为零；测试哈希未变，变更路径均仅为 `pricing.py` 和 `invoice.py`。脱敏的三次完整结果见 [`docs/evidence/e2-golden-eval.json`](docs/evidence/e2-golden-eval.json)。完整补丁审查曾发现评测器子进程未显式净化环境；修复前报告未发现实际凭据泄露，但不计入最终结论，已原样保留为 [`docs/evidence/e2-golden-eval-pre-env-sanitization.json`](docs/evidence/e2-golden-eval-pre-env-sanitization.json)。该结果只描述这一个冻结任务的三次样本，不泛化为整体成功率。
