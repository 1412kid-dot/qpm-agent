# 验收记录（2026-10-07）：QPM Agent 0.2.0

## Windows安装入口修正

已复现默认Windows PowerShell5.1的Restricted阻止直接加载ps1。上一版验收主动Bypass却文档直接调用ps1，属于入口覆盖遗漏。修正版增加四个根目录CMD，ASCII/CRLF，使用系统Windows PowerShell5.1的-NoProfile与子进程ExecutionPolicy Bypass；不修改永久策略。组策略优先级保留。

发布入口默认强制从最终ZIP，在Windows PowerShell5.1和7的Restricted父会话执行CMD安装/检查/卸载/恢复及原16类安装回归，并检查直接ps1被阻止、父会话及永久策略未变。验收结果绑定ZIP SHA256，记录在dist/INSTALLER-ACCEPTANCE.json；失败不会报告RELEASE VERIFIED。开发--build-only明确不算安装验收通过。本轮仅使用隔离desktop，真实模型/COMSOL状态未改变。

## 本版程序验收

- Python 62项测试通过：保留原47项，并验证生成规范/schema/五类模板一致性、晶轴和默认模式、明确指标不可静默丢弃、传播方向限制、后处理单位/缺失点/输出契约。
- 插件13项测试通过：原工具流程、原生Skill注册及官方skill工具加载、12项QPM工具结构化参数、审核/哈希/测试/扩展类型/会话隔离、后处理曲线、源结果不变及验证状态不可通过context修改而提升。
- 16个记录的原项目文件哈希保持不变；不修改原虚拟环境。
- 实际随附SDK的后台演示、取消、超时、启动错误诊断通过；SHG/公差、GVD搜索、SFG二维与SPDC二维worker/直接工作流数值一致性通过。真实COMSOL调用0。
- 发布包含原生Skill、Python生成的规范/schema/模板、metadata固定的postprocess示例、真实模型验收任务集；使用0.2.0版本的tgz与wheel，避免旧包缓存混淆。
- 新发布ZIP的Windows PowerShell 5.1/7隔离desktop安装验收通过，每种引擎覆盖16类情形，含中文/空格路径、BOM/无BOM配置、用户设置保留、旧完整预设Skill迁移、重装、卸载、损坏包与缺依赖拒绝、后置失败恢复、并发修改拒绝和重试。仅修改项目内隔离profile，未改日常desktop或模型凭据。
- 发布检查验证全部清单校验和、脚本BOM、tgz内Skill布局、wheel与Python源代码一致、src与lib编译内容一致。验收期间Electron的隔离profile安装使用正常权限测试进程，避免受限运行环境阻止realpath；没有启动桌面聊天、模型或COMSOL。

## 本版仍待验证

真实DeepSeek模型工具选择与聊天理解待用户按docs/模型验收.md执行；真实桌面配置页可视操作仍单列。原生SDK工具调用成功不能称模型理解验收通过。真实COMSOL x切对照和z切科学验收仍未执行。跨机器、其他Harness版本、非Windows及Isolated网络依赖安装未验证。任务不跨进程恢复；批准扩展是可信Python代码，不是OS沙箱；不开放任意求解器修改。

以下是0.1.0历史验收记录，旧版工具数和扩展范围不代表0.2.0。

# 历史验收记录（2026-10-04）

## desktop安装与封装复核修正

本次修正取消desktop的CLI dump-config检查，改用实际随附SDK只读解析/合成并校验QPM服务、预设和工具入口。Windows PowerShell 5.1及PowerShell 7从发布ZIP解压后，在名称确为desktop的隔离profile完成验收：初次安装、重复安装、卸载、重装、缺少installation.json、中文与空格路径、BOM/无BOM已有配置、用户设置保留、损坏包拒绝、缺失科学依赖、后置失败自动恢复及重试、并发编辑拒绝、无效配置拒绝。既有47项Python测试、9项插件测试及16个原文件哈希检查通过。静态成功不表示桌面实际加载、配置页面或真实模型聊天已验证，真实COMSOL仍未执行。

额外修复PowerShell 5.1传递Python引号参数、显式指定解释器时丢失私有模块目录、GUI Node进程输出等待、重装原子替换、peer依赖自动下载和环境变量恢复。所有5个ps1均UTF-8 BOM，Node辅助脚本不执行!!js，诊断不输出配置原文。开发验收报告位于.fixtures/installer-acceptance-report.json，不随发行包携带测试profile或结果。


## Windows PowerShell 5.1 编码修正

修复原发布包的三个安装相关脚本无 BOM 导致中文误读的问题。Check-Qpm.ps1、Install-Qpm.ps1、Uninstall-Qpm.ps1 统一采用 UTF-8 BOM；dev/release.py 在每次打包时自动确保该编码。已使用本机 Windows PowerShell 5.1.26100.9444 验证三个文件语法、安装/卸载预检查、只读环境检查，并在独立 qpm-test profile 完成真实安装、卸载和重新安装。测试执行策略仅在测试子进程内设置，不改变系统策略；没有修改日常 desktop profile。修正版覆盖原同名 ZIP、插件 tgz 与校验清单，版本号及安装命令保持不变。

