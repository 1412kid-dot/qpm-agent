# QPM Agent for DeepSeek Harness 0.3.0

QPM Agent 将薄膜铌酸锂/钽酸锂准相位匹配设计流程封装成 DeepSeek Harness 原生插件：模型通过受约束的工具理解任务、校验参数、制定计划，再调用随包提供的 Python Core 执行演示或已配置的 COMSOL 工作流，并读取结果、续算或绘图。

## 发布状态

- 正式版本：**QPM Agent 0.3.0**；配套 Python Core **0.1.0**，仅作为插件 ZIP 内的运行依赖，不单独发布 Core。
- 首个兼容目标：Windows 桌面版 DeepSeek Harness **0.2.0-rc.2**。其他 Harness 版本、Linux 和 macOS 尚未验证。
- 插件工作流在实现范围内已完成初步验收；插件能力边界之外的语义任务及更完整的自主研究流程将在后续版本扩展。
- 代表性物理检查通过。结果适用范围受材料模型、几何、模式连续性和 COMSOL 设置约束，不代表任意设计都经过科学验证。

## 能做什么

插件提供原生 `qpm-design` Skill、中文任务预设、设置页和结构化工具。可查询设计约定与能力，校验任务并估算预算，执行固定结构、公差、结构搜索、SPDC PMF 和 SFG 二维扫描；可访问登记的历史运行、续算、比较绘图及经人工审核的任务适配/后处理扩展。三点二阶差分为常规色散默认方案；仅在异常点或明确任务要求时使用更高阶差分。

当前范围及物理约定以随包 Skill 和运行时 `qpm_capabilities` 查询为准。模型必须把明确指标写入任务并遵守 Python 校验和预算；未实现的物理能力不会被视作已计算。

## 环境要求

插件本身不提供 Harness、COMSOL、许可证或模型 API 凭据。

- Windows 桌面版 DeepSeek Harness 0.2.0-rc.2；Harness 自带 Node/pnpm，普通用户无需另装 Node。
- 64 位 Python 3.12 或更高版本，以及 NumPy、SciPy、pandas、Matplotlib。安装包附带 Core wheel；科学计算依赖按技术手册安装。
- 真实 COMSOL 后端还需要用户自行安装的 COMSOL Multiphysics（本机验证基线 6.4）、有效许可证和兼容的 MPh/JPype Python 桥接。COMSOL 许可证需包含使用模型所需的 Wave Optics/模式分析功能。演示后端不启动 COMSOL。
- 详细版本、配置和诊断步骤见[中文技术手册](docs/技术手册.md)。

## 安装

1. 在 Harness 中先配置你自己的模型/API。
2. 下载 [v0.3.0 正式版](https://github.com/1412kid-dot/qpm-agent/releases/tag/v0.3.0)，解压完整 Windows ZIP。
3. 按[安装章节](docs/技术手册.md#4-windows-安装)创建或选择 Python 环境，然后运行 ZIP 根目录 `Install-Qpm.cmd`。推荐 CMD 入口，兼容 PowerShell 5.1/7 常见执行策略限制。
4. 重启 Harness，在插件设置中运行环境检查；先使用 demo 后端验证工作流，再按手册显式配置并启用 COMSOL。

安装脚本支持 Existing（复用现有解释器但不向其安装 Core）和 Isolated（创建专用环境并安装可选依赖）两种方式。安装/卸载、升级与回退见技术手册。每个发布文件的 SHA256 在 Release 附件和 ZIP 内清单中。

## 版本与升级

0.3.0 是取代开发版 dev.10 的稳定版本。v0.2.0 历史 Release 保留。升级前备份用户结果目录；退出 Harness 后用完整 0.3.0 ZIP 执行安装，现有用户配置由安装器保留。需要回退时安装 0.2.0 完整 ZIP，并检查该版本的配置兼容说明；不要用旧配置覆盖当前整个 Harness profile。

查看[变更记录](CHANGELOG.md)、[兼容表](COMPATIBILITY.md)和[验证说明](VALIDATION.md)。开发者构建流程见[技术手册](docs/技术手册.md#12-开发构建与发行包验收)。
