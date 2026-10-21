# 兼容版本

| 组件 | 首个兼容目标 | 状态 |
|---|---|---|
| Windows桌面Harness | 0.2.0-rc.2 | 发布目标；使用本机随附SDK及桌面CLI接口 |
| 随附Node / pnpm | 24.18.1 / 11.7.0 | 插件预编译ESM；桌面自带运行时 |
| Cordis / Schemastery | 4.0.4 / 3.18.4 | 与兼容Harness SDK核对 |
| Harness工具/设置/后台任务 | 0.2.0-rc.2 | 面向本机随附接口 |
| Windows PowerShell | 5.1、7 | ZIP根目录CMD入口；安装回执绑定最终ZIP |
| Python | 3.12+（基线3.12.3） | 64位；需科学计算依赖 |
| QPM Agent | 0.3.0 | 正式版 |
| qpm-simulation Core | 0.1.0 | 作为插件ZIP运行依赖，不单独发布 |
| COMSOL / MPh / JPype | 6.4 / 1.3.1 / 1.5+ | 真实后端的本机基线；须有适用许可证 |
| 其他Harness版本、Linux、macOS | — | 未验证，不声明兼容 |

兼容性以发行包和隔离profile验收为准。静态配置验收不表示桌面GUI加载、真实模型聊天、任意语义任务或所有物理组合均通过。插件不捆绑COMSOL、许可证或模型API凭据。
