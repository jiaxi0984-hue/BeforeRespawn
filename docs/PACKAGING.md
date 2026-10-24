# BeforeRespawn · Windows 便携程序构建与验收

## 三种包，不要混淆

| 类型 | 启动 | 是否需要系统 Python |
|---|---|---|
| Windows x64 便携版 | 完整解压后双击 `BeforeRespawn.exe` | 不需要 |
| 源码包 | `install.cmd` 安装依赖，再运行 `start.cmd` | 需要 Python 3.14 x64 |
| 含 `_deps` 的 Python 运行包 | `start.cmd` | 仍需 Python 3.14 x64 |

便携版支持 Windows 10/11 x64，采用完整文件夹方式，不是单文件 EXE。必须保留 `_internal`、`assets`、内置目录和许可文件。网页模式仍需用户安装 Edge 或 Chrome；Anki 等学习软件由用户自行安装。

解压到可写的独立目录，不要直接在压缩包内运行。配置、日志和专用浏览器资料保存在 EXE 旁。升级前退出旧工具，备份并单独迁移自己的 `config.json` 和 `runtime/`；不要分享专用浏览器资料，也不要将新版直接覆盖解压到个人资料目录。

默认自动监测，可用 `BeforeRespawn.exe --paused` 暂停启动。`BeforeRespawn.exe --admin` 由用户主动请求 Windows 授权，授权后保持暂停；不强制管理员常驻。直接右键“以管理员身份运行”而不带参数时仍按默认自动监测。

## 构建

在 Windows 和 Python 3.14 x64 环境中，从源码根目录运行：

```powershell
python -m venv .build-env
& .\.build-env\Scripts\python.exe -m pip install -r requirements-build.txt
# 如果已有 install.cmd 安装好的 _deps，可以跳过这一行。
& .\.build-env\Scripts\python.exe -m pip install --target _deps -r requirements.txt
& .\.build-env\Scripts\python.exe -B tools/build_exe.py --output ..\outputs\BeforeRespawn-Windows-x64-portable.zip
```

构建依赖锁定：PyInstaller 6.22.3、hooks 2026.9；运行依赖锁定见 `requirements.txt`。源码包包含 `build_config/windows.spec`、`requirements-build.txt`、构建与验包脚本。没有 Git 时仍可构建，构建清单中的 Git 元数据为 `null`；有 Git 时只记录本仓库基准和工作区是否修改，不冒用父目录仓库。

构建使用独立临时目录；工作台中优先使用 `PI_SCRATCH_DIR`，其他环境使用系统临时目录。默认构建正常无控制台版本，不沿用诊断用控制台环境变量。需要保留诊断构建时可单独运行 PyInstaller spec，但不要把诊断包当成正式窗口版。

已有本工具 PyInstaller 输出目录时可使用：

```powershell
& .\.build-env\Scripts\python.exe -B tools/build_exe.py --existing-dist <新构建的BeforeRespawn目录> --output ..\outputs\新文件名-portable.zip
```

脚本拒绝覆盖已有 ZIP、校验文件或收集目录，拒绝混入个人配置、运行资料、测试和历史归档；Windows 文件名大小写差异不能绕过检查。资源和第三方许可随包收集。自检在副本上进行，不将测试日志写入交付目录。

## 强制启动验收与独立验包

出包前必须执行 `--check`，确认冻结模式、5 页界面、资源、有效画面采样以及数据根目录跟随 EXE。新包还必须报告 `application_name=BeforeRespawn` 和含新品牌的窗口标题。任一失败均不生成交付 ZIP，不能仅凭 ZIP 文件存在或 `.sha256` 一致就声称程序可运行。

```powershell
python -B tools/verify_exe.py ..\outputs\BeforeRespawn-Windows-x64-portable.zip --report ..\outputs\portable-verification.json
python -B -m unittest discover -s tests -q
```

独立验包工具检查 ZIP 路径、完整性、文件清单与全部摘要，再在新的中文 / 空格目录解压副本，以无源码工作目录、移除外部 Python 与 Qt 环境变量、仅保留 Windows 系统 PATH 的环境启动。报告文件已存在时拒绝覆盖。

包内 `BUILD-MANIFEST.json` 保存逐文件 SHA-256，`VALIDATION.json` 保存强制自检结果，ZIP 旁附 `.zip.sha256`。`--check` 不监测游戏、不启动目标、不下载内容。

