# code-operator

code-operator 是一个正在从零实现的命令行编程智能体（coding agent）。目标是通过模型原生 tool calling，在受约束的本地工作区内读取和修改文件、执行命令，并根据真实执行结果继续完成编程任务。

项目不使用 agent 框架或 Agent SDK。AgentLoop、协议处理、工具执行、安全策略、上下文管理、循环终止和错误处理将独立实现。当前已完成 M0，并取得 P0 真实协议证据；未完成或未验证的能力不视为已经实现。

## 配置约定

后续运行时只从环境变量读取以下三个必要配置：

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

CLI 尚未实现；准确启动命令将在 M1 通过测试和真实闭环后补充。

## 安全边界

计划中的工作区路径限制、命令审批、超时和凭据净化是应用层防护，不构成操作系统沙箱。只有通过自动测试或真实验收的能力才会写入最终功能说明。

## 人工审核

每个提交都必须先展示完整暂存差异、验证证据、风险和敏感信息扫描结果，并由项目作者明确批准。公开审核规则见 [`REVIEWING.md`](REVIEWING.md)，逐次审核结论见 [`REVIEW_LOG.md`](REVIEW_LOG.md)。作者自审不冒充第三方独立审计，审核通过与允许远端推送是两个独立门禁。

## 开发状态

执行顺序为 M0 -> P0 -> R0 -> M1 -> M2 -> M3 -> M4 -> E1/E2/E3。详细设计和已验证边界见 `DESIGN.md`。
