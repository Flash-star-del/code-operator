# code-operator

code-operator 是一个正在从零实现的命令行编程智能体（coding agent）。目标是通过模型原生 tool calling，在受约束的本地工作区内读取和修改文件、执行命令，并根据真实执行结果继续完成编程任务。

项目不使用 agent 框架或 Agent SDK。AgentLoop、协议处理、工具执行、安全策略、上下文管理、循环终止和错误处理将独立实现。当前仓库仍处于 M0 交付骨架阶段，未完成或未验证的能力不视为已经实现。

## 配置约定

后续运行时只从环境变量读取以下三个必要配置：

```bash
NANOCODE_API_KEY=<YOUR_API_KEY>
NANOCODE_BASE_URL=<YOUR_OPENAI_COMPATIBLE_API_ROOT>
NANOCODE_MODEL=<YOUR_MODEL_ID>
```

API 根地址、模型 ID、输出上限参数和消息回放字段将在 P0 真实协议探针通过后写入准确示例；在此之前不提供未经验证的默认值。API Key 不得写入仓库、README.txt、日志或演示视频。

## 安装与运行

项目要求 Python 3.11。可先安装运行依赖：

```bash
python -m pip install -r requirements.txt
```

CLI 尚未实现；准确启动命令将在 M1 通过测试和真实闭环后补充。

## 安全边界

计划中的工作区路径限制、命令审批、超时和凭据净化是应用层防护，不构成操作系统沙箱。只有通过自动测试或真实验收的能力才会写入最终功能说明。

## 开发状态

执行顺序为 M0 -> P0 -> R0 -> M1 -> M2 -> M3 -> M4 -> E1/E2/E3。详细设计和已验证边界见 `DESIGN.md`。
