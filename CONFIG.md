# 配置与迁移

完整默认例子见 `config.example.json`。首次运行读取默认配置，保存后生成本机 `config.json`。旧配置自动迁移，保留已有参数；旧 `anki_path` 转入默认 Anki 目标。无效配置不会被覆盖。

| 字段 | 含义 |
|---|---|
| `mode` | `study` 学习；`entertainment` 娱乐 |
| `study_target` | 学习目标的唯一 `id` |
| `entertainment_site` | `bilibili` 或 `douyin` |
| `death_penalty_enabled` | 默认 `false`，仅娱乐模式生效 |
| `death_switch_delay` | 死亡后切出延迟，默认 3 秒，0–120 秒 |
| `return_before_seconds` | 复活前提前返回，默认 1 秒，0–30 秒 |
| `persistent_target_topmost` | 全局置顶，与目标自身 `pin` 同时开启才持续置顶 |
| `gray_threshold` | 灰色比例门槛，默认 0.82 |
| `confirmation_frames` | 连续有效灰屏次数，默认 2 |
| `game_display_mode` | `borderless`、`fullscreen` 或保守兼容的 `auto` |
| `player_id` | 可选完整本人标识；空值时严格匹配本人，不猜测队友 |

旧 `persistent_anki_topmost` 保留为兼容字段并与新全局置顶同步。旧的关闭 Anki 选项不再控制生命周期；所有目标结束对局均保留。

模式、目标和惩罚开关保存后立即切换并取消旧任务；显示方式立即生效；公共计时、灰屏阈值及全局置顶参数需要暂停并恢复后生效。

## 桌面软件模板

优先使用“自动发现桌面软件”；未发现时用文件选择器。启动命令为参数数组，不经命令行外壳执行。下面的尖括号是待替换内容，**不能原样保存**，不代表任何品牌软件的实际路径：

```json
{
  "id": "my-study-app",
  "name": "你选择的学习软件",
  "adapter": "desktop",
  "command": ["<文件选择器产生的实际程序绝对路径>"],
  "process_path": "<实际主窗口所属程序的完整路径>",
  "title_contains": "<用于区分主窗口的标题片段，可留空>",
  "window_class": "",
  "pin": true
}
```

有启动器时，实际进程路径可能不同于启动文件。共享 Python、Java 等解释器时还必须提供窗口标题或类名条件。多个窗口不能唯一匹配时不操作；原来锁定的窗口关闭后不自动改选其他窗口，需手动重新打开或细化条件。

## 官方网页示例

```json
{
  "id": "bbdc-web",
  "name": "不背单词官方网页工具",
  "adapter": "web",
  "url": "https://bbdc.cn/lexis/login",
  "pin": true
}
```

只接受不含账号密码的 HTTPS 地址。不背单词此入口可用于官方生词、自制词书工具，不保证等同手机端全部背词功能。百词斩未核实到官方 Windows 客户端或完整学习网页版，未预置虚构入口。

## 本机运行状态

- `runtime/browsers/`：专用浏览器配置、进程身份和本工具页面标识。浏览器自身可能保存用户在专用窗口中的登录状态，不要分享此目录。
- `runtime/penalty.json`：本地随机盐、身份摘要、进程摘要、对局时间、死亡数和触发状态，不保存明文玩家名。
- `runtime.log`：必要状态和返回诊断，不记录画面、卡片、凭据或完整浏览历史。

删除状态文件、切换运行目录会影响同局重启去重。需要迁移时，先退出工具，再迁移自己的 `runtime/penalty.json`；不要复制他人的运行状态。配置、浏览器资料和本机状态均不应上传仓库。
