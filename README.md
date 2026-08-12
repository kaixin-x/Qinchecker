# QinChecker

秦岭植物 FOC 核对桌面程序。它读取 Excel 的指定批次，访问 iPlant 的“中国植物志（修订版，FOC）”与区县分布信息，生成可逐字段复核的修改建议；原始 Excel 永不覆盖。

## 给使用者

使用发布目录中的 `QinChecker.exe`：

1. 选择待核对的 `.xlsx` 文件，设置起始行和处理条数。
2. 等待程序抓取 iPlant 数据；左侧目录颜色会提示待复核项目。
3. 在中间表格逐字段选择“接受来源”“保留原值”或“手动编辑”。右侧可打开每条建议的原始网址。
4. 导出新的 Excel 和同名 TXT。主表不会新增网址列；网址、证据和待复核项位于“核对日志”工作表与 TXT。

程序需要网络访问 iPlant。基础绿色版会使用电脑已有的 Chrome 或 Edge；完整绿色版会额外包含 Chromium，因此无需用户安装浏览器。

缓存和人工复核决定保存在 `%LOCALAPPDATA%\QinChecker\sessions`，不会写入程序安装目录，也不会修改输入 Excel。

## 给开发者

需要 Python 3.11+。安装依赖：

```powershell
python -m pip install ".[build]"
```

运行测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

构建基础绿色版（目标电脑需有 Chrome 或 Edge）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1 -SkipBrowser
```

构建完整绿色版（下载并内置 Playwright Chromium）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1 -RefreshBrowser
```

构建结果位于 `dist\QinChecker\QinChecker.exe`。Excel 读写使用公开 Python 依赖，不需要 Node、Codex 或私有运行时。
