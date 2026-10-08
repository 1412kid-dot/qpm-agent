# 能力目录

机器可读版本：`capabilities.json`；任务入口：`qpm_simulation`。本目录的七个脚本均保留原有手动主入口，统一工作流优先使用新包。所有真实 COMSOL 能力均为“实现完成、实算待验证”。

| 文件/模块 | 已整理的功能 | 当前边界 |
|---|---|---|
| LN_LT_COMSOL_SHG_QPM_Automation.py | 共享二维建模、材料、模式筛选、6/20/40模追踪、预测、场提取、旧MAT导出；新增晶向参数 | 原 x 切行为为回归参考，z 切物理验收待做 |
| LN_LT_COMSOL_SFG_Period_Automation.py | SFG周期与扫描，复用共享builder | 保留原过程专用后处理 |
| LN_LT_COMSOL_SPDC_PMF_Automation.py | SPDC PMF与扫描，复用共享builder | PMF不包含绝对产生率 |
| LN_LT_COMSOL_Dispersion_Automation.py | 色散计算与原搜索流程，依赖改为本目录 | 数值依赖已补齐；新任务优先走统一后端 |
| LN_LT_COMSOL_GVM_Automation.py | 群指数/GVM、局部细分；本地后端，恢复20→40保底 | 保留旧指标单位；新包统一单位 |
| LN_LT_Nonlinear_Efficiency.py | 独立SHG/SFG/DFG公式、非线性区域掩膜与场积分 | 去除对旧代码位置/强制注入旧site-packages的依赖 |
| TH_sensitivity_COMSOL.py | 厚度相位失配敏感性、波长敏感性等旧功能 | 后端及输出根迁至本目录；其旧专用报告可继续使用 |
| dispersion_core.py | 原局部色散、有限段、Matérn GP及搜索数值函数 | 从原项目只读复制 |
| qpm_simulation.config/crystal/materials | 统一任务、16组合、晶轴与张量旋转、材料参考 | 任意传播方向未支持 |
| qpm_simulation.backends | demo / comsol / replay、严格缓存、成本和失败日志 | COMSOL仅显式执行时启动 |
| qpm_simulation.metrics/physics | 波长匹配、三色散、GVM、效率、公差、厚度指标 | 量子SPDC绝对效率/一般全矢量非线性未实现 |
| qpm_simulation.optimization | 合法初扫、GP低成本寻零/最小化、预算停止 | GP不是仿真结果；连续求根未启用 |
| qpm_simulation.workflow/reporting | 计划、执行、最佳候选复核、CSV/JSON/图 | 结构搜索默认仅最佳曲线，无结构热图 |
| qpm_simulation.environment | 只读环境检查 | 不试许可证、不安装软件 |

共享builder和新包统一模式求解路径已提取；旧脚本的过程专用例程保留用于手动使用和回归，未大幅重写所有历史后处理。对新需求，修改任务JSON优先于修改旧脚本。需要新增公式或求解功能时，应在共享模块扩展，并保留测试。

离线验收涵盖16组合、晶轴映射、解析β1/β2/D、GVM符号、缺失点、默认网格/长度、效率公式与旧脚本对照、公差真实端点/固定光栅、合法全组合初扫、连续区间细分、预算、缓存回放、七个导入、模式回退/预测/恢复、模拟MPh边界及原文件哈希。详细状态见 `validation_status.json`。
# QPM Agent 0.2.0 设计契约补充

Python新增`describe_conventions(topic,template)`、`task_schema()`以及CLI `conventions`/`schema`，均不启动求解器。任务可声明`propagation_axis`及`required_metrics`：x切只支持晶体Y传播、z切只支持晶体X传播；明确要求的未实现指标拒绝执行。`plan_task`增加晶轴、波长角色、指标适用性与参数来源解释。

Harness插件内置`qpm-design`原生Skill及五类任务模板。独立扩展支持经审核的任务适配和导出数据后处理，不开放新求解物理的自动修改。见[harness_plugin说明](harness_plugin/README.md)。