当前构建统一使用 `BeforeRespawn/BeforeRespawn.exe`。验包工具仍兼容历史 `LOL-Words/LOL-Words.exe`，但拒绝同时包含两个品牌 EXE、两个顶层目录或目录与 EXE 混名。已发布的 `v2026.10.24` 及旧附件不改名、不覆盖；旧包兼容验收不等于新品牌构建验收。内部 `lol_words` 包、兼容环境变量和配置存储位置不变。

## 2026-10-24 英文名称统一验证

- 英文品牌改为 **BeforeRespawn**，中文名称、五页导航、内部 Python 包名和配置存储位置不变。
- **306 项自动化回归通过，0 失败、0 错误、0 跳过**；包含新品牌标识必需、历史包兼容、双 EXE 和混名 ZIP 拒绝测试。
- Windows Qt 渲染检查生成 11 张截图（含小窗口检查），更新文档中已有的 10 张截图；使用隔离配置，没有启动实际学习软件或网站。
- 从更新后的 spec 重新构建 `BeforeRespawn-Windows-x64-20261024-portable.zip`，不是将旧 EXE 改名。出包自检及重新解压验包均通过，应用名为 `BeforeRespawn`，窗口标题为 `泉水时间管理 · BeforeRespawn`，保留 5 页界面，DXGI 有效采样 20,736 字节。
- 独立验收覆盖中文 / 空格目录、无源码工作目录、移除外部 Python / Qt 环境变量、ZIP 清单及逐文件 SHA-256；历史 2026-10-23 便携 ZIP 也通过新版验包工具，摘要保持不变。
- 本轮改名检查没有操作真实游戏、实际 Anki 或娱乐网站，也不是全新无 Python Windows 虚拟机测试；下节中的交互及替身测试属于改名前版本的既有证据。

## 已解决的冻结问题

- QtWidgets 导入报 Windows 127：构建曾从开发机 PATH 中收集第三方 Poppler 的 `icuuc.dll`，但 Qt 6.12 需要 Windows ICU 的无版本后缀导出。完整导出表核对确认缺少 `ucnv_open` 等符号；临时副本只移除错误 ICU 后，自检由失败变为成功。构建现限制 DLL 搜索 PATH，并不携带应由 Windows 提供的 ICU DLL，不靠降级 Qt 或批量安装可选依赖规避。
- 冻结后的数据目录跟随 EXE，不注入源码 `_deps`；管理员重启同一 EXE，不依赖源码启动器或外部 Python。
- 外部学习软件、浏览器和只读软件发现子进程通过统一启动入口创建：在短暂的 CreateProcess 阶段串行清理 DLL 目录并立即恢复；等待不持锁，异常也恢复，不全局修改 PATH。子进程环境去除包内 Qt 路径，保留其他用户环境。
- 日志复制确认真实剪贴板写入结果，暂时占用时有界异步重试，不再在失败时显示“已复制”。

## 2026-10-23 本机验证及边界

- **297 项自动化回归通过**，没有跳过日志复制测试；日志复制文件另有 40 项及多轮重复验证。
- 正常无控制台 EXE 成功构建；新 ZIP 出包自检和重新解压隔离验包通过，中文 / 空格路径、外部 Python PATH 清理、资源和 DXGI 采样均通过。
- Windows UI Automation 操作新 EXE 的真实按钮：暂停启动、继续、暂停、正常关闭均通过，退出码 0；检查时没有游戏进程。
- 新 EXE 在独立测试配置下启动桌面学习替身，重复打开复用同一进程，子进程 DLL 目录为空，工具退出后目标保留。替身使用外部 Python 作为测试目标，不代表工具依赖外部 Python，也不冒充真实 Anki 实测。
- 现有浏览器适配器在模拟冻结 DLL 环境下实际启动独立 Edge 应用窗口；本地测试页连续读取 3 次并复用进程和页面，父进程 DLL 目录正确恢复。未访问个人浏览器资料；这不是新 EXE 的真实娱乐站点或媒体播放验收。
- 不覆盖源码中的个人配置和运行资料，产物不包含这些资料；本轮没有实际下载或运行安装程序。

这不是卸载了 Python 的全新 Windows 虚拟机测试，而是本机进程环境隔离验收。仍需用户配合验证真实全屏 / 无边框对局、提前手动切屏、连续右键、死歌 / 大嘴死亡后操作、短时间复活、接口中断、连续多局，以及实际 Anki 和新 EXE 娱乐站点的登录 / 视频行为。

旧的 2026-10-22 失败 EXE 压缩包不能作为可用版本；交付以本次新便携 ZIP 和对应验包报告为准。
