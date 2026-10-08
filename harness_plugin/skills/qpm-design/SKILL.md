---
name: qpm-design
description: LN/LT薄膜准相位匹配设计规范、工具选择、晶轴与波长约定、工艺公差及经审核的指标扩展。相关仿真任务执行前加载。
---

# QPM 设计与仿真规范

你负责将自然语言设计任务转换为已实现的 QPM 工作流，执行并解释结果。规范负责指导，工具和 Python 校验负责实际能力边界。

## 执行步骤

1. 加载本 Skill；第一次仿真先查询 `qpm_capabilities`、`qpm_environment`。检查环境不会启动 COMSOL。
2. 按需调用 `qpm_conventions`：`crystal` 晶轴，`geometry` 尺寸，`wavelengths` 波长角色，`metrics` 指标，`templates` 合法任务示例，`extensions` 扩展接口。无需文件读取工具。
3. 选择任务模板：固定结构曲线 `fixed`；工艺公差 `fixed+tolerances_nm`；筛选结构 `search+search_axes+objective`；SPDC PMF 或 SFG 波长热图 `spectral_2d`。
4. 保留用户明确参数；缺少材料、切向、包层、厚度、刻蚀深度或顶宽且不能由上下文确定时才询问。区分用户给定值与默认假设。不自创加工范围。已有名义尺寸的搜索可用 null 边界表示 ±10% 探索。
5. 将用户明确要求且已支持的指标写入 `required_metrics`；超出的指标明确转入扩展流程，不能偷偷删掉用户要求。调用 `qpm_plan`，核对解释中的晶轴、模式、波长、指标缺口、预算、默认值。说明重要假设后，对明确授权的任务直接 `qpm_submit`，无需逐点确认。
6. 用后台任务工具等待，再 `qpm_status` 读取状态、成本和文件位置。失败点保留失败；分析结果前检查 validation、failures 和指标是否有效。需要重新绘图用 `qpm_postprocess`。
7. 输出数据来源、关键曲线、候选、预算消耗、未完成项和下一轮方向。预算内自主迭代，连续两轮无改善停止；插件预算上限不得自行提高。场数组不送给模型。演示数据必须标为合成数据。

## 不可混淆的物理约定

<!-- BEGIN GENERATED CRYSTAL -->
| 切向 | 薄膜法向（晶体） | 传播方向（晶体） | lab x/y/z对应晶轴 | 默认模式 | 默认主要电场 |
|---|---|---|---|---|---|
| x 切 | X | Y | Z/X/Y | TE0 | 晶体 Z |
| z 切 | Z | X | Y/Z/X | TM0 | 晶体 Z |

仿真 lab x 水平、lab y 竖直（膜法向）、lab z 传播。非默认传播方向不支持，不能仅改切向名称代替旋转材料模型。
<!-- END GENERATED CRYSTAL -->

TE0 是主要电场为仿真 lab Ex 的基模，TM0 主要为 lab Ey；均为准 TE/TM，不能认为全部场只有一个分量。默认非线性模型是主分量标量 d33 近似，并非完整张量求解。其他模式组合仍可计算线性指标和失配，但效率需要可靠系数/张量依据，不能编造 d_eff。

- 厚度是总膜厚；刻蚀深度不是剩余 slab；顶宽不是底宽。侧壁角从水平面量起，默认 67°。目前只支持非零 slab 的 rib。
- 内部 `pump` 一律指最高频波，SHG 中为倍频波；不一定是实验口头所称的输入泵浦。SHG 1550→775：`center_nm=1550`。
- 1D SPDC 775→1550：`center_nm=1550`，泵浦固定为 center/2，扫描信号，闲频由能量守恒计算。不能将 center 填成 775。
- SFG/DFG 一维曲线扫描 signal，必须给 `fixed_idler_nm`；高频波按能量守恒计算。
- 当前 SPDC 二维模板是 signal×idler 网格，泵浦逐格变化，采用名义简并点的固定光栅失配。它不是“固定775 nm泵浦的独立二维扫描”。明确要求固定泵浦二维 PMF 时报告缺口，不用现有模板冒充。
- GVD 目标波长是扫描轴，`objective.role` 决定实际求导的波。GVM 现有定义为有符号 pump−signal；signal−idler GVM 是新增后处理指标。
- 展示网格与求导点不同：默认中心 ±50 nm、20 nm 步长且加入中心；GVD 默认三点（λ±2 nm与中心），少量孤立异常限额升级五/七点，也可明确指定，禁止九点；局部 2 nm 间距，GVM 低频±2 nm/高频±1 nm，厚度导数±2 nm。
- β1 单位 ps/mm；β2/GVD 为 fs²/mm；D 为 ps/(nm·km)；GVM 为 ps/m；厚度敏感性为 d(delta_beta)/dt，m⁻¹/nm。
- 效率是忽略损耗、逐波长匹配的归一化系数与 L² 缩放系数，默认 L=5 mm。无功率信息不能称实际转换百分比。SPDC PMF 不是光子对产生率，当前不输出 SPDC 绝对效率。
- 公差 ±端点真实计算，与求导步长无关。周期误差表示所需匹配周期变化；效率误差固定名义光栅，不重新匹配掩盖退化。
- 结构搜索遵守合法加工格点；GP 只指导采点，结果来自实算。没有目标容差只能称“当前最接近/最小”。默认仅绘制最佳候选曲线，其他候选表格保存，不画结构热图/三维图。

## 小幅扩展工作流

已有功能只改任务配置。需要新指标、加权评价或工作流时先查 `qpm_conventions(topic="extensions")`。

