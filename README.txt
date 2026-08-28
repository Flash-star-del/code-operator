code-operator

Git 仓库地址：https://github.com/Flash-star-del/code-operator

一、安装与启动
要求 Python 3.11。使用“python -m pip install -r requirements.txt”安装，再运行“python -m code_operator --workspace <WORKSPACE> \"<TASK>\"”。必须通过环境变量提供 CODE_OPERATOR_API_KEY=<YOUR_API_KEY>、CODE_OPERATOR_BASE_URL=https://api.moonshot.cn/v1 和 CODE_OPERATOR_MODEL=kimi-k3。

二、特色
已独立实现 ModelClient、AgentLoop 和六个本地工具，通过模型原生 tool calling 完成真实文件修改与命令验证；支持四层错误、完整回合上下文裁剪、重复/连续失败终止及严格测试自动审批。

三、安全边界
项目限制文件真实路径并拒绝敏感文件和链接逃逸，以读取状态和哈希保护覆盖写入；命令使用参数数组、固定目录、超时和净化环境，并要求审批。这些措施不构成操作系统沙箱，获批代码仍可能访问外部资源。

四、当前状态
已完成 M0 至 M2 验收；M3 核心功能验证完成、提交审核待办：205 项核心离线测试通过，普通终端 Ctrl-C 已确认停止子进程且不再请求模型。kimi-k3 真实闭环为 3 轮请求、2 次工具调用、退出码 0。审核规则见 REVIEWING.md、REVIEW_LOG.md。