## 已验证

- 既有Python功能47项pytest测试通过，16个记录的原项目文件哈希全数不变。
- 插件9项自动化测试通过：SDK导入/配置、10项原生工具注册、计划/后台演示/后处理、会话所有权、并发/轮数/预算上限、扩展语法检查、人工令牌/独立测试/哈希固定、启动前取消、格式错误拒绝；设置卡片挂载及带revision的持久化调用。
- 使用实际桌面版0.2.0-rc.2随附SDK、Node24.18.1、Cordis4.0.4、原生Windows子进程范围管理和后台任务服务，执行能力→计划→提交→等待→结果流程；取消、含初始化/绘图的超时、服务销毁清理均通过。无需模型API；脚本驱动工具调用，不称真实模型聊天。
- 隔离DSH_HOME的qpm-test profile：桌面随附dsh CLI安装tgz、配置合成、加载独立web服务、环境检查、卸载、重新安装通过。配置值重新加载保留、无重复qpm-runtime声明。未改daily desktop profile。
- Python worker与直接工作流的数值一致性：SHG固定结构/公差、GVD搜索最佳候选、SFG二维周期、SPDC二维PMF全部逐数据对象相等；曲线8图、二维任务1图。
- 只读环境检查返回solver_started=false、license_checked=false。全过程真实COMSOL调用0，未试许可证。
- wheel解压到插件私有runtime/python-core的Existing安装方式与doctor验证通过；不修改原解释器环境。

## 待验证和限制

- 真实模型聊天：用户先完成Harness API配置，再用快速入门示例验收。
- 配置页可视操作、逐项保存和重启恢复：原生卡片与服务接口已实现；隔离网页被首次API Key引导挡住，未填密钥，未声明这部分可视验收通过。CLI配置持久化/重载已验证。
- COMSOL科学验收：x切原结果对照、z切代表结构、真实网格与模式连续性仍待显式试算。
- Isolated模式的新虚拟环境网络安装未执行；Existing模式已验证。其他Harness版本和非Windows未验证。
- 安装pnpm有SDK peer warning；桌面运行时实际提供SDK，配置加载和原生试算通过。尚未做跨机器分发验收。
- 旧后台job_id不跨进程恢复；新版本可通过持久化run_id发现并续算历史任务，配置与缓存重新校验。扩展支持经审核的适配与后处理，不自动替换求解器。
- src是TypeScript兼容的JavaScript子集，构建预编译ESM，不宣称完成TS静态类型检查。

测试入口：tests/runtime.test.mjs、dev/native-smoke.mjs、dev/parity.py，以及父目录pytest/verify_original_hashes.py。发布包不包含SDK提取目录、测试环境、API凭据、原虚拟环境或科学结果。


## 2026-10-07 三点差分与续算更新

本节覆盖旧版九点求导/硬超时/不跨进程续算的描述。默认三点；五点显式选择；额外精度验证默认关闭。新缓存与日志逐点原子保存，跨任务复用经版本/物理/场数据验证。异常最多两次自动恢复，累计预算耗尽暂停。旧版没有完整检查点的失败任务成本可能不完整。

Python回归新增解析三/五点导数、预算续算、跨任务复用、损坏缓存拒绝、不可变任务与无隐藏加密；插件回归新增估算、跨会话历史续算、恢复轮次与预算暂停。最终测试结果与安装验收以本次生成的回执为准；不运行真实COMSOL，不配置模型凭据。


2026-10-08更新：71项Python测试、18项插件测试及本机Harness SDK演示、取消、预算暂停、历史预设绘图、实际源码读取已通过；真实模型审核UI/聊天与COMSOL科学验收待完成。Windows安装以dist/INSTALLER-ACCEPTANCE.json匹配最终ZIP哈希为准。


2026-10-08历史修复：73项Python、20项插件测试；实际9个旧结果只读发现/读取，隔离副本后处理、预设绘图及原版本续算通过，原结果哈希不变、COMSOL调用0次。最终Windows安装状态以dist/INSTALLER-ACCEPTANCE.json匹配ZIP为准。


## 跨轮次绘图验收（2026-10-08）
独立绘图入口、历史只读输入、当前工作区输出、源码版本故障隔离、受限脚本与三格式导出由dev/plot-acceptance.mjs和Python比较绘图测试验证。实际验收结果见dist/PLOT-ACCEPTANCE.json。真实模型自主选择工具与真实COMSOL科学精度不由这些测试证明。

## 首个公开版本与返回值修复（2026-10-08）

工具执行及渲染统一规范为合法 JSON；未赋值可选字段及非有限数值为 null，循环和非 JSON 值明确报错。新增三项序列化回归：插件总计23项通过，私有开发树Python82项通过；公开源码不附原项目哈希清单，对应一项本机审计跳过，81项通过。原项目16个文件另行核验不变。

Windows安装以同一次最终ZIP的INSTALLER-ACCEPTANCE.json为准，必须PS5.1/7均通过；重新打包后旧哈希回执不可复用。首次开源仍为预发布；真实模型聊天和COMSOL科学验收待完成。
