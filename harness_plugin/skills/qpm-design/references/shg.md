# SHG 任务解释

1560→780 nm 的 center_nm=1560，高频角色pump是倍频输出。modes.signal/idler/pump对应旧SHG的fundamental1/fundamental2/harmonic。两基频偏振不同为TypeⅡ；三波相同为Type0；两基频相同而高频不同为TypeⅠ。

FORWARD在两种波序中均为(+,+,+)。phase_matching_direction=BACKWARD反转低频2，统一高频/低频1/低频2波序的符号为(+,+,-)，旧SHG低频1/低频2/高频波序为(+,-,+)。propagation_axis是晶体中的轴，不能代替反向符号。

查询实际metric_availability。原脚本新增功能不意味着统一接口已完整接入。TypeⅡ周期和线性指标已接入；混偏振矢量张量效率目前尚未完整桥接，不得编造系数或套d33。

x切Y传播，lab x/y/z=Z/X/Y；z切X传播，lab x/y/z=Y/Z/X。默认模式TE0/TM0。z切桥接与真实验收分别报告。
