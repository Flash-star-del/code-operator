code-operator

仓库：https://github.com/Flash-star-del/code-operator

要求 Python 3.11。安装：python -m pip install -r requirements.txt。环境变量：CODE_OPERATOR_API_KEY=<YOUR_API_KEY>、CODE_OPERATOR_BASE_URL=https://api.moonshot.cn/v1、CODE_OPERATOR_MODEL=kimi-k3。可用 --max-model-rounds N 调整单次任务的模型轮次上限。

一次性模式：python -m code_operator --workspace <WORKSPACE> "<TASK>"，执行后退出。交互模式：省略 <TASK>，在同一进程连续输入任务；只支持整行 /help、/status、/init、/undo、/new、/exit。/undo 以后进先出方式撤销最近一次成功且确实改变文件的直接 write_file/edit_file，校验路径类型与外部哈希，不撤销 run_command；/new 清对话、读状态和撤销记录但不恢复文件；/help 列出本地命令，/status 报告会话状态与累计 token，/init 生成或更新 AGENT.md。会话与撤销仅在内存中，无跨进程 Resume 或 checkpoint。

项目独立实现 ModelClient、AgentLoop、六工具、策略、上下文和会话；不用 Agent SDK。支持原生 tool calling、ID 配对、完整组裁剪、中止后结果补齐、路径/敏感文件/链接逃逸防护、命令审批、超时、进程树终止和脱敏审计。应用层防护不构成 OS 沙箱。

Windows/Python 3.11 离线测试覆盖上述行为；kimi-k3 固定订单任务三个全新工作区 3/3 完成，只代表该样本。docs/agent-comparison-results.md 记录与 Kimi Code 的双系统横评（三个合成任务、单次运行、独立隐藏测试评分）。CLI 为脱敏、有界、终端安全的普通文本轨迹。Ubuntu、E4 真实 Session API 探针、真实窄终端中文编码行为和第一支完整录像未验证。详见 DESIGN.md、DEFENSE.md、REVIEW_LOG.md。
