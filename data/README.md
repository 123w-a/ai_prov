# `data/` 目录的边界

这个目录里**混着两类语义完全不同的东西**。改动之前先看清自己动的是哪一类。

## 一、随仓库分发的资产（受版本控制，人人都有、内容一致）

| 文件 | 用途 |
| --- | --- |
| `china_food_components.csv` | 食材成分表，`domain/dish_nutrition.py` 与 `agent_tools/legacy.py` 直接读 |
| `nutrition_table.csv` | 营养素参考值表，同上 |
| `preferences.txt.example` | 用户偏好文件的样例（真实的 `preferences.txt` 是用户数据，见第二节） |

这三份是**源码资产**：进仓库、进 clone、应该进任何分发物。

## 二、本机运行时数据（用户的真实数据，已被 `.gitignore` 逐条忽略）

`fridge.json`（冰箱现有食材）、`meals.json`（逐餐记录）、`profile.json`（家庭成员与身体档案）、
`pantry_log.json`（补货流水）、`dish_assets.json`、`answer_feedback.json`、`taste_signals.json`、
`weekly_summary.json`、`memory_candidates.json`、`app.log`（日志写不出去时的兜底位置）
以及 `*.json.bak` 系列备份。

**这一类的纪律**：

- **绝不进发行包、绝不进截图、绝不进文档** —— 这里面是真实家庭成员的身体数据、
  孕期状态、忌口与逐餐饮食记录。
- 位置由 `infrastructure/paths.py` 的 `DATA_DIR` 唯一决定。`scripts/migrate_runtime_paths.py`
  会把散落在各包目录（`storage/data/`、`agent/data/`、`domain/data/` …）的副本**合并回**这里 ——
  方向是"合并进来"，不是"搬出去"，别用反了。
- 日志是**唯一例外**：默认写在项目之外的用户级目录
  （Windows 为 `%LOCALAPPDATA%\XiaoShanGuanJia\logs`），该目录写不进去时降级到这里。
  判定"能不能写"用的是**真写一次**，不是 `os.access` —— 详见 `infrastructure/paths.py`
  的 `_is_writable`（`os.access` 在 Windows 上不看 ACL，会选中实际写不进的目录，
  直到 `RotatingFileHandler` 构造时才抛 `PermissionError` 把启动链路带崩）。

## 三、为什么不分家

既然混，为什么不把第一类挪到 `resources/` 之类的地方？

因为这三份资产的**读取者**横跨 `domain/`、`agent_tools/`、`storage/` 与 `scripts/`，
搬一次要同时改若干条硬编码路径，收益只是"目录看起来更整齐"，风险是漏改一处导致
营养表读不到 —— 而那个失败只在真正做菜时才暴露。所以这里的取舍是：
**保持位置不动，把边界写在这份文件里**。

（另：仓库目前**没有**发行打包流程 —— `pyproject.toml` 只声明依赖，没有 `Dockerfile`、
`MANIFEST.in`、`build.py` 或 pyinstaller spec；`scripts/build_*.py` 全是知识库文档构建。
所以第二节那条纪律现在靠自觉，将来真做打包时必须回来在这里补一条排除规则。）
