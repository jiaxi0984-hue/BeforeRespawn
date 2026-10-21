# 源码目录与开发入口

Python 源码集中在 `lol_words/` 包，按功能分组；项目说明集中在 `docs/`，根目录保留首页说明、许可证、启动脚本、配置示例和产品目录。

```text
LOL-Words-zhandoushuang/
├─ lol_words/
│  ├─ __main__.py          模块启动入口
│  ├─ application/         应用入口、服务、唯一监测编排
│  ├─ ui/                  Qt 界面、状态投影与历史界面回归入口
│  ├─ game/                本人状态、死亡流程、对局追踪、本地对局接口
│  ├─ targets/             桌面 / 浏览器适配、官网与软件发现
│  ├─ modes/               模式配置、切换与相关生命周期管理
│  ├─ lobby/               大厅连接与三项基本功能状态机
│  └─ system/              Windows 操作、采样、窗口层级、权限和存储
├─ launchers/start.pyw     双击启动的无控制台入口
├─ tests/                 回归与入口集成测试
├─ tools/                 只读检查、替身窗口检查、截图和打包脚本
├─ assets/                界面图标
├─ docs/                  配置、界面、大厅、目录、源码结构及许可说明
│  └─ screenshots/        当前界面截图
├─ licenses/              依赖许可证
├─ start.cmd              常规启动
├─ start-admin.cmd        用户主动管理员启动
├─ config.example.json    默认配置示例
└─ learning_catalog.json  学习产品目录
```

## 功能定位

| 目录 | 主要文件 | 职责 |
|---|---|---|
| `application/` | `app.py`、`application_service.py`、`monitoring.py` | 连接界面与业务，管理监测、任务和事件 |
| `ui/` | `desktop_ui.py`、`ui_state.py`、`legacy_app.py` | 六页 Qt 界面、真实状态展示；历史界面仅用于回归 |
| `game/` | `core.py`、`study_flow.py`、`game_api.py`、`automation.py` | 本人死亡与复活、画面确认、延迟和对局追踪 |
| `targets/` | `adapters.py`、`browser_target.py`、`catalog.py`、`software_catalog.py`、`discovery.py` | 目标适配、复用、官网查询与本机安装发现 |
| `modes/` | `mode_config.py`、`mode_runtime.py` 等 | 配置迁移与模式生命周期 |
| `lobby/` | `lcu_api.py`、`lobby_flow.py`、`lobby_runtime.py`、`lobby_config.py` | 大厅连接与三个待实测基本功能，不包含图像恢复 |
| `system/` | `native.py`、`capture.py`、`presentation.py`、`privileges.py`、`state_store.py`、`paths.py` | 系统操作、权限、原子存储及统一根目录 |

## 启动与验证

```powershell
# 在仓库根目录中运行
python -m lol_words
python -m lol_words --paused --lobby
python -m lol_words --check
python -B -m unittest discover -s tests -q
python tools/check_lobby_readonly.py
python tools/package_app.py --output ..\outputs\LOL-Words-source.zip
python tools/package_app.py --with-deps --output ..\outputs\LOL-Words-runtime.zip
```

Windows 用户继续双击 `start.cmd` 或 `start-admin.cmd`。原先根目录的 `app.pyw` 迁移为 `launchers/start.pyw`，需要使用双击入口时请更新旧快捷方式；`python app.py` 改为 `python -m lol_words`。

## 路径与升级兼容

- `system/paths.py` 根据安装位置确定项目根目录，不依赖当前工作目录。
- `config.json`、`runtime/`、`runtime.log`、`runtime-python.txt`、`_deps/` 保持原位置；不会随着模块移动重新创建一套配置。
- 图标、内置目录和示例配置仍从根目录读取。大厅仅通过本地接口读取与提交，不再包含图像识别辅助进程。
- 跨目录导入使用 `lol_words.<功能>.<模块>`，无需把各功能目录加入搜索路径，也不维护两套同名旧源码。
- 运行包包含完整包目录、启动器和辅助脚本；个人配置、浏览器资料、日志、头像定位和动作记录继续排除。
