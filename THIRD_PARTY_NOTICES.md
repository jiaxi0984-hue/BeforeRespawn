# 第三方依赖与许可

本仓库发布本项目源码，不包含下载后的 `_deps`、第三方二进制文件、Anki 或英雄联盟。执行安装脚本会从当前配置的 Python 包源获取以下依赖，依赖各自的许可证继续适用。

| 依赖 | 锁定版本 | 许可证 | 来源 |
|---|---|---|---|
| DXcam | 0.3.0 | MIT | https://github.com/ra1nty/DXcam |
| comtypes | 1.4.17 | MIT | https://github.com/enthought/comtypes |
| NumPy | 2.5.4 | BSD-3-Clause；分发包中的附带组件可能有其他声明 | https://numpy.org/ |

安装后的完整许可文件位于各自的 `.dist-info/licenses` 等目录。若自行重新分发第三方依赖或打包可执行文件，应随分发包保留相应版权声明和许可文本。

Python 和 tkinter 由用户自己的 Python 发行版提供，遵循相应发行版及组件许可。

本项目的 MIT 许可证仅覆盖本项目原创部分，不改变上述第三方组件及商标的权利。
