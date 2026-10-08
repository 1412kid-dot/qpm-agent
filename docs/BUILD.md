# 开发与发布

需要 Python 3.12+、科学计算依赖、pytest、setuptools，以及 Node 24。完整本地验收还需要合法安装的 Windows Harness 0.2.0-rc.2 和 PowerShell 5.1/7。不需要 API Key，不运行真实 COMSOL。

```powershell
$env:QPM_HARNESS_ROOT = 'E:\Deepseek Harness'
$env:QPM_TEST_PYTHON = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$env:QPM_BUILD_NODE = 'E:\Deepseek Harness\resources\runtime\primary-runtime\dependencies\node\bin\node.exe'
& $env:QPM_BUILD_NODE .\harness_plugin\dev\extract-sdk.cjs
& $env:QPM_TEST_PYTHON .\harness_plugin\dev\release.py
```

SDK 提取仅用于本机测试，产物位于 `.fixtures`，不能提交或分发。发布入口生成规范、编译插件、运行 Python/插件/绘图测试、打包 wheel/tgz，检查 PowerShell BOM 和校验和，最后从最终 ZIP 对隔离的 desktop profile 执行安装验收。不会修改日常 desktop 会话或模型设置。

只需构建而没有完整 Windows 验收环境时，可加 `--build-only`。这种产物必须标为“仅构建，未通过安装验收”，不能复用旧版本验收记录。

下载文件位于 `harness_plugin/dist`。公开源码不包含用户的原项目哈希清单；私有开发树存在该清单时仍核对原文件。可选的独立脚本模式基线目录使用 `QPM_MODE_BASELINE_ROOT` 配置，缺省为源码旁的 `mode_baseline`；统一仿真后端不读取这项旧基线目录。

发版前重新构建；将同一次构建的 ZIP、tgz、wheel 和 SHA256 清单上传到同一个 GitHub Release。源代码与安装包版本均为 0.2.0，此次首发建议标为 prerelease。不要提交 `.fixtures`、`.venv`、缓存、结果、备份、安装登记或凭据。
