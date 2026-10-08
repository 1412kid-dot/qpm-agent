# QPM独立绘图接口

`qpm_plot`支持sources来源别名、preset配置、script受限脚本、name预设名称、output_dir可选输出。省略preset时读取保存预设；source可替换为新run。qpm_plot_preset旧run_id入口仍可使用。

每条series支持source、metric、x（默认wavelength_nm）、dataset（默认nominal）、label、color、linestyle、marker、axis（left/right）、kind（line/scatter）、unit、scale、normalize、x_range、interpolate_nm、smooth_window。扩展指标通过metric_units及metric_definitions注明单位与定义。

preset支持layout（overlay/subplots/dual_axis/heatmaps）、heatmap、formats（png/svg/pdf）、title、xlabel、ylabel、figsize、font、fontsize、legend、legend_location、xlim、ylim、xticks、yticks、xscale、yscale、dpi、errors（true或envelope）。默认不插值不平滑。缺失指标跳过并返回warnings。不同网格各用原点；显式插值不跨缺失段、不外推。

受限脚本示例：
```python
plot.option(title="两轮色散对比", layout="subplots", formats=["png", "pdf"])
plot.series(source="基准", metric="signal_gvd_fs2_mm", color="blue")
plot.series(source="第二轮", metric="signal_gvd_fs2_mm", color="red", x_range=[1500,1600])
```
脚本仅解释字面量调用；不执行Python exec/eval。数值筛选、缩放、归一化、分段插值和平滑通过series API完成。通用Python扩展仍需审核。

绘图独立限额：120秒、Windows工作集1GiB（250ms监控，超限终止）、30来源/曲线、12子图、单来源128MiB、单曲线100000点、最多3格式。工作集监控不是操作系统硬内存隔离。官方Windows沙盒不能提供完整读取/网络隔离；脚本解释器不开放这些API。输出策略和目录链接检查约束写入，临时目录和字体缓存单独设在当前工作区QPM/.qpm/p的短目录；进程从解释器目录启动，允许较长的图表输出路径。工作区本身过长时返回明确提示选择短工作区，不自动修改Windows策略。不依赖当前活动求解源码版本。

历史结果、原图和缓存不覆盖。保存数据哈希、每来源验证状态、脚本及预设；演示与实算混合显示各自标识。QPM全局只读或Windows ACL禁止读取仍需用户选择可访问目录，插件不能解除系统权限。
