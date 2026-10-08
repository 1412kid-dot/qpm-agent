# Windows安装入口与发布检查

## 本次错误与根因

“在此系统上禁止运行脚本 / PSSecurityException / UnauthorizedAccess”发生在安装代码执行之前。Windows PowerShell 5.1本机所有执行策略范围为Undefined，默认有效策略Restricted；PowerShell 7的配置不同，不能用7的Get-ExecutionPolicy推断5.1。当前脚本已有UTF-8 BOM，没有Zone.Identifier，所以本次不是中文编码或下载标记问题，也不是COMSOL、Python或API问题。

上一版验收命令显式指定Bypass，但交付示例直接执行ps1；测试入口和用户入口不一致。这是封装与验收遗漏。本版增加独立CMD入口，测试从Restricted父会话调用它，再核对父会话与永久策略不变。

本轮还复现了PowerShell7的PSModulePath通过Python/原生命令继承给5.1，导致5.1误加载7的模块而无法使用Get-ExecutionPolicy。CMD用setlocal清空子进程PSModulePath，让5.1按自身标准位置重新建立模块路径；父进程环境不改变。

此前问题属于不同层次：UTF-8无BOM在5.1误读中文；5.1传递原生命令的引号和编码不同；Harness的desktop禁止dump-config属于Harness接口限制；本次是Windows执行策略。不能用修正其中一种替代其他验收。

## 推荐命令

完整解压ZIP，退出Harness，在PowerShell运行：

```powershell
& 'E:\work\Python\Harness_QPM\QPM-Agent-0.2.0\Install-Qpm.cmd' -PythonExecutable 'E:\work\Python\Comosol\Period\.venv\Scripts\python.exe'
```

检查/卸载/恢复使用同目录Check-Qpm.cmd、Uninstall-Qpm.cmd、Repair-Qpm.cmd。CMD为ASCII/CRLF，不受ps1执行策略加载限制；使用绝对系统路径启动PowerShell5.1，指定-NoProfile及子进程ExecutionPolicy Bypass，传递参数并保留退出码。路径与中文均在最终ZIP验收。

不会修改CurrentUser或LocalMachine执行策略，不修改注册表，也不会清除下载标记。组策略MachinePolicy/UserPolicy优先于Process，若组织要求签名或禁止脚本，则启动器仍可能被拦截；按组织部署要求处理，不能宣称此入口绕过组织策略。

高级直接入口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'E:\work\Python\Harness_QPM\QPM-Agent-0.2.0\scripts\Install-Qpm.ps1' -PythonExecutable 'E:\work\Python\Comosol\Period\.venv\Scripts\python.exe'
```

## 以后每次发布的强制检查

`python dev/release.py`默认强制运行以下验收，失败返回非零状态；不会仅凭构建成功宣布包可安装。

- 统一生成四个CMD入口，ASCII/CRLF、相对脚本定位、退出码保留、无永久策略修改命令。
- 全部ps1为UTF-8 BOM；配置显式UTF-8；包校验和、tgz/wheel及编译文件一致。
- 从最终ZIP解压到中文/空格路径，分别用5.1和7的Restricted父会话调用CMD；验证原直接ps1确实被拦截，CMD安装可执行。
- 安装、检查、卸载、恢复、重装、错误退出、用户设置、旧预设、包损坏、依赖缺失、并发修改和失败恢复。
- 父会话仍Restricted，永久执行策略列表前后相同；仅操作隔离desktop，不改日常profile、模型凭据或原环境。
- 保存对应ZIP SHA256和验收摘要至dist/INSTALLER-ACCEPTANCE.json。开发用--build-only明确不代表可分发验收完成。

组策略强制环境、其他Windows版本及签名发布未实测，单独列为未验证。真实模型聊天与COMSOL科学验收仍是独立项目。

依据：[Microsoft执行策略说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies?view=powershell-5.1)、[Set-ExecutionPolicy](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/set-executionpolicy?view=powershell-7.6)。
