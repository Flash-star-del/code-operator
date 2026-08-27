code-operator

Git 仓库地址：https://github.com/Flash-star-del/code-operator

一、安装与启动
要求 Python 3.11。使用“python -m pip install -r requirements.txt”安装，再运行“python -m code_operator --workspace <WORKSPACE> \"<TASK>\"”。必须通过环境变量提供 CODE_OPERATOR_API_KEY=<YOUR_API_KEY>、CODE_OPERATOR_BASE_URL=https://api.moonshot.cn/v1 和 CODE_OPERATOR_MODEL=kimi-k3。

二、特色
已独立实现非流式 ModelClient、最小 AgentLoop、工具注册表及 list_dir、read_file、grep、write_file、edit_file、run_command，通过模型原生 tool calling 完成真实文件创建和命令验证。完整上下文管理和增强终止策略仍在后续里程碑实现。

三、安全边界
项目限制文件真实路径并拒绝敏感文件和链接逃逸，以读取状态和哈希保护覆盖写入；命令使用参数数组、固定工作目录、超时和净化后的子进程环境，并要求人工审批，当前 Windows 进程树超时探针已通过。这些措施不构成操作系统沙箱，获批代码仍可能访问外部资源。

四、当前状态
已完成 M0、P0、R0、M1 和 M2 验收：166 项离线测试通过。kimi-k3 在空临时工作区经 3 轮请求、2 次工具调用创建并运行 hello.py，退出码 0。审核规则和台账见 REVIEWING.md、REVIEW_LOG.md。
