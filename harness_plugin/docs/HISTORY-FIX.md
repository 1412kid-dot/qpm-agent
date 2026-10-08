# 0.2.0 历史结果修复

旧outputRoot、已有0.1.0/0.2.0的results与当前工作区均自动发现。不搬迁数据。额外目录可在设置historySources填写绝对路径JSON数组，或qpm_history_sources add登记。

先调用qpm_runs，返回runs、sources、diagnostics；使用完整run_id读取qpm_result、重绘qpm_postprocess、qpm_plot_preset或分析qpm_analyze_run。旧UUID唯一时仍兼容，重复时返回候选。run_id不是后台job_id。

新图表与分析保存到旧运行目录的独立编号子目录；不覆盖原图、result.json或缓存。目录/权限不可写时明确报错，不改保存位置。缺少结果但有缓存的运行会单独列出能力状态。

源版本损坏不阻止发现/读取历史；执行后处理仍需要可用的处理版本。原执行版本缺失不能自动续算，需恢复该版本。独立设计预算用design_id区分；同设计迭代必须携带原design_id。异常差分先复用三点已有场检查连续性，模式异常不增加高阶点。

安装仍使用Install-Qpm.cmd，UTF-8 BOM、PowerShell 5.1/7最终ZIP验收、配置备份及保留规则不变。更新同名0.2.0发布包；真实聊天和COMSOL科学验收待完成。
