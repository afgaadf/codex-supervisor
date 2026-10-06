# 第三方组件声明

管家的冻结版和 MSIX 包可能包含以下第三方组件。发布二进制前应随包保留对应许可证文本。

| 组件 | 用途 | 许可证/官方入口 |
|---|---|---|
| Python 3.12 | 运行时 | PSF License：https://docs.python.org/3/license.html |
| PySide6 / Qt for Python | GUI | LGPL/GPL/商业许可：https://doc.qt.io/qtforpython-6/licenses.html |
| PyInstaller | 冻结打包 | GPL with exception：https://pyinstaller.org/en/stable/license.html |

说明：本文件是发布工程清单，不构成法律意见。正式公开分发前，应由发布者确认 LGPL 合规方式，并随二进制包提供许可证文本和可替换/重链接所需说明。