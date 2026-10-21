# QPM Agent

QPM Agent 是面向 **DeepSeek Harness** 的 LN/LT 薄膜准相位匹配设计插件。当前稳定版 **0.3.0** 提供 Harness 原生插件、`qpm-design` Skill、任务计划与校验工具、演示/COMSOL工作流接入、结果续算和绘图。

> 本仓库的当前发行产品是 Harness 插件。QPM Python Core 0.1.0 wheel 作为运行依赖附在插件安装 ZIP 中；本 Release 不单独发布 Core。历史 v0.2.0 Release 保留。

## 下载与兼容

从 [QPM Agent 0.3.0 正式版](https://github.com/1412kid-dot/qpm-agent/releases/tag/v0.3.0)下载Windows安装 ZIP。首个兼容目标是 Windows 桌面 DeepSeek Harness **0.2.0-rc.2**；其他 Harness 版本、Linux、macOS 尚未验证。

插件工作流在当前实现能力范围内完成初步验收；代表性物理检查通过。超出当前插件能力的语义任务和更完整自主研究流程属于后续版本扩展范围，不应将本版理解为面向任意需求的通用自主研究 Agent。0.3.0 不更改物理求解实现，不重复进行COMSOL科学试算或付费模型评测。

## 运行环境要求

安装包不包含 Harness、COMSOL、许可证或模型 API 凭据。使用前需要：

- Windows 桌面 DeepSeek Harness 0.2.0-rc.2，并在 Harness 自身配置可用模型/API；Harness 已自带 Node/pnpm。
- 64 位 Python 3.12+；NumPy ≥2.0、SciPy ≥1.13、pandas ≥2.2、Matplotlib ≥3.8。Core wheel 随插件 ZIP提供，科学计算库需在所选解释器中准备。
- 真实 COMSOL 仿真还需自行安装 COMSOL Multiphysics（验证基线6.4）、有效许可证及模型所需的 Wave Optics / EWFD 模式分析功能；Python端需要 MPh ≥1.3.1 和 JPype1 ≥1.5。演示后端不启动 COMSOL。

安装、专用 Python 环境、配置、任务流程、历史结果、续算、绘图、卸载回退与故障排查请看[完整中文技术手册](harness_plugin/docs/技术手册.md)。

## 快速安装

1. 在 Harness 配置模型/API。
2. 下载并解压完整 Windows ZIP；退出 Harness。
3. 准备上述 Python 环境，在解压目录运行：

```powershell
.\Install-Qpm.cmd -PythonExecutable 'C:\Apps\QPM\.venv\Scripts\python.exe' -PythonMode Existing
```

4. 重启 Harness 并运行插件环境检查；先用默认 demo 后端验证，之后再显式配置和开启 COMSOL。

PowerShell 5.1/7、安装失败恢复及完整环境准备步骤见技术手册。校验和与发行摘要也可从 Release 附件下载。

## 仓库结构

- `harness_plugin/`：Harness插件源码、预编译模块、Skill、Windows安装器和技术手册。
- `qpm_simulation/`：Python Core源码，用于此仓库整体的源码审阅和开发；独立Core发布不在本次Release范围。
- `tests/`：数值Core测试；插件测试位于 `harness_plugin/tests/`。

版本改动见[CHANGELOG](CHANGELOG.md)、[插件兼容表](harness_plugin/COMPATIBILITY.md)和[验证说明](harness_plugin/VALIDATION.md)。
