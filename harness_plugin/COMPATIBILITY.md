# 兼容版本

| 组件 | 首个兼容目标 | 状态 |
|---|---|---|
| Windows桌面Harness | 0.2.0-rc.2 | 本机随附SDK核对、原生子进程/任务演示验证 |
| 随附Node | 24.18.1 | 构建包为预编译ESM，Node>=24 |
| 随附pnpm | 11.7.0 | 使用桌面自带dsh插件安装命令 |
| Windows PowerShell | 5.1 | ps1 UTF-8 BOM；推荐ASCII/CRLF CMD入口，仅对子进程指定策略；发布强制Restricted父会话安装验收 |
| Cordis / Schemastery | 4.0.4 / 3.18.4 | 随附实际接口验证 |
| Harness工具/设置/任务服务 | 0.2.0-rc.2 | 采用本机接口，非只参照在线最新版 |
| Harness原生Skill | 0.2.0-rc.2 | skills.register及官方skill加载工具测试通过 |
| Python | 3.12.3 | 测试通过；需>=3.12 |
| qpm-simulation | 0.2.0 | 含设计契约、worker和后处理扩展入口 |
| COMSOL / MPh | 6.4 / 1.3.1 | 环境基线；真实试算未运行 |
| 其他Harness、Linux、macOS | 未验证 | 不声明兼容 |

官方规范：[发布与bundle](https://deepseek-harness.github.io/deepseek-harness/en/develop/basic/publish)、[工具](https://deepseek-harness.github.io/deepseek-harness/en/reference/cookbook/adding-a-tool)、[设置卡片](https://deepseek-harness.github.io/deepseek-harness/en/reference/cookbook/adding-a-settings-card)。插件以本机随附接口为准。

Windows PowerShell 7 同样通过发行ZIP中的隔离desktop验收。desktop只通过随附CLI执行plugin管理；配置检查使用官方只读SDK接口，不启动desktop profile。安装后仍需用户重启桌面验证实际加载。
