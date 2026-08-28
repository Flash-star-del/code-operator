# code-operator

code-operator 是一个正在从零实现的命令行编程智能体（coding agent）。目标是通过模型原生 tool calling，在受约束的本地工作区内读取和修改文件、执行命令，并根据真实执行结果继续完成编程任务。

项目不使用 agent 框架或 Agent SDK。AgentLoop、协议处理、工具执行和安全策略均独立实现。当前已完成 M0、P0、R0、M1 和 M2 验收；M3 功能验证和本地提交审核已完成，尚待推送门禁。未完成或未验证的能力不视为已经实现。

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

当前六个工具 `list_dir`、`read_file`、`grep`、`write_file`、`edit_file` 和 `run_command` 均已接入最小安全闭环。文件修改返回受限长度的 unified diff；运行 Python、pytest 等命令时会显示参数数组和固定工作目录，并要求人工批准。

## 安全边界

已实现工作区真实路径限制、敏感文件拒绝、符号链接/目录联接逃逸检查、文件读取状态与哈希覆盖保护、命令审批、`shell=False`、固定工作目录、超时、输出上限、当前 Windows 场景的进程树终止和统一凭据脱敏。请求前会为输出预留空间并按完整工具回合裁剪上下文；极简审计只写脱敏执行摘要。这些措施是应用层防护，不构成操作系统沙箱；用户批准的代码仍可能访问网络或工作区外资源。

## 人工审核

每个提交都必须先展示完整暂存差异、验证证据、风险和敏感信息扫描结果，并由项目作者明确批准。公开审核规则见 [`REVIEWING.md`](REVIEWING.md)，逐次审核结论见 [`REVIEW_LOG.md`](REVIEW_LOG.md)。审核通过与允许远端推送是两个独立门禁。

## 开发状态

执行顺序为 M0 -> P0 -> R0 -> M1 -> M2 -> M3 -> M4 -> E1/E2/E3。当前 211 项离线测试通过；M3 已覆盖四层错误、完整回合上下文裁剪、重复调用/连续失败终止、usage 可用性、审批选项和脱敏审计。普通交互终端 Ctrl-C 已确认返回 `USER_ABORTED`、停止子进程且不再请求模型；`/history` 未注册。本地 token 值只是按计划公式计算的粗估，不是供应商 tokenizer 上界。M1 的 `kimi-k3` 真实闭环证据仍有效，详细设计和已验证边界见 `DESIGN.md`。
