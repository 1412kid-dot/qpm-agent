# 研究闭环

先查询能力和规范，创建spec：task为已有QPM配置，goal为目标描述，constraints为明确min/max/step，budget为累计上限，knowledge={capabilities_checked:true,conventions_version:"qpm-design/1"}。

qpm_study_propose给出初扫或GP建议；选择候选用evaluate计算。动作返回后台job_id，使用原生jobs等待并用qpm_status读取。研究由独立study_id标识，跨会话不变。

每批后读取真实结果及诊断。正常时比较候选或验证最好结构；异常时检查模式证据、求导或网格。排除伪零点同样是研究进展。

追加计算带reason和evidence，evidence引用实际run_id，例如：

```json
{"kind":"verify","reason":"检查当前候选的网格和步长稳定性","evidence":["r0001"]}
```

refine可指定stencil_points=3/5/7；verify使用refined网格及半步长检查。中断先status/recover，再resume原run；用户取消后不自动重启。

finalize生成报告。达到目标要求容差和数值验证同时通过；独立科学验收另列。预算耗尽、无容差或候选不足时保留最好实算和缺口，不声称全局无解。
