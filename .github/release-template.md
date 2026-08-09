# Career Scout Release 说明模板

- Windows 安装包：`CareerScout-vX.Y.Z.exe`
- macOS 安装包：`CareerScout-vX.Y.Z.dmg`
- 校验值（SHA256）：`CareerScout-vX.Y.Z.exe.sha256`、`CareerScout-vX.Y.Z.dmg.sha256`
- 前置条件：Windows 10/11 或 macOS 11+；已安装 Chrome 或 Edge；Windows 建议安装 WebView2 运行时；macOS 首次打开按 Gatekeeper 指引处理
- 已知限制：Windows 单文件首次启动需要解压，等待时间较长；未签名 macOS 应用首次打开会提示；杀毒软件可能误报单文件产物
- 常见问题与排错：界面空白请检查 WebView2 并查看 `~/.career-scout/desktop.log`；登录失效请在浏览器账号中重新登录；数据目录为 `~/.career-scout`；更多指引见项目 README
