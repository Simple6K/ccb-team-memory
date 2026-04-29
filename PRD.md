# 企业团队记忆功能 - 需求文档 V4.4

## 文档信息

| 字段 | 值 |
|------|-----|
| 产品名称 | ccb-team-memory |
| 目标版本 | 1.0.0 |
| 生成日期 | 2026-04-28 |
| Python | 3.13+ |
| 语言 | 中文（提示词、输出、记忆内容均使用中文） |

---

## 一、核心目标

在 ccb 基础上，以 Python 3.13+ 实现一个**完全独立于 ccb 原生自动记忆**的团队记忆系统，包含两大核心能力：

```
┌─────────────────────────────────────────────────────┐
│                 ccb-team-memory                      │
│                                                      │
│  核心 1：记忆提取（独立于 ccb 自动记忆）              │
│  ┌──────────────────────────────────────────────┐   │
│  │ 对话 → 分析 → 分类 → 写入团队/项目记忆         │   │
│  │  ├─ 团队记忆 (shared/)  跨项目共享知识         │   │
│  │  └─ 项目记忆 (projects/) 本项目专属知识        │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  核心 2：记忆同步（Git 驱动）                        │
│  ┌──────────────────────────────────────────────┐   │
│  │ 本地 .claude/team-memory/ ←→ 远程 Git 仓库     │   │
│  │  ├─ push：密钥扫描 → git commit → git push    │   │
│  │  └─ pull：git pull → 记忆立即可用              │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## 二、记忆模型

### 2.1 两层 scope

| scope | 路径 | 用途 | 示例 |
|-------|------|------|------|
| **团队** (team) | `shared/*.md` | 跨项目共享，所有项目成员可见 | "所有项目统一用 bun 而非 npm" |
| **项目** (project) | `projects/<name>/*.md` | 本项目专属 | "Auth 中间件重写因合规驱动" |

### 2.2 四种记忆类型

| 类型 | 默认 scope | 说明 |
|------|-----------|------|
| `user` | team（团队偏好） | 团队的角色、偏好、知识背景 |
| `feedback` | team > project | 工作流中的经验教训和纠正 |
| `project` | project > team | 架构决策、项目约束、里程碑 |
| `reference` | team | 外部资源指针（文档链接、系统地址） |

### 2.3 文件格式

```yaml
---
name: my-memory-name
description: 一句话描述，用于未来的相关性判断
type: user|feedback|project|reference
scope: team|project
created: 2026-04-27
extracted_at: 2026-04-27T15:30:00+08:00
contributor: 提取人姓名
---
```

| 字段 | 用途 | 加载时 |
|------|------|--------|
| `name` | 记忆标识 | 保留 |
| `description` | 相关性匹配 | 保留 |
| `type` | 四种类型之一 | 保留 |
| `scope` | team/project | 保留 |
| `created` | 知识产生日期 | 保留 |
| `extracted_at` | 提取时间（ISO 8601） | **剥离，不注入上下文** |
| `contributor` | 提取人 | **剥离，不注入上下文** |

`extracted_at` 和 `contributor` 存储在文件中用于审计追溯，但加载时由 loader 自动过滤，不占用模型上下文。

### 2.4 MEMORY.md 索引

每个目录一个 `MEMORY.md`，格式：
```markdown
- [filename.md](filename.md) — 一句话描述
```
按类型分组。提取后自动更新。

---

## 三、核心功能

### 3.1 记忆提取（`extract`）

**独立于 ccb 原生自动记忆**。Python CLI 生成提取 prompt → 注入给模型 → 模型执行提取并写入文件。

#### 3.1.1 提取作用域

| 模式 | 说明 | 扫描目录 |
|------|------|----------|
| `team` | 仅提取团队级记忆 | `shared/` |
| `project` | 仅提取项目级记忆 | `projects/<name>/` |
| `all`（默认） | 两者都提取 | `shared/` + `projects/<name>/` |

#### 3.1.2 提取触发模式

| 模式 | 触发方式 | 用户感知 |
|------|----------|----------|
| **manual** | 用户手动执行 `/team-memory extract` 或说"提取团队记忆" | 完全手动 |
| **instruction**（默认） | 注入提取指令到 ccb instructions.md | 半自动（用户需说提取关键词） |
| **auto** | Stop Hook 自动触发，每轮对话后提取 | 完全自动 |

#### 3.1.3 Auto 模式流程

```
对话轮次结束
  → Stop Hook 触发
    → team-memory extract prompt --mode auto
      → 扫描已有记忆文件（manifest）
      → 生成提取 prompt
      → 输出到 stdout → ccb 注入为 additionalContext
  → 下一轮对话开始
    → 模型看到 prompt + 用户消息
    → 模型分析对话，提取新记忆
    → 模型 Write 记忆文件到 .claude/team-memory/
  → PostToolUse Hook 触发
    → team-memory push（自动提交+推送）
```

#### 3.1.4 Instruction 模式流程

```
用户说："提取一下团队记忆"
  → Skill 触发 /team-memory extract
    → team-memory extract prompt --mode instruction
      → 生成针对性的提取 prompt
      → 模型看到当前对话 + 已有记忆 + 提取指令
    → 模型提取记忆并写入文件
  → 用户确认或自动 push
```

#### 3.1.5 提取 Prompt 内容

生成的 prompt 包含：
- 四种记忆类型定义 + scope 规则
- 不保存的内容清单（排除规则）
- 已有记忆 manifest（最新 N 个，防重复）
- 当前对话摘要上下文
- 写入指令（路径、格式、MEMORY.md 更新）

#### 3.1.6 排除规则（不提取的内容）

- 代码片段或项目源文件内容
- 会话特定的临时上下文
- 与 `.claude/CLAUDE.md` 重复或矛盾的信息
- 敏感数据（API 密钥、令牌、密码）
- 临时调试状态（断点、变量值）
- 会过时的 Git 分支名或 PR 号
- 可通过链接引用的文档原文

---

### 3.2 记忆加载（`load`）

#### 3.2.1 自动加载

**命令**：`team-memory load --auto`

在 SessionStart Hook 中触发。行为：

| 配置 | 行为 |
|------|------|
| `teamMemory.autoLoad: true`（默认） | 启动时自动 load，将团队记忆摘要注入 ccb 上下文 |
| `teamMemory.autoLoad: false` | 不自动加载 |

自动加载时：
1. 读取 MEMORY.md 索引
2. 生成团队记忆摘要（最多 200 行，按类型分组）
3. 剥离 `extracted_at` 和 `contributor` 字段，仅保留业务相关元数据
4. 通过 Skill 或系统提示词注入到模型上下文

#### 3.2.2 手动加载

**命令**：`/team-memory load [query]`

用户主动搜索和加载记忆：
- `/team-memory load` → 列出所有团队记忆摘要
- `/team-memory load 部署` → Grep 搜索包含"部署"的记忆并加载
- `/team-memory load --type project` → 仅加载 project 类型

---

### 3.3 记忆同步

#### 3.3.1 初始化（`init`）

**方式一：从 ccb-annto-memory.yaml 自动初始化（推荐）**

```bash
team-memory init
```

- 自动查找 `../ccb-annto-memory.yaml`
- 解析 YAML 获取 team/project memory repo 和 path
- `git clone` 团队记忆到 `.claude/team-memory/shared/`
- `git clone` 项目记忆到 `.claude/team-memory/projects/<name>/`（如与团队记忆不同 repo）
- 确保 `.gitignore` 包含 `.claude/team-memory/`
- 可选：`team-memory install` 注册 hooks

**方式二：显式指定 repo（向后兼容）**

```bash
team-memory init --repo git@github.com:org/team-memories.git
```

- 写入 `.claude/settings.json` → `teamMemory` 段
- `git clone --depth 1 <repo> .claude/team-memory/`
- 行为与 V4.0 相同

**方式三：生成 YAML 模板**

```bash
team-memory init --generate-yaml --team-repo <url> [--project-repo <url>]
```

- 在项目父目录创建 `ccb-annto-memory.yaml` 模板
- 后续其他项目可直接通过方式一初始化

#### 3.3.2 拉取（`pull`）

```bash
team-memory pull
```

#### 3.3.3 推送（`push`）

```bash
team-memory push [--force]
```

执行顺序：
1. **身份校验**（V4.2）：`verify_project_identity()` — `project.url` 必须与本地 git remote 100% 匹配，否则拒绝
2. **密钥扫描**：扫描待推送文件（`--force` 跳过）
3. **提交+推送**：自动提交消息，冲突重试 3 次

非 git 项目、`project.url` 未配置、URL 不匹配均拒绝 push。

#### 3.3.4 新成员自动检测（V4.2 更新）

```
ccb 启动 → SessionStart Hook
  → team-memory pull --quiet
    → find_project_root() — 向上查找 ccb-annto-memory.yaml（纯 YAML）
      → 找到：
        ├─ git clone/pull team_memory.repo → .claude/team-memory/
        ├─ git clone/pull project_memory.repo（如分离）
        ├─ verify_project_identity() → 校验 project.url（不匹配仅警告）
        └─ team-memory load auto → 生成 MEMORY.md
      → 未找到：
        └─ 检查 .claude/settings.json teamMemory.repo（向后兼容）
          → 存在 → 使用 legacy 模式
```

新成员无需任何手动配置，只要父目录有 `ccb-annto-memory.yaml`，ccb 启动即自动拉取团队记忆。

---

### 3.4 密钥扫描（`scan`）

```bash
team-memory scan
```

- 仅扫描 `.claude/team-memory/**/*.md`
- 移植 ccb `secretScanner.ts` 36 条规则
- 退出码：0 = 安全，1 = 检测到密钥

### 3.5 状态查看（`status`）

```bash
team-memory status
```

- 配置信息（repo URL、分支、项目名）
- 文件统计
- 同步状态（last pull/push）
- 提取状态（extract mode、上次提取时间）

### 3.6 安装到 ccb（`install`）

```bash
team-memory install
```

注册以下 hooks 到 `.claude/settings.json`：

| Hook 事件 | 动作 | 说明 |
|-----------|------|------|
| SessionStart | `team-memory pull && team-memory load --auto` | 拉取 + 自动加载 |
| PostToolUse | `team-memory push` | 写入后自动推送 |
| Stop（auto 模式） | `team-memory extract prompt --mode auto` | 每轮后提取 |
| SessionEnd | `team-memory push --flush` | 退出前 flush |

同时注册 Skill 到全局（`~/.ccb-dev/skills/team-memory/SKILL.md`）和项目级（`.claude/skills/team-memory/SKILL.md`），确保有/无项目配置时均可加载中文 Skill。

---

## 四、配置

### 4.1 ccb-annto-memory.yaml（推荐，V4.1 新增，V4.2 增强）

**文件位置**：项目根目录的**父目录**（`$(git rev-parse --show-toplevel)/../ccb-annto-memory.yaml`）

**项目发现**：`find_project_root()` 纯 YAML 驱动，不依赖 git。向上查找 `ccb-annto-memory.yaml`，找到即视为项目根目录。

此文件由人工创建，允许多个项目共享同一份配置，实现零配置启动。

```yaml
# ccb-annto-memory.yaml
team_memory:
  repo: git@github.com:myorg/team-memories.git
  branch: main          # 可选，默认 main
  path: shared/         # 可选，仓库内团队记忆目录，默认 shared/

