# Qt 依赖许可

本项目动态加载未修改的 PySide6-Essentials 与 shiboken6 6.12.0。安装包元数据标注 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`，本项目原创代码的 MIT 许可不替代 Qt 组件的许可。

本目录的三个许可文本来自 Qt 官方源码镜像：

- https://github.com/qt/qtbase/tree/dev/LICENSES

运行包也保留原始 `.dist-info` 中的声明与许可文件。Qt 与绑定的源码、版权及组件说明见官方仓库：

- https://code.qt.io/cgit/qt/qtbase.git/
- https://code.qt.io/cgit/pyside/pyside-setup.git/
- https://doc.qt.io/qtforpython-6/licenses.html

依赖通过 `_deps` 内独立的动态库加载，未嵌入或修改 Qt 本身。
