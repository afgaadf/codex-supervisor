# MSIX 打包说明

## 构建

先在仓库根目录构建两个 exe：

```powershell
python packaging\build.py
```

生成 `packaging\dist\Supervisor\Supervisor.exe`（窗口）和
`SupervisorButler.exe`（后台管家），两者共享一个 `_internal` 目录。

打包未签名 MSIX：

```powershell
python packaging\msix\build_msix.py
```

输出在 `packaging\msix\out\`。再加入正式代码签名证书：

```powershell
python packaging\msix\build_msix.py `
  --publisher "CN=你的证书主题" `
  --sign-pfx "C:\安全路径\你的证书.pfx" `
  --sign-password "<密码>" `
  --timestamp-url "http://timestamp.digicert.com"
```

`--publisher` 必须与证书主题一致。自签名证书只能做本地测试；公开下载需要
CA 代码签名证书（用户自行申请）。

## 本地测试

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\msix\make-test-cert.ps1
python packaging\msix\build_msix.py --sign-pfx packaging\msix\local-cert\CodexSupervisorDev.pfx --sign-password CodexSupervisorDev!
```

`make-test-cert.ps1` 会把测试证书导入当前用户的 `TrustedPeople`。测试完可在
`certmgr.msc` 里手动删除；它不改系统级证书。若系统未启用开发者模式/旁加载，
安装仍可能报 `0x80073CFF`；这是 Windows 的部署策略，不是包结构错误。

## 清单要点

- `Identity` 声明包身份：名称、发布者、版本。
- 窗口程序是默认启动器：`Supervisor\Supervisor.exe`。
- 后台管家通过 `uap5:Extension Category="windows.startupTask"` 声明式自启：
  `Supervisor\SupervisorButler.exe`，`TaskId=SupervisorButler`。
- 用户第一次启动应用后，启动任务才会注册；之后可在任务管理器“启动”页关闭。
  用户关闭后，应用不能自行重新开启（官方文档明确说明）。
- `rescap:runFullTrust` 是桌面全信任应用所需能力。

## 依据（T1 一手权威，访问 2026-10-07）

- Microsoft Learn《What is MSIX?》——包身份由 publisher + name + version 组成。
  https://learn.microsoft.com/en-us/windows/msix/overview
- Microsoft Learn《uap5:StartupTask》——元素结构和属性。
  https://learn.microsoft.com/en-us/uwp/schemas/appxpackage/uapmanifestschema/element-uap5-startuptask
- Microsoft Learn《Integrate your desktop app with Windows using packaging extensions》——
  startup task 的注册、任务管理器开关、以及不能程序化重新启用。
  https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/desktop-to-uwp-extensions
- Microsoft Learn《Create an app package with the MakeAppx.exe tool》。
  https://learn.microsoft.com/en-us/windows/msix/package/create-app-package-with-makeappx-tool
- Microsoft Learn《Sign an MSIX package》——MSIX 必须签名，生产分发需受信任证书。
  https://learn.microsoft.com/en-us/windows/msix/package/signing-package-overview
- Microsoft Learn《Understanding how packaged desktop apps run on Windows》——
  MSIX 按用户安装，安装目录默认在 `C:\Program Files\WindowsApps\<package_full_name>`。
  https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes

> 说明：本项目把「去重窗口 15 分钟」当作**项目约定（非权威）**；MSIX / 自启 /
> 签名相关的硬事实以上面 T1 文档为准。