project_memory:
  repo: git@github.com:myorg/team-memories.git
  branch: main          # 可选，默认 main
  path: projects/my-project/  # 可选，仓库内项目记忆目录

# 项目身份 — push 操作必填（V4.2 新增）
project:
  url: git@github.com:myorg/my-project.git
  name: my-project      # 可选，覆盖项目名
```

**字段说明**：

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `team_memory.repo` | 是 | — | 团队记忆 Git 仓库 URL |
| `team_memory.branch` | 否 | `main` | 分支 |
| `team_memory.path` | 否 | `shared/` | 仓库内团队记忆路径 |
| `project_memory.repo` | 是 | — | 项目记忆 Git 仓库 URL |
| `project_memory.branch` | 否 | `main` | 分支 |
| `project_memory.path` | 否 | `projects/<name>/` | 仓库内项目记忆路径 |
| `project.url` | push 必填 | — | 项目 git remote URL，用于 push 前身份校验 |
| `project.name` | 否 | 自动推导 | 覆盖项目名（优先级最高） |

> `team_memory.repo` 和 `project_memory.repo` 可指向同一仓库（当前模式），也可分离。

#### 4.1.1 Push 身份校验（V4.2）

push 前强制校验 `project.url` 与本地 `git remote get-url origin` 100% 匹配：

| 条件 | push | pull/load |
|------|------|-----------|
| git 项目 + `project.url` 匹配 | 允许 | 允许 |
| git 项目 + `project.url` 不匹配 | **拒绝** | 允许（警告） |
| git 项目 + `project.url` 未设置 | **拒绝** | 允许（警告） |
| 非 git 项目 | **拒绝** | 允许（警告） |

校验函数：`verify_project_identity(config, project_root) → (bool, message)`

### 4.2 配置发现优先级

```
find_project_root() 逻辑（纯 YAML，不依赖 git）：
  1. 向上查找 ccb-annto-memory.yaml → 找到则 CWD 即为项目根目录
  2. 未找到 → None（非团队记忆项目）
