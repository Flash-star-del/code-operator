code-operator——从零实现的命令行编程智能体

【仓库地址】
https://github.com/Flash-star-del/code-operator

【如何运行】
要求 Python 3.11。安装：python -m pip install -r requirements.txt
凭据仅通过环境变量提供（不入库）：CODE_OPERATOR_API_KEY、CODE_OPERATOR_BASE_URL（如 https://api.moonshot.cn/v1）、CODE_OPERATOR_MODEL（如 kimi-k3）。
一次性模式：python -m code_operator --workspace <目录> "<任务>"，按终态返回退出码。省略任务进入交互会话，支持整行本地命令：/help、/status、/init、/compact、/undo、/new、/exit。

【特色功能】
1. 核心闭环全部独立实现，未用任何 agent 框架/SDK：自研 ModelClient（原生 tool calling）、AgentLoop（模型轮次与工具调用 ID 严格配对、多重终止条件）、上下文窗口估算并按完整工具回合裁剪、六个本地工具（list_dir/read_file/grep/write_file/edit_file/run_command）。
2. 安全边界：工作区真实路径限制与符号链接逃逸检查、敏感文件拒绝、命令 shell=False+固定工作目录+超时+人工审批（可选仅自动放行 pytest）、进程树终止、统一凭据脱敏、终端控制字符消毒防伪造审批标记、脱敏 JSONL 审计。
3. 会话能力：/undo 基于变更日志的后进先出文件撤销，撤销前校验路径与内容哈希，防外部篡改；/compact 调用模型将历史压缩为摘要，失败时保持原历史；/init 让 agent 分析工作区生成 AGENT.md，会话启动时自动注入系统提示词，形成项目记忆闭环；/status 报告模型、撤销深度与累计 token。
4. 可验证证据：真实缺陷修复任务（初始 2 failed，修复后 3 passed，6 轮 6 次工具调用）与冻结黄金任务三次全部通过的脱敏证据存于 docs/evidence；离线测试 870+ 项，测试进程主动拒绝真实网络连接。docs/agent-comparison-results.md 另记录与 Kimi Code 的双系统横评（五个合成任务、单次运行、独立隐藏测试评分），横评定位的缺陷已当日修复并复跑验证，形成发现、修复、验证闭环。

【其他说明】
开发全程使用 AI 工具辅助，但每处设计均经人工审查后提交，审核门禁与逐次结论见 REVIEWING.md 与 REVIEW_LOG.md。诚实限制：应用层防护不构成操作系统沙箱；会话与撤销记录不跨进程持久化；黄金任务结果仅代表该固定样本，不泛化为整体成功率。架构细节与设计决策辩护见 DESIGN.md 与 DEFENSE.md。
