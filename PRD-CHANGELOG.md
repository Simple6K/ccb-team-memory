# PRD 变更记录

## 变更 V4.4 — 五层模块化架构重构

**日期**: 2026-04-29
**版本**: V4.3.2 → V4.4
**变更类型**: 架构重构 — 扁平模块 → 五层子包架构

### 背景

当前 8 个 `.py` 文件平铺在 `src/team_memory/` 下，`config.py` (513行) 职责过多，`cli.py` (457行) 混合参数解析和业务编排，`prompts/` 和 `skills/` 游离在 `src/` 外导致打包不可靠。

### 核心变更

将 8 个扁平模块重构为 **5 层子包架构**：

```
src/team_memory/
├── cli/          # 接口层：argparse 定义（6 文件）
├── services/     # 业务层：领域逻辑（5 文件）
├── config/       # 配置层：数据模型 + YAML + 项目发现（2 文件）
├── utils/        # 工具层：git subprocess 封装（1 文件）
└── templates/    # 资源层：静态 .md 模板（2 文件）
```

**具体变更**：

- `config.py` (513行) → `config/settings.py` + `config/annto.py`：数据模型与 YAML 解析分离
- `cli.py` (457行) → `cli/main.py` + `cli/init.py` + `cli/sync.py` + `cli/extract.py` + `cli/load.py` + `cli/install.py`：每个命令组独立文件
- `git_ops.py` → `utils/git.py`：提升为独立工具层
- `prompts/extract.md` + `skills/SKILL.md` → `templates/`：模板资源内聚到包内，`pyproject.toml` 声明 `package-data` 确保打包可靠
- `_ensure_rules_wrapper()` 从 `cli.py` 移入 `services/installer.py`
- 新增 `__main__.py`：支持 `python -m team_memory`
- `config/__init__.py` 重导出：`from team_memory.config import TeamMemoryConfig, find_project_root` 一个 import 搞定

### 设计原则

**单向依赖**：cli → services → {config, utils}。config 和 utils 不依赖上层。templates 无代码依赖。

### 影响范围

| 模块 | 变更 |
|------|------|
| `cli/` | 新增子包，6 文件（原 `cli.py` 拆分） |
| `services/` | 新增子包，5 文件（原扁平模块移入） |
| `config/` | 新增子包，2 文件（原 `config.py` 拆分） |
| `utils/` | 新增子包，1 文件（原 `git_ops.py` 移入） |
| `templates/` | 新增子包，2 文件（原 `prompts/` + `skills/` 移入） |
| `pyproject.toml` | 新增 `[tool.setuptools.package-data]`；entry_points 路径更新 |

### 不涉及

- CLI 命令签名与行为
- 配置格式（`ccb-annto-memory.yaml`、`settings.json`）
- 测试逻辑（仅调整 import 路径）
- 零外部依赖约束

---

## 变更 V4.3.2 — 记忆可追溯性：提取时间 + 提交人

**日期**: 2026-04-29
**版本**: V4.3.1 → V4.3.2
**变更类型**: 功能增强 — 记忆文件新增审计字段

### 背景

记忆文件创建后无法追溯"谁在什么时候提取的"，不利于审计和知识生命周期管理。

### 核心变更

- `extract.py` — `FILE_FORMAT` 模板新增 `extracted_at`（ISO 8601 时间戳）和 `contributor`（提取人）字段
- `extract.py` — 新增 `strip_metadata_fields()`，加载时自动剥离这两个字段，不占用模型上下文
- `extract.py` — `generate_auto_load_summary()` 加载时调用 `strip_metadata_fields()`
- `loader.py` — `manual_load()` 加载时调用 `strip_metadata_fields()`
- `extract.py` — `HOW_TO_SAVE` 索引格式不体现提取人和时间
- `PRD.md` — §2.3 文件格式新增字段说明表；§3.2.1 补充剥离步骤

### 设计原则

**存储 = 追溯，加载 = 精简**。`extracted_at` 和 `contributor` 写入 frontmatter 用于 git 审计，但注入模型上下文前由 loader 过滤，不浪费 token。

### 影响范围

| 模块 | 变更 |
|------|------|
| `extract.py` | `FILE_FORMAT` + `HOW_TO_SAVE` 模板；新增 `strip_metadata_fields()`；`generate_auto_load_summary()` 调用剥离 |
| `loader.py` | `manual_load()` 调用 `strip_metadata_fields()` |

### 不涉及

- config / sync / scanner / installer / cli
- 现有记忆文件（向后兼容，旧文件缺少这两个字段不影响加载）

---

## 变更 V4.3.1 — Skill 全局安装 + 嵌入式模板中文化

**日期**: 2026-04-28
**版本**: V4.3 → V4.3.1
**变更类型**: 缺陷修复 — Skill 安装路径不全 + 回退模板遗漏

### 背景

