# QPM Agent

面向 **DeepSeek Harness** 的 LN/LT 薄膜准相位匹配设计助手：原生插件、`qpm-design` Skill 和独立 Python 仿真工作流。

支持任务规划、SHG/SPDC/SFG、色散与 GVM、工艺公差、低成本结构搜索、检查点续算、历史结果读取，以及跨轮次绘图。模型/API 由 Harness 管理，本插件不包含 API Key。

**首个公开版本：0.2.0 预发布。** 软件演示与安装验收、真实模型聊天验收、真实 COMSOL 科学验收分别记录。演示数据是合成数据，不能作为器件设计结论。真实 COMSOL 科学验收仍待完成。

## 下载与安装

### 运行环境要求（安装前阅读）

本插件调用用户本机的 Python 与仿真软件，**安装 ZIP 不等于安装了完整仿真环境**。真实器件仿真需要下列环境：

| 组件 | 要求与作用 |
|---|---|
| 操作系统 | Windows；安装启动器兼容 Windows PowerShell 5.1，另有 PowerShell 7 验收 |
| DeepSeek Harness | 首个兼容目标为桌面版 0.2.0-rc.2；先配置可用模型/API |
| COMSOL Multiphysics | 用户自行安装并具备有效许可证；当前适配目标 6.4，需支持工作流使用的光学电磁场模式分析与 Java/API 自动化接口；插件不附带软件或许可证 |
| Python | **64 位 Python 3.12+** 的独立虚拟环境，或已有且依赖满足要求的解释器；当前开发环境为 3.12.3 |
| 科学计算包 | numpy ≥2.0、scipy ≥1.13、pandas ≥2.2、matplotlib ≥3.8 |
| COMSOL 自动化包 | **MPh ≥1.3.1、JPype1 ≥1.5**；目标验收环境 MPh 1.3.1，其他版本需自行核验 |
| Node | Harness 原生插件需要 Node ≥24；桌面版使用自带运行时，普通安装无需另行安装 Node 或 pnpm |
| 文件权限 | 可读的历史来源，以及可写的工作区/结果目录、临时目录与绘图缓存目录 |

