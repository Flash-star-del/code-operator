# code-operator Logo 设计

日期：2026-09-02

## 目标

为项目提供统一的视觉标识，覆盖两个场景：GitHub 仓库门面（README）与交互模式启动画面（终端）。

## 视觉概念

方括号包循环箭头 `[⟳]`：方括号象征代码与工具调用，循环箭头象征 agent 核心闭环（读取 → 执行 → 根据真实结果继续推理）。

配色：深色底 `#0d1117`、细描边 `#30363d`、主体终端绿 `#3fb950`。

## 交付物

1. **SVG 图标** `docs/assets/logo.svg`
   - 256×256 画布，圆角方形深色底。
   - 左右方括号 + 约 270° 顺时针圆弧，弧末端为上指箭头（chevron）。
   - 全部描边风格：终端绿、圆头线帽、线宽 16。纯手写 SVG，无构建依赖。
2. **README 顶部** 改为居中 logo（宽 120px）+ `<h1>code-operator</h1>`，正文不动。
3. **ASCII 启动 banner**（`code_operator/__main__.py`）
   - 模块级常量 `_BANNER`，纯 ASCII（规避 Windows GBK 控制台编码问题）：

     ```
      [ ,-. ]
      [ `-> ]  code-operator
     ```

   - 在 `_run_interactive` 进入主循环前打印一次。
   - 一次性模式（`_run_one_shot`）不打印，保持脚本/CI 输出干净。

## 测试

- 新增用例：交互模式输出包含 `_BANNER` 且 `_BANNER` 为纯 ASCII。
- 现有交互测试均为子串断言，不受影响。

## 非目标

不做 PNG/favicon/多尺寸导出；不引入图像构建工具链。
