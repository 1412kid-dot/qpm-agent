# QPM Agent 0.2.0

面向 Windows DeepSeek Harness 0.2.0-rc.2 的原生插件与可安装 bundle。复用 Harness 的模型/API 配置，调用配套 qpm-simulation 0.2.0 Python 功能包。安装无需编译 TypeScript。内置原生 qpm-design Skill、五类任务模板、25项QPM工具及经审核的后处理扩展。

## 首次安装

### 必备环境

- Windows；兼容目标为桌面 DeepSeek Harness **0.2.0-rc.2**。桌面随附 Node ≥24；普通安装不需要另装 Node/pnpm。
- **64 位 Python 3.12+**（本机验收 3.12.3），建议专用虚拟环境。
- Python 科学计算依赖：numpy ≥2.0、scipy ≥1.13、pandas ≥2.2、matplotlib ≥3.8。
- **真实仿真还需用户自行安装并授权 COMSOL Multiphysics**，当前适配 6.4；许可证须包含 `ewfd` 与模式分析所需接口，标准光学路径为 Wave Optics Module。Python 还需 MPh ≥1.3.1（本机验收 1.3.1）、JPype1 ≥1.5。
- COMSOL 自带 Java，通常无需另装 JRE；应保证 Python 与 COMSOL 都为 64 位并配置正确的 COMSOL 路径。插件不附带仿真软件、许可证或模型 API Key。
- 工作区、临时目录和绘图缓存目录需要可写。demo/历史绘图仍需要上述科学计算依赖，但不启动 COMSOL。

可先创建专用环境（需要已安装 Python 和网络）：

```powershell
py -3.12 -m venv C:\Apps\QPM\.venv
& 'C:\Apps\QPM\.venv\Scripts\python.exe' -m pip install 'numpy>=2.0' 'scipy>=1.13' 'pandas>=2.2' 'matplotlib>=3.8' 'MPh==1.3.1' 'JPype1>=1.5'
```

填写该环境的 python.exe 后执行 `Check-Qpm.cmd`。依赖导入和安装静态校验通过不代表 COMSOL 许可证或科学计算已验证；真实后端需明确启用并先做小规模试算。Lumerical 尚未作为已验收后端交付。

先退出 Harness。解压完整发布 ZIP，运行 PowerShell：

```powershell
.\Install-Qpm.cmd -PythonExecutable 'E:\work\Python\Comosol\Period\.venv\Scripts\python.exe' -PythonMode Existing
```

Existing 模式只把 wheel 解压到插件私有目录，通过子进程 PYTHONPATH 调用；不安装依赖到旧虚拟环境。此解释器必须已有 numpy、scipy、pandas、matplotlib；真实仿真另需 MPh、JPype。Isolated 模式在解压目录创建新虚拟环境并安装 wheel 与依赖（需要网络）。不复制原环境。

重新启动 Harness，在插件设置选择「QPM设计助手」，核对解释器与结果目录，然后在新会话选择「QPM 设计助手」预设。先保持 demo。已有 API/模型设置无需在插件中再次填写。

所有安装相关 `.ps1` 脚本均以 **UTF-8 BOM** 保存，兼容 Windows PowerShell 5.1 的中文读取。发布脚本自动确保 BOM。本次升级为0.2.0，请完整解压新版后按原命令安装；新包更新QPM登记及私有Python包，保留用户配置。旧解压目录中的结果、扩展和备份保留。

```powershell
.\Check-Qpm.cmd -PythonExecutable '你的python.exe路径'
.\Uninstall-Qpm.cmd
```

配置、示例、故障处理见 [详细使用说明](docs/使用说明.md)，开发与接口见 [开发说明](docs/开发说明.md)。设计规范见 [QPM Skill](skills/qpm-design/SKILL.md)，新指标接口见 [扩展说明](docs/指标扩展.md)。不支持自动覆盖核心求解器。

## 验证边界

演示数据是合成模型，只用于软件流程验证。真实模型聊天、COMSOL x切对照和z切代表结构科学验收尚未执行。精确验收记录见 VALIDATION.md。取消时管理整个任务进程范围；强制结束可能留有不完整缓存，后续读取仍进行校验。

## 本地分发

源码 src/、预编译 lib/、dsh-qpm-agent-0.2.0.tgz、配套 wheel、安装脚本均包含在发布 ZIP 内。暂不发布 npm/GitHub；正式开源前需补充作者选定的许可证和既有脚本/材料模型的来源许可。