V4.3 全面中文化时遗漏了 `installer.py` 中的嵌入式回退模板（`_get_embedded_skill_template()`），该模板在找不到外部 `skills/SKILL.md` 时使用。同时 `install_skill()` 仅安装到项目级 `.claude/skills/`，未安装到全局 `~/.ccb-dev/skills/`，导致非团队记忆项目无法加载 Skill。

### 核心变更

- `installer.py`：`install_skill()` 新增 `global_skill` 参数，`True` 时安装到 `~/.ccb-dev/skills/team-memory/`（或 `~/.claude/skills/`）
- `installer.py`：`install_all()` 传递 `global_skill=global_hooks`，全局安装时间时安装全局 Skill
- `installer.py`：`_get_embedded_skill_template()` 回退模板从英文改为中文
- `openclaw-boad/.claude/skills/team-memory/SKILL.md`：手动覆盖旧英文版为中文

### 影响范围

| 模块 | 变更 |
|------|------|
| `installer.py` | `install_skill()` 签名变更（新增 `global_skill` 参数）；`install_all()` 传参适配；回退模板中文化 |

### 不涉及

- hooks 注册逻辑
- config / sync / extract / loader / scanner

---

## 变更 V4.3 — 团队记忆全面中文化

**日期**: 2026-04-28
**版本**: V4.2 → V4.3
**变更类型**: 语言标准化 — 提示词、输出、记忆内容统一使用中文

### 背景

团队为中文团队，记忆内容本身已是中文，但系统生成的提取提示词、加载输出仍为英文，不一致。

### 核心变更

- `extract.py`：所有提示词模版 `MEMORY_TYPES_HELP`、`WHAT_NOT_TO_SAVE`、`FILE_FORMAT`、`HOW_TO_SAVE`、`EXTRACTION_INSTRUCTIONS` 翻译为中文
- `loader.py`：`auto_load()`、`manual_load()`、`list_memory_files()` 输出消息翻译为中文
- `cli.py`：已在上次修改中部分中文化，检查补全

### 不涉及

- 记忆文件 frontmatter 字段名（保持英文 key：name, description, type, scope, created）
- settings.json 键名

---

## 变更 V4.2 — 取消 git 限制 + 项目身份强校验

**日期**: 2026-04-28
**版本**: V4.1 → V4.2
**变更类型**: 架构变更 — 项目发现纯 YAML + push 身份校验

### 背景

V4.1 仍依赖 `git rev-parse` 识别项目根目录，且 push 没有项目身份校验。V4.2 彻底移除 git 依赖，项目发现纯 YAML 驱动，同时引入 `project.url` 身份校验确保 push 安全性。

### 核心变更

#### 1. `find_project_root()` 纯 YAML

不再调用 `git rev-parse`。向上查找 `ccb-annto-memory.yaml`，找到即返回 CWD 作为项目根目录。

#### 2. 新增 `project` YAML 段

```yaml
project:
  url: git@github.com:owner/repo.git   # push 必填
  name: my-project                     # 可选，覆盖项目名
```

#### 3. `verify_project_identity()` 校验函数

push 前强制校验 `project.url` 与本地 `git remote get-url origin` 100% 匹配：

| 条件 | push | pull/load |
|------|------|-----------|
| git + `project.url` 匹配 | 允许 | 允许 |
| git + `project.url` 不匹配 | **拒绝** | 允许（警告） |
| git + `project.url` 未设置 | **拒绝** | 允许（警告） |
| 非 git | **拒绝** | 允许（警告） |

#### 4. `get_project_name()` 三级优先级

1. YAML `project.name`
2. `git remote get-url origin` → `owner--repo`
3. 目录名（fallback）

#### 5. 新增 `get_git_remote_url()` 工具函数

获取并标准化本地 git remote URL（`removesuffix(".git")`）。

### 影响范围

| 模块 | 变更 |
|------|------|
| `config.py` | 新增 `ProjectIdentity`、`verify_project_identity()`、`get_git_remote_url()`；`find_project_root()` 纯 YAML；`load_annto_yaml()` 解析 `project` 段；`generate_annto_yaml()` 自动填入 `project.url` |
| `cli.py` | `cmd_push` 加身份校验拦截；`cmd_pull` 加身份警告；`cmd_init` 适配纯 YAML 发现 |
| `sync.py` | 修复 `_push_repo()` 中 `PushResult` 无 `files_changed` 的 bug |

### 不涉及

- settings.json 向后兼容
- hooks / 密钥扫描 / extract / loader / skill
- `.claude/rules/team-memory.md` 机制

---

## 变更 V4.1 — 引入 `ccb-annto-memory.yaml` 自动发现机制

**日期**: 2026-04-27
**版本**: V4.0 → V4.1
**变更类型**: 架构变更 — 配置发现机制重构

### 背景

当前 V4.0 中，团队记忆配置存储于 `<project>/.claude/settings.json` 的 `teamMemory` 段。这要求每个项目独立配置 `teamMemory.repo`，新成员克隆项目后也需要显式执行 `team-memory init`。