```

```
load_team_memory_config() 逻辑：
  1. ccb-annto-memory.yaml（V4.2 推荐）  ← 最高优先级
  2. .claude/settings.json teamMemory 段    ← 向后兼容（V4.0）
  3. 都没有 → None（静默跳过）
```

```
get_project_name() 优先级（V4.2）：
  1. project.name（YAML 显式指定）
  2. git remote get-url origin → owner--repo（本地 git）
  3. 目录名（fallback）
```

### 4.3 settings.json 配置段（向后兼容）

```json
{
  "teamMemory": {
    "repo": "git@github.com:myorg/team-memories.git",
    "branch": "main",
    "enabled": true,
    "extract": {
      "mode": "instruction",
      "scope": "all",
      "autoPush": true
    },
    "load": {
      "autoLoad": true,
      "maxFiles": 10
    },
    "scan": {
      "enabled": true
    }
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `repo` | — | 共享 Git 仓库 URL（必填） |
| `branch` | `main` | 分支 |
| `enabled` | `true` | 团队记忆开关 |
| `extract.mode` | `instruction` | `manual` / `instruction` / `auto` |
| `extract.scope` | `all` | `team` / `project` / `all` |
| `extract.autoPush` | `true` | 提取后是否自动 push |
| `load.autoLoad` | `true` | 启动时自动加载记忆摘要 |
| `load.maxFiles` | `10` | 手动 load 时最多返回文件数 |
| `scan.enabled` | `true` | 推送前密钥扫描 |

> 当 `ccb-annto-memory.yaml` 存在时，优先使用 YAML 中的 repo/path 信息。settings.json 中的 `extract`、`load`、`scan` 子配置仍然生效。

---

## 五、路径体系

| 路径 | 用途 | Git 跟踪 |
|------|------|----------|
| `<project>/../ccb-annto-memory.yaml` | **团队+项目记忆配置（V4.1 新增）** | 否（手动管理） |
| `<project>/.claude/settings.json` | teamMemory 配置 + hooks（向后兼容） | **是（项目 Git）** |
| `<project>/.claude/team-memory/` | 团队记忆 Git 仓库本地副本 | **否（.gitignore）** |
| `<project>/.claude/team-memory/shared/` | 跨项目团队记忆 | 是（团队记忆 Git） |
| `<project>/.claude/team-memory/projects/<name>/` | 本项目记忆 | 是（团队记忆 Git） |
| `<project>/.claude/team-memory/shared/MEMORY.md` | 团队级索引 | 是（团队记忆 Git） |
| `<project>/.claude/team-memory/projects/<name>/MEMORY.md` | 项目级索引 | 是（团队记忆 Git） |
| `<project>/.claude/rules/team-memory.md` | ccb @include 入口 | 是（项目 Git） |

### 5.1 @include 机制（ccb 原生）

ccb 加载 `.claude/rules/*.md` 时，解析 Markdown 中的 `@<path>` 指令，递归引入外部文件到模型上下文。`ccb-team-memory` 依赖此机制，**零修改 ccb 源码**即可注入团队记忆。

**语法**（来自 `claudemd.ts`）：

| 写法 | 含义 |
|------|------|
| `@path` | 相对路径（等同于 `@./path`） |
| `@./relative/path` | 相对于当前文件所在目录 |
| `@~/home/path` | 相对用户家目录 |
| `@/absolute/path` | 绝对路径 |

**规则**：
- 递归引入，有最大深度限制 + 循环检测
- 仅允许文本文件（`.md`, `.txt` 等）
- 被引入的文件在父文件之前注入上下文
- `MEMORY.md` 类型文件限制最大行数和字节数

**ccb-team-memory 的集成方式**：

```
ccb 启动
  → 扫描 .claude/rules/team-memory.md
    → 解析 @../team-memory/MEMORY.md
      → 展开团队记忆摘要 → 注入模型上下文
      → 模型在后续对话中遵循团队记忆约束
```

`_ensure_rules_wrapper()` 自动生成入口文件（中文）：

```markdown
<!-- team-memory managed -->
# 团队记忆
通过 ccb-team-memory 同步的团队共享知识。
@../team-memory/MEMORY.md
```

`MEMORY.md` 由 `team-memory load auto` 生成，包含所有已同步记忆的索引摘要。

---

## 六、CLI 命令总览

```
team-memory
├── init      [--repo <url>] [--generate-yaml]  初始化项目团队记忆
├── pull                                        拉取最新团队记忆
├── push      [--force]                         推送本地变更
├── scan                                        密钥扫描
├── status                                      查看状态
├── extract
│   ├── prompt  [--mode auto|instruction|manual]  生成提取 prompt
│   └── status                                   查看提取状态
├── load
│   ├── auto                                     自动加载记忆摘要
│   ├── search [query] [--type]                  搜索并加载记忆
│   └── list                                     列出所有记忆文件
├── install   [--config-dir <path>]              安装 hooks + Skill 到 ccb
└── uninstall [--config-dir <path>]              移除 ccb hooks
```

---

## 七、技术架构

### 7.1 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python 3.13+ |
| CLI 框架 | argparse（零外部依赖） |
| Git 操作 | subprocess（git CLI） |
| 配置读写 | json（settings.json）+ YAML |
| 密钥扫描 | re（正则，移植 36 条规则） |
| 测试 | pytest |

### 7.2 Python 包结构（V4.4 五层架构）

```
ccb-team-memory/
├── pyproject.toml
├── src/team_memory/
│   ├── __init__.py
│   ├── __main__.py             # python -m team_memory
│   │
│   ├── cli/                    # 接口层 — argparse + 参数校验
│   │   ├── __init__.py
│   │   ├── main.py             # 顶层 parser + dispatch
│   │   ├── init.py             # cmd_init
│   │   ├── sync.py             # cmd_pull / cmd_push / cmd_scan / cmd_status
│   │   ├── extract.py          # cmd_extract_prompt / cmd_extract_status
│   │   ├── load.py             # cmd_load_auto / cmd_load_search / cmd_load_list
│   │   └── install.py          # cmd_install / cmd_uninstall
│   │
│   ├── services/               # 业务层 — 领域逻辑
│   │   ├── __init__.py
│   │   ├── extract.py          # 提取 prompt 生成 + manifest
│   │   ├── loader.py           # 记忆加载（自动/手动）
│   │   ├── sync.py             # pull/push 编排
│   │   ├── scanner.py          # 密钥扫描（36 规则）
│   │   └── installer.py        # hooks + Skill 注册 + rules wrapper
│   │
│   ├── config/                 # 配置层 — 数据模型 + YAML 解析 + 项目发现
│   │   ├── __init__.py
│   │   ├── settings.py         # TeamMemoryConfig + settings.json 读写 + 路径工具
│   │   └── annto.py            # AnntoMemoryConfig + YAML 解析/生成 + 身份校验
│   │
│   ├── utils/                  # 工具层 — 底层通用操作
│   │   ├── __init__.py
│   │   └── git.py              # Git subprocess 封装
│   │
│   └── templates/              # 资源层 — 静态 Markdown 模板
│       ├── extract.md          # 提取 prompt 模板
│       └── SKILL.md            # Skill 模板
│
└── tests/
```

**五层职责边界**：

| 层 | 职责 | 依赖方向 |
|----|------|----------|
| `cli/` | argparse 定义，参数校验，调用 services | → services, config |
| `services/` | 领域逻辑编排 | → config, utils |
| `config/` | 数据模型，配置读写，YAML 解析，项目发现，身份校验 | → utils（仅 YAML 解析需 git） |
| `utils/` | git subprocess 封装，通用底层操作 | 无内部依赖 |
| `templates/` | 静态 .md 资源，打包进 wheel | 无代码依赖 |

### 7.3 依赖

仅 **git + Python 3.13 标准库**。零外部 Python 依赖。

---

## 八、安全

| 措施 | 实现 |
|------|------|
| 推送前密钥扫描 | scanner.py（36 条正则，移植自 ccb） |
| 仅同步 .md | `git add *.md` |
| 配置不含密钥 | repo URL 为公开 Git 地址 |

---

## 九、实施计划

### Phase 1：提取引擎 + Git 同步
1. 项目骨架 + `pyproject.toml`
2. `config.py` — settings.json 读写
3. `git_ops.py` — Git subprocess 封装
4. `sync.py` — pull/push 编排
5. `extract.py` — prompt 生成、manifest 扫描、模式分发
6. `prompts/extract.md` — 提取 prompt 模板

### Phase 2：加载 + 安装
7. `loader.py` — 自动加载摘要 + 手动搜索加载
8. `scanner.py` — 密钥扫描
9. `cli.py` — 全部命令
10. `installer.py` — hooks + Skill 注册

### Phase 3：自动发现（V4.1）
11. `config.py` — `AnntoMemoryConfig` 数据类、`find_annto_yaml()`、`load_annto_yaml()`
12. `sync.py` — `do_init()`/`do_pull()` 适配分离的 team/project 仓库
13. `cli.py` — `init` 新增 `--generate-yaml`、`--team-repo`、`--project-repo`

### Phase 4：身份校验（V4.2）
14. `config.py` — `find_project_root()` 改纯 YAML（移除 git rev-parse）
15. `config.py` — `ProjectIdentity` 数据类、`verify_project_identity()`、`get_git_remote_url()`
16. `config.py` — `get_project_name()` 三级优先级（YAML name → git remote → 目录名）
17. `cli.py` — `cmd_push` 加身份校验拦截、`cmd_pull` 加警告

### Phase 5：测试
18. 单元测试（scanner / extract / config / annto_yaml / identity_verification）
19. 集成测试（完整流：extract → write → push → pull → load）
20. ccb 联调

---

## 十、验证

1. **YAML 发现验证**：在项目父目录放置 `ccb-annto-memory.yaml`，运行 `team-memory pull` 自动发现并拉取
2. **非 git 项目验证**（V4.2）：纯 YAML 驱动，无 git 目录也能发现项目
3. **身份校验通过**（V4.2）：`project.url` = 本地 git remote → push 允许
4. **身份校验拒绝**（V4.2）：`project.url` 为空/不匹配/非 git → push 拒绝
5. **向后兼容验证**：无 YAML 文件时，从 `.claude/settings.json` 读取配置正常工作
6. **提取验证**：提供一个对话样本，验证提取 prompt 是否生成正确
7. **同步验证**：init → 写入记忆 → push → 另一目录 pull → 内容一致
8. **安全验证**：含密钥的记忆文件 → push 被阻断
9. **加载验证**：SessionStart 自动 load 摘要注入模型上下文
10. **端到端**：ccb 中完整走通 extract → write → push → pull → load
