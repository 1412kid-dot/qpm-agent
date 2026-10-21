---
name: qpm-design
description: LN/LT 准相位匹配设计与自主仿真研究。用于任务解释、工具组合、模式与数值诊断、设计迭代和可追溯交付。
---

# QPM 仿真研究

先查询qpm_capabilities和相关qpm_conventions；目录安装成功不代表已经读懂能力。环境用qpm_environment检查，许可证与真实科学验收另列。

解释必须服从本次工具事实：solver_calls 与 cache_hits 分别报告；新增 GVD/GVM 等指标须重新估算，不能沿用仅周期的预算。SPDC 当前不支持 normalized_efficiency、process_coefficient 或绝对产生率。只有通用报错时，不判断失败波角色、弱束缚或具体几何原因；先指出缺少实际 run 的模式筛选证据。

## 按任务读取

- 验证范围与适用边界：读[validation.md](references/validation.md)，查询 qpm_capabilities.validation；仅对当前已实现工作流描述初步验收，不把能力范围外需求当作已支持。

- SHG、TypeⅡ和反向匹配：读[shg.md](references/shg.md)，或查询crystal/wavelengths/metrics。
- 依据结果自主迭代：读[research.md](references/research.md)，或查询research。
- 模式跳变、GVD异常、收敛：读[quality.md](references/quality.md)，或查询quality。
- 历史来源和跨run绘图：读[plotting.md](plotting.md)，先用qpm_runs获取真实run_id。
- 新的数据分析能力：查询extensions，沿用独立版本、审核与测试。

无文件读取工具时使用规范查询工具，不猜测shell命令。

## 执行方式

单次明确计算：qpm_plan → qpm_submit → 后台等待 → qpm_status。

开放设计目标：qpm_study_create建立持久研究；每批后读取qpm_study_result及qpm_study_diagnose，再选择qpm_study_step动作。qpm_study_propose是候选建议，预测不是实算。

追加计算记录简短reason及已记录run_id的evidence。保留同一study_id，预算和代码版本跨会话累计。不能通过新run重置预算。允许的检查与停止条件见research/quality。无需逐点确认已经授权的计算。

批次之间用一句话说明动作即可，完整解释留到交付。候选已落入目标容差时优先verify，不为增加候选数量消耗预算。后台结果已含状态和候选时不重复查询同一状态。

## 物理约定

内部pump/signal/idler是高频、低频1、低频2。SHG的center_nm是基频。FORWARD在新旧波序中均为(+,+,+)。BACKWARD反转低频2，其统一波序符号(+,+,-)对应旧SHG波序的(+,-,+)；不是改变晶体传播轴。

x切沿晶体Y传播，lab x/y/z=Z/X/Y，默认TE0；z切沿X传播，lab x/y/z=Y/Z/X，默认TM0。

TypeⅡ周期及线性指标已支持，矢量张量效率尚未完整接入统一服务；显式要求时报告缺口。PMF不等于光子对产生率，归一化效率不等于实际百分比。保留材料和非线性系数来源。

## 交付

缺必需参数时，只指出缺失字段并直接提问，结束本轮；不展开默认模式、晶轴或未请求指标。固定计算只交付用户请求指标的数值/单位、必要假设、demo或COMSOL来源、验证状态及可复现结果路径；无需重述整份规范。开放研究另交付目标、候选和失败、累计预算、关键决策与未完成项。仅nominal结果只能说当前最好；达到目标需要容差和验证。demo始终标为合成数据。

结果按用户请求的指标简洁汇报，数值、方向和单位以工具及持久结果为准。只有实际计算且可追溯的派生量才报数值；不在文字中临时手算斜率、外推敏感度或补充材料物理结论。研究建议可以基于已测趋势推断，但必须标为预测，提交实算后再形成结论。verify显式指定要验证的候选geometry，evidence只表示证据来源，不会选择验证结构。

结果交付与物理解释见 [physics-delivery](references/physics-delivery.md)。优先使用工具的 delivery 事实摘要；研究过程可用 qpm_process 读取。