新的 `ccb-annto-memory.yaml` 机制将配置提升到项目**父目录**，实现：
1. **零配置启动** — 新成员克隆项目后，ccb 启动时自动发现并拉取团队记忆
2. **多项目共享** — 同一父目录下的多个项目可共享同一份配置
3. **团队/项目记忆分离** — 团队记忆和项目记忆可以使用不同的 Git 仓库和路径

### 核心变更

#### 1. 新增 `ccb-annto-memory.yaml` 配置文件

文件位置：**项目根目录的父目录**（即 `$(git rev-parse --show-toplevel)/../ccb-annto-memory.yaml`）

```yaml
# ccb-annto-memory.yaml
team_memory:
  repo: git@github.com:myorg/team-memories.git
  branch: main
  path: shared/              # 仓库内团队记忆目录

project_memory:
  repo: git@github.com:myorg/team-memories.git
  branch: main
  path: projects/openclaw-boad/  # 仓库内项目记忆目录
```

**字段说明**:

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `team_memory.repo` | 是 | — | 团队记忆 Git 仓库 URL |
| `team_memory.branch` | 否 | `main` | 团队记忆分支 |
| `team_memory.path` | 否 | `shared/` | 仓库内团队记忆目录路径 |
| `project_memory.repo` | 是 | — | 项目记忆 Git 仓库 URL |
| `project_memory.branch` | 否 | `main` | 项目记忆分支 |
| `project_memory.path` | 否 | `projects/<project_name>/` | 仓库内项目记忆目录路径 |

> **注**: `team_memory.repo` 和 `project_memory.repo` 可以指向同一个 Git 仓库（当前模式），也可以指向不同仓库（灵活模式）。

#### 2. 配置发现优先级（新）

```
SessionStart / 项目启动
  │
  ├─ 1. 检查 CLAUDE_CONFIG_DIR / ~/.ccb-dev/ / ~/.claude/ settings.json
  │      └─ 有 teamMemory 配置？ → 使用（向后兼容）
  │
  ├─ 2. 向上查找 ccb-annto-memory.yaml
  │      ├─ 项目根目录本身？
  │      ├─ 项目根目录的父目录？  ← 主要目标
  │      └─ 继续向上到 ~/ ？
  │      └─ 找到 → 解析 YAML，自动拉取团队+项目记忆
  │
  └─ 3. 都没有 → 静默跳过（不报错）
```

#### 3. 自动拉取流程

```
ccb 启动 → SessionStart Hook
  → team-memory pull --quiet && team-memory load auto
    → find_project_root()
    → 查找 ccb-annto-memory.yaml（父目录）
      → 找到：
        ├─ git clone/pull team_memory.repo → .claude/team-memory/shared/
        ├─ git clone/pull project_memory.repo → .claude/team-memory/projects/<name>/
        └─ 生成 MEMORY.md 入口点
      → 未找到：
        └─ 检查 settings.json 兼容配置 → 有则使用
```

#### 4. init 命令变更

`team-memory init` 不再需要 `--repo` 参数（改为可选），新增：

```bash
# 从 ccb-annto-memory.yaml 初始化
team-memory init

# 显式指定 repo（覆盖 YAML 或独立使用）
team-memory init --repo <url>

# 生成模板 ccb-annto-memory.yaml 到父目录
team-memory init --generate-yaml --team-repo <url> [--project-repo <url>]
```

#### 5. 向后兼容

- `.claude/settings.json` 中的 `teamMemory` 配置**继续支持**
- 如果同时存在 YAML 和 settings.json，YAML **优先级更高**
- 现有使用 `team-memory init --repo` 的项目无需改动

### 影响范围

| 模块 | 变更 |
|------|------|
| `config.py` | 新增 `find_annto_yaml()`、`load_annto_yaml()`、`AnntoMemoryConfig` 数据类 |
| `sync.py` | `do_init()` / `do_pull()` 支持从 AnntoConfig 分离 team/project 仓库 |
| `cli.py` | `init` 添加 `--generate-yaml`、`--team-repo`、`--project-repo` 参数；`pull`/`push` 适配新配置 |
| `installer.py` | 无变更（hooks 逻辑不变） |
| `loader.py` | `auto_load()` 适配可能的分离仓库路径 |

### 不涉及

- hooks 注册逻辑（仍写入 settings.json hooks 段）
- 密钥扫描逻辑
- 记忆提取 prompt 生成逻辑
- Skill 注册逻辑
- `.claude/rules/team-memory.md` 机制

### 验证计划

1. 在项目父目录放置 `ccb-annto-memory.yaml`
2. 删除 `.claude/settings.json` 中的 `teamMemory` 段
3. 运行 `team-memory pull` — 应自动发现 YAML 并拉取
4. 运行 `team-memory load auto` — 应生成 MEMORY.md
5. 验证 ccb-dev 启动时自动加载团队记忆