## desktop 安装修正版

安装与检查不再调用 desktop 的 `--dump-config`（该版本禁止此命令）。使用桌面随附运行时的官方只读配置合成接口进行静态校验，结果会明确注明桌面实际加载待验证。重新安装保留COMSOL、后端、预算、公差与扩展审核设置；仅更新解释器和配套Python包位置。安装失败记录阶段和退出码，自动恢复本次QPM变更；如提示需人工恢复，使用错误中给出的 `Repair-Qpm.cmd -BackupDirectory ...` 命令。不要整份恢复旧profile覆盖其他插件或API配置。

必须解压完整ZIP后运行，安装前校验文件完整性。不能只复制ps1。一次完整发布运行 `python dev/release.py`，重建插件、tgz、wheel、BOM、ASCII/CRLF启动器与校验清单，并强制从最终ZIP在Restricted父会话中验收Windows PowerShell 5.1/7的CMD安装入口。失败则发布命令失败。`--build-only`仅供开发，不能称安装验收通过；对应ZIP的验收摘要在dist/INSTALLER-ACCEPTANCE.json。

## Windows执行策略修正（0.2.0安装包更新）

推荐调用根目录`.cmd`入口，不直接运行ps1。启动器使用系统Windows PowerShell 5.1，指定`-NoProfile -ExecutionPolicy Bypass`，仅作用于该子进程，不修改CurrentUser/LocalMachine策略。MachinePolicy/UserPolicy组策略仍具有优先级；受组织策略限制的设备需按组织要求处理，不声明可绕过。四个入口包括安装、检查、卸载和恢复。

若仍使用原ps1，可显式运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "完整脚本路径" ...`。不需要以管理员身份永久降低策略。完整原因和发布检查见[Windows兼容性说明](docs/Windows兼容性.md)。

## 本次覆盖更新：三点差分与可靠续算

仍使用QPM-Agent-0.2.0目录和包名。安装后请重启Harness，并新建会话加载更新的Skill。

- 默认色散只求解λ±2 nm及中心三点；五点只在明确指定波长检查，不自动九点或精度加密。
- 只要求信号光GVD时只求解该波段；通过dispersion_roles选择其他角色。
- qpm_estimate/qpm_plan给出去重采点、经过校验的缓存与历史用时范围。无历史不编造时间。
- qpm_runs查询历史run_id，qpm_resume续算并复用完整缓存；临时进程故障预算内最多自动恢复两次。时间/求解上限是累计值，耗尽后需用户允许提高，不能无限重启。
- 默认不做额外网格/半步长验收，结果明确标为收敛未验证。
- 跨任务复用仅接受物理设置与已知实现版本一致、数据校验合格的缓存。旧失败任务缺乏完整日志时，之前的总成本只能视为记录不完整；新版本逐点记账。

完整说明见[续算与低成本色散](docs/续算与低成本色散.md)。Windows安装继续使用Install-Qpm.cmd，保留BOM与最终ZIP验收流程。

同版本覆盖升级使用按tgz校验和命名的私有安装源，并核对安装后的代码与本次发布内容一致，避免pnpm沿用同名旧包。默认发布同时强制Python与插件回归。


本次更新见 [0.2.0 更新说明](docs/UPGRADE-20261008.md)。

历史结果读取修复与调用方法：[说明](docs/HISTORY-FIX.md)。


## 跨run绘图（2026-10-08）
先qpm_runs获取完整run_id。qpm_plot(sources=[{alias:"基准",run_id:...},{alias:"第二轮",run_id:...}],preset={series:[{source:"基准",metric:"signal_gvd_fs2_mm"},{source:"第二轮",metric:"signal_gvd_fs2_mm"}],formats:["png","svg","pdf"]},name="色散对比")。历史来源只读，输出当前工作区；不启动求解器，不占仿真预算。不同量纲选择layout="subplots"或"dual_axis"并指定axis="right"；二维扫描选择"heatmaps"。
script无需源码修改审核，仅支持Python形状的plot.option(title="标题",...)和plot.series(source="基准",metric="...",...)字面量调用；不支持import、循环、任意函数或文件访问。scale、normalize、x_range、interpolate_nm、smooth_window可明确控制数值展示，缺失点不会跨段插值。普通绘图无需申请修改求解源码。完整说明见docs/PLOTTING.md。
