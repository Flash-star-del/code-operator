code-operator

Git 仓库地址：https://github.com/Flash-star-del/code-operator

一、安装与启动
要求 Python 3.11。运行依赖使用“python -m pip install -r requirements.txt”安装。程序完成 M1 实现并通过验证后，在此补充准确启动命令。运行时必须通过环境变量提供 CODE_OPERATOR_API_KEY=<YOUR_API_KEY>、CODE_OPERATOR_BASE_URL=https://api.moonshot.cn/v1 和 CODE_OPERATOR_MODEL=kimi-k3。

二、特色
目标是独立实现 ModelClient、AgentLoop、工具注册与本地执行、安全策略、上下文管理、错误分流和循环终止，通过模型原生 tool calling 完成真实编程任务。未通过测试或真实验收的能力不作为已完成功能宣传。

三、安全边界
项目计划限制文件操作范围、拒绝敏感文件、使用参数数组执行命令、设置超时并净化子进程凭据。上述措施属于应用层工作区约束与命令审批，不构成操作系统沙箱。

四、当前状态
目前完成 M0，并已通过 P0 真实探针确认上述配置、max_tokens 输出限制和非思考模式原生工具调用；每次提交的人工审核规则和公开台账见 REVIEWING.md、REVIEW_LOG.md；启动命令将在 M1 获得真实证据后更新。
