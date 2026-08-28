code-operator

Git 仓库地址：https://github.com/Flash-star-del/code-operator

一、安装与启动
要求 Python 3.11。执行“python -m pip install -r requirements.txt”安装，再运行“python -m code_operator --workspace <WORKSPACE> \"<TASK>\"”。必须通过环境变量提供 CODE_OPERATOR_API_KEY=<YOUR_API_KEY>、CODE_OPERATOR_BASE_URL=https://api.moonshot.cn/v1、CODE_OPERATOR_MODEL=kimi-k3。可用 CODE_OPERATOR_MAX_MODEL_ROUNDS 等变量收紧轮次和上下文限制。

二、特色
独立实现 ModelClient、AgentLoop、六个本地工具和安全策略，使用模型原生 tool calling 完成读取、搜索、修改和真实测试闭环；支持协议白名单、错误回灌、完整回合裁剪、重复/连续失败终止、Ctrl-C 子进程清理和脱敏审计。

三、安全边界
文件工具限制真实路径并拒绝敏感文件和链接逃逸，以完整读取状态和哈希保护覆盖；命令使用参数数组、固定目录、超时、净化环境和审批。`--auto-approve-tests` 只信任明确的 pytest 命令。这些措施不构成 OS 沙箱，获批代码仍可能访问外部资源。

四、当前状态
基础版 v0.1.0 已发布；Windows/Python 3.11 当前 240 项离线测试通过。M4 的 kimi-k3 隔离修复为 6 轮、6 次工具调用，测试从 2 失败/1 通过变为 3 通过。Ubuntu 未验证。设计证据见 DESIGN.md、DEFENSE.md 和 REVIEW_LOG.md。