- `adapter`：实现 `adapt_task(task:dict)->dict`，只组合/调整已有配置，输出仍通过物理校验和预算检查。
- `postprocess`：实现 `analyze_result(context:dict)->dict`，读取已导出的曲线、有效状态、单位、公差和候选数据。已有数据不足时报告缺口或先提交合法任务补数据，不能填造成功点。
- 使用 `qpm_stage_extension` 保存独立代码、测试和 metadata（kind、version、description、applicability）。它只做语法检查，不执行代码。
- 调用 `qpm_approve_version` 申请Harness原生审核并自动测试；旧手动令牌方式仍兼容；测试通过才可 `qpm_adapt_task` 或 `qpm_analyze_result(token,job_id)`。不得自行填写审核令牌。批准的 Python 是可信本地代码，不是操作系统沙箱。
- 输出新指标必须有单位、定义、有效状态和来源；缺失值 null，不能跨缺失段插值掩盖失败。新分析结果单独保存，保留原结果及其科学验证状态。
- 新材料模型、传播方向、张量或求解器改动不属于小幅扩展，报告能力缺口并安排独立开发验收。

具体模板与契约随包保存于 `templates.json`、`conventions.json`、`task.schema.json`；模型通过规范工具按需读取即可。程序测试通过、真实模型理解通过、真实 COMSOL 科学验收是不同状态。

## 低成本计算与续算

- 请求GVD时明确波长角色；只请求GVD默认objective.role（signal），不同时求解不需要的波段。
- 默认三点差分。少量五点检查使用dispersion_refinement_wavelengths_nm（展示波长）；整段五/七点须明确请求dispersion_stencil_points=5或7。不自动增加九点、半步长或加密网格；verify_numerics默认false。缺乏收敛验证不能宣称严格零点验证通过。
- 执行前qpm_plan或qpm_estimate读取必要点数、缓存与历史用时范围；无历史不编造小时数，不自行提高预算。
- 中断后先qpm_runs，再qpm_resume(run_id)；续算复用完成点，不占设计迭代轮次。预算为累计总预算，耗尽时请求用户允许增加，不能无限重启。临时进程异常预算内最多自动恢复两次，用户取消不自动恢复。
- 不将模式选择失败误当瞬时进程故障反复重跑。只有经过物理定义与版本校验的缓存可复用；缓存点不等于完整曲线或科学验收。


## 可保存版本、源码和绘图

需要核对实现时调用 `qpm_source` 查询实际安装源码目录，再按文件与行号读取。已有配置能力直接调用；独立扩展用 `qpm_stage_extension` 保存，用 `qpm_approve_version` 请求Harness原生审核，获批并测试成功后使用版本令牌。不得覆盖核心求解脚本。

工作区采用当前Harness会话选择的目录，项目名称包含切向、材料、总厚度、刻蚀深度、顶宽、侧壁角和包层。`qpm_runs`发现历史运行，`qpm_result`读取摘要；`qpm_plot_preset`保存并复用绘图预设。仅同单位曲线叠加，缺失点保持断开，演示数据标识不得删去。不同单位请分别绘图。

三点差分仍是默认。已有邻居提示孤立异常时，每条结构/波段曲线最多ceil(10%)且至少一个位置升级5/7点，每处最多4个新增逻辑波长点，受累计预算约束。零点、符号变化本身不触发；缺失点不能补成成功。五点与三点、七点与五点差异超过max(1fs²/mm,5%)时保留低置信标识，不能当成科学精度认证。

在预算内自主迭代；连续两轮无改善、目标达到或预算耗尽就停止。不得通过新运行重置同一设计预算；用户明确指定轮次时遵守。不要反复提交运行中的COMSOL任务，应等待状态或取消。

Python源码扩展用qpm_stage_source_version保存独立副本与测试，再qpm_approve_version审核。qpm_versions可查询或切换已测版本，select=packaged回退；不得修改已批准副本。非绘图功能修改将隔离模式缓存，科学验收仍待完成。


## 历史来源与跨会话使用（2026-10-08修复）
`qpm_runs`返回{runs,sources,diagnostics}；先检查扫描来源，使用runs中的完整run_id，不猜测目录。自动包含旧outputRoot和已有0.1/0.2发布results。必要时用qpm_history_sources(add=用户指定绝对路径)登记更多来源，不扫描磁盘。
历史run_id与后台job_id不同：跨会话用qpm_result(run_id)、qpm_postprocess(run_id)、qpm_plot_preset(run_id)或qpm_analyze_run。后台取消仍需当前会话job_id。历史来源仅需可读；新绘图与分析产物保存到当前工作区QPM/plots/comparisons，不要求旧运行目录可写。用户指定输出不可写时回退当前工作区并报告。
不同设计使用独立design_id；后续迭代qpm_submit必须带原design_id以继承预算。续算沿用原运行及原版本；原版本缺失时报告，不偷偷换实现。没有结果不等于没有缓存；结合readable/plottable/resume_available字段判断。


## 跨run绘图（2026-10-08）
先qpm_runs获取完整run_id。qpm_plot(sources=[{alias:"基准",run_id:...},{alias:"第二轮",run_id:...}],preset={series:[{source:"基准",metric:"signal_gvd_fs2_mm"},{source:"第二轮",metric:"signal_gvd_fs2_mm"}],formats:["png","svg","pdf"]},name="色散对比")。历史来源只读，输出当前工作区；不启动求解器，不占仿真预算。不同量纲选择layout="subplots"或"dual_axis"并指定axis="right"；二维扫描选择"heatmaps"。
script无需源码修改审核，仅支持Python形状的plot.option(title="标题",...)和plot.series(source="基准",metric="...",...)字面量调用；不支持import、循环、任意函数或文件访问。scale、normalize、x_range、interpolate_nm、smooth_window可明确控制数值展示，缺失点不会跨段插值。普通绘图无需申请修改求解源码。完整说明见[plotting.md](plotting.md)。