当前建模使用 `ElectromagneticWavesFrequencyDomain`（`ewfd`）与 Mode Analysis，请确认许可证包含对应物理接口；标准光学路径为 [Wave Optics Module](https://doc.comsol.com/6.4/doc/com.comsol.help.woptics/woptics_ug_intro.4.05.html)。

MPh 通过 JPype 调用 COMSOL Java API；COMSOL 自带 Java 运行时，**通常无需另装 Java**。Python 位数需要与 COMSOL 匹配；Windows 建议使用 python.org 的 64 位安装程序，参考 [MPh 安装说明](https://mph.readthedocs.io/en/stable/installation.html)。自定义 COMSOL 路径仍需正确配置。以 `Check-Qpm.cmd` 与显式小规模真实试算为准，环境检查不会自动试用许可证或启动真实求解。模型/API 接入需要用户自己的网络与账户配置。

建议新建一个专用环境，而不是改动其他项目的 Python 环境：

```powershell
py -3.12 -m venv C:\Apps\QPM\.venv
& 'C:\Apps\QPM\.venv\Scripts\python.exe' -m pip install 'numpy>=2.0' 'scipy>=1.13' 'pandas>=2.2' 'matplotlib>=3.8' 'MPh==1.3.1' 'JPype1>=1.5'
& 'C:\Apps\QPM\.venv\Scripts\python.exe' -c "import numpy, scipy, pandas, matplotlib, mph, jpype; print('Python dependencies import OK')"
```

上述命令需要已安装 Python 3.12 和网络。导入依赖成功只说明 Python 环境可用，**不等于 COMSOL 许可证、物理模块、求解与科学精度已验证**。配置插件时填入这个环境的 `python.exe`，然后填 COMSOL 路径，先执行环境检查，再按文档显式启用真实后端。

仅使用 `demo`、历史数据分析或绘图时，Python 科学计算依赖仍必需；这些操作不需要启动 COMSOL。只提供 LLM API 而没有本机 Python/COMSOL 环境，无法执行真实仿真。本轮主要实现 COMSOL 工作流；Lumerical 自动化尚未作为已验收后端交付。

普通用户请到 [Releases](https://github.com/1412kid-dot/qpm-agent/releases) 下载完整 Windows ZIP，解压到可写目录，关闭 Harness 后执行：

```powershell
.\Install-Qpm.cmd -PythonExecutable 'C:\Apps\QPM\.venv\Scripts\python.exe' -PythonMode Existing -HarnessRoot 'E:\Deepseek Harness'
```

把示例路径替换为实际路径。Existing 模式要求解释器已有 numpy、scipy、pandas、matplotlib；插件将自己的 wheel 放入私有目录，不改已有虚拟环境。真实 COMSOL 后端还需合法安装 COMSOL、有效许可证以及 MPh、JPype。Isolated 模式可创建独立环境，安装依赖时需要网络。

**使用 `.cmd` 入口**，避免 Windows 默认脚本策略直接阻止 `.ps1`。启动器仅对本次子进程使用 Bypass，不改变系统或用户的持久执行策略；PowerShell 脚本保留 UTF-8 BOM。desktop 配置采用只读解析校验，不使用该版本不支持的 `--dump-config`。

```powershell
.\Check-Qpm.cmd -PythonExecutable 'C:\Apps\QPM\.venv\Scripts\python.exe'
.\Uninstall-Qpm.cmd
```

启动 Harness，配置模型，选择“QPM 设计助手”。先保持 `demo` 后端；核对 Python、COMSOL、预算与输出路径后，才启用真实仿真。

示例任务：“在 x 切 LN、空气包层上，厚度 600 nm、刻蚀 300 nm、顶宽 1200 nm，TE0，计算 1550 nm 基频 SHG 的周期、色散、GVM 和厚度 ±5 nm 公差。先生成计划，使用演示后端。”

## 能力与物理约定

支持 LN/LT、x/z 切、TE0/TM0、AIR/SiO₂ 的配置组合。x 切默认沿晶体 Y 传播、TE0 主场沿晶体 Z；z 切默认沿晶体 X 传播、TM0 主场沿晶体 Z。其他组合的线性指标可用，不能未经张量核对套用 d33 效率。

默认三点二阶差分；异常点可在限额内升级五点/七点，模式连续性失败先按模式问题处理。结构搜索使用真实采点与代理模型指导选点，代理预测不冒充仿真结果。缓存与续算核对任务、物理设置和实现版本。SPDC PMF 不代表绝对光子对产生率；没有输入泵浦功率时，归一化过程系数不称为实际转换百分比。

跨 run 绘图可读取所有已登记历史来源，新图输出到当前可写工作区。历史原数据保持只读。普通绘图不消耗仿真轮次；通用扩展代码测试后需审核启用。

## 源码与开发

| 位置 | 内容 |
|---|---|
| `qpm_simulation/` | 配置、材料/晶向、求解器适配、指标、搜索、缓存、绘图 |
| 根目录 `LN_LT_*.py`、`dispersion_core.py` | 既有自动化逻辑及共享数值模块 |
| `harness_plugin/src/`、`lib/` | 原生插件源码与预编译模块 |
| `harness_plugin/skills/qpm-design/` | 设计规范和五类任务模板 |
| `harness_plugin/scripts/`、`dev/` | 安装、校验、打包和隔离验收 |
| `tests/`、`harness_plugin/tests/` | Python 与插件回归测试 |

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m pytest tests -q
```

完整插件构建与发布步骤见 [开发与发布](docs/BUILD.md)。用户环境、历史仿真结果、许可证、模型配置、Harness 运行时及从安装提取的 SDK **不在仓库中**。仓库不分发 COMSOL、Lumerical 或 Harness。

兼容目标：Windows 桌面 Harness **0.2.0-rc.2**、Node **24.18.1**、Python **3.12+**；本机科学环境 COMSOL **6.4**、MPh **1.3.1**。其他 Harness 版本未验证。详见 [兼容说明](harness_plugin/COMPATIBILITY.md)、[使用说明](harness_plugin/docs/使用说明.md) 和 [发布验证](docs/VALIDATION.md)。

MIT 许可证，参见 [LICENSE](LICENSE)。贡献与问题反馈见 [CONTRIBUTING.md](CONTRIBUTING.md)。

An open-source QPM workflow plugin for DeepSeek Harness, with Python simulation tools, adaptive search, resumable jobs and cross-run plotting. This initial release is a software preview; real COMSOL scientific validation remains pending.
