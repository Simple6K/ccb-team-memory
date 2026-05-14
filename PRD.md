# 企业团队记忆功能 - 需求文档 V4.10

## 文档信息

| 字段 | 值 |
|------|-----|
| 产品名称 | ccb-team-memory |
| 目标版本 | 1.0.0 |
| 生成日期 | 2026-05-06 |
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
    → team-memory extract run
      → 守卫检查（冷却时间、互斥、目录存在）
      → 读取 session transcript（JSONL）
      → 扫描已有记忆文件（manifest）
      → 构建提取 prompt（复用已有 build_extract_prompt）
      → Agent Loop（异步子 agent 模式）:
          ├─ 调用 Anthropic API（DeepSeek 兼容端点）
          ├─ 模型返回 tool_use → 执行工具（读/写/编辑记忆文件）
          ├─ 路径沙箱限制在 .claude/team-memory/ 内
          ├─ 发送 tool_result → API 继续
          └─ 模型返回纯文本 → 完成
      → 更新 .extract-state.json（游标推进）
      → 条件推送检查（--push-on-count / --push-on-minutes）:
          计数达标或（超时且 _staging/ 非空）→ team-memory push（自动提交+推送）
  → PostToolUse Hook 触发
    → team-memory push（自动提交+推送）
```

**对标 ccb-dev 内置 executeExtractMemories**：同样采用异步 agent loop 模式，
独立调用 API 完成提取，用户完全无感知。差异仅在于内置系统从进程内存
读取对话，本系统从 JSONL transcript 文件读取。

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
- **项目相关性前置判断**（V4.8）：先判断对话是否与当前项目相关，无关则跳过提取
- **提取状态上下文**（V4.6）：上次提取时间、累计次数、上次写入的文件列表
- **会话标识**（V4.6）：项目名 + 时间戳，用于追踪提取来源
- 四种记忆类型定义 + scope 规则（XML 结构化格式）
- 不保存的内容清单（排除规则，含"即使用户要求保存也适用"条款）
- 已有记忆 manifest（按类型分组，含 name/description，用于精确去重）
- **MEMORY.md 约束**（V4.6）：每条索引 ≤ 150 字符、按类型分组、总行数 ≤ 200
- 写入指令（路径、frontmatter 格式、MEMORY.md 两步更新流程）
- **去重指令**（V4.6）：明确要求先检查已有记忆中是否存在相同主题，如有则更新而非新建

#### 3.1.6 排除规则（不提取的内容）

- 代码片段或项目源文件内容
- 会话特定的临时上下文
- 与 `.claude/CLAUDE.md` 重复或矛盾的信息
- 敏感数据（API 密钥、令牌、密码）
- 临时调试状态（断点、变量值）
- 会过时的 Git 分支名或 PR 号
- 可通过链接引用的文档原文
- 与当前项目无关的通用讨论（纯技术问答、闲聊、不涉及项目决策/约束/流程的对话）

---

### 3.1A 记忆审核（`review`）（V4.9 新增，**V4.10 已由 knowledge extract 取代**）

> **注意**：V4.10 的 `knowledge extract` 已取代原有的 review/approve/integrate 流程。
> `_staging/` 中的记忆通过二次提取直接归纳为结构化知识文档，无需人工逐条审核。
> `review` 子命令组保留向后兼容，但建议迁移到 `knowledge extract`。

自动提取的记忆写入 `_staging/` 待审核区，不会自动加载。

#### 目录结构

```
.claude/team-memory/
  ├── _staging/          ← 自动提取写入（无需 MEMORY.md，仅本地不随 pull 同步）
  ├── shared/            ← 审核通过后移入
  ├── projects/<name>/   ← 审核通过后移入
  └── knowledge/         ← 知识文档（V4.10，knowledge extract 产出）
```

#### 命令

```bash
team-memory review list              # 列出所有待审核记忆
team-memory review approve <N>       # 批准指定编号的记忆
team-memory review approve --all     # 批准全部
team-memory review reject <N>        # 拒绝指定编号的记忆
team-memory review reject --all      # 拒绝全部
team-memory review integrate         # 批量整合远程 _staging/ → shared/projects
team-memory review integrate --dry-run  # 预览整合（不实际执行）
```

#### 批量整合（`review integrate`）

多人团队各自提取记忆到 `_staging/` 并 push 到远端后，管理员定期运行整合命令，
将所有远程增量 staging 进行去重合并，整理到正式目录。

**关键边界**：只整合 pull 带来的远程增量 staging（通过 `git diff` 对比 pull 前后的 remote HEAD），
本地自己提取但未 push 的 staging 文件不会被纳入。

#### knowledge extract 取代 review（V4.10）

`knowledge extract` 统一处理 `_staging/` 中的所有待审核记忆：
- **提取即审核**：不再有单独的 review → approve → integrate 流程
- **AI 归纳**：原始记忆被 AI 归纳为结构化知识文档（QA 对 / 体系化知识）
- **写入 knowledge/**：知识文档存放在团队记忆仓库的 `knowledge/` 目录
- **_staging/ 保留不变**：提取后 `_staging/` 文件保留，后续 extract 通过 doc_id upsert 自然去重

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

- 自动查找 `ccb-annto-memory.yaml`
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

- 在项目目录创建 `ccb-annto-memory.yaml` 模板
- 后续其他项目可直接通过方式一初始化

#### 3.3.2 拉取（`pull`）

```bash
team-memory pull
```

`pull` 使用 git sparse-checkout 排除 `_staging/` 目录。`_staging/` 仅本地存储，不随 pull 同步。只有 `knowledge extract` 在本地读取 `_staging/` 进行二次提取。

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
    → find_project_root() — 在当前目录查找 ccb-annto-memory.yaml
      → 找到：
        ├─ git clone/pull team_memory.repo → .claude/team-memory/
        ├─ git clone/pull project_memory.repo（如分离）
        ├─ verify_project_identity() → 校验 project.url（不匹配仅警告）
        └─ team-memory load auto → 生成 MEMORY.md
      → 未找到：
        └─ 检查 .claude/settings.json teamMemory.repo（向后兼容）
          → 存在 → 使用 legacy 模式
```

新成员无需任何手动配置，只要项目目录有 `ccb-annto-memory.yaml`，ccb 启动即自动拉取团队记忆。

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

### 3.6 安装到 ccb（`install` / `uninstall`）

**安装**：
```bash
team-memory install [--config-dir ~/.ccb-dev]
```

**卸载**（V4.6：同步移除 hooks + skill）：
```bash
team-memory uninstall [--config-dir ~/.ccb-dev]
```

`--config-dir` 指定目标 ccb 配置目录（默认优先 `~/.ccb-dev/`，回退 `~/.claude/`）。

注册以下 hooks 到 `settings.json`：

| Hook 事件 | 动作 | 说明 |
|-----------|------|------|
| SessionStart | `team-memory pull && team-memory load auto` | 拉取 + 自动加载 |
| PostToolUse | `team-memory push` | 写入后自动推送 |
| Stop | `team-memory extract run [--push-on-count N] [--push-on-minutes M]` | 每轮后提取，可选条件推送（V4.9） |
| SessionEnd | `team-memory push` | 退出前推送 |

同时注册 Skill 到全局（`~/.ccb-dev/skills/team-memory/SKILL.md`）和项目级（`.claude/skills/team-memory/SKILL.md`）。

卸载时同步移除 hooks 配置和 skill 文件。

### 3.7 提取状态追踪（V4.6 新增）

**对应 claude-code**：`initExtractMemories()` 闭包（`extractMemories.ts:296`）

**状态文件**：`.claude/team-memory/.extract-state.json`

```json
{
  "last_extraction_at": "2026-04-29T16:00:00+08:00",
  "last_push_at": "2026-04-29T16:05:00+08:00",
  "total_extractions": 42,
  "last_files_written": ["user_prefs.md"],
  "team_count": 15,
  "project_count": 27
}
```

**核心能力**：
- **游标追踪**：`last_extraction_at` 作为提取游标，避免重复处理
- **互斥检测**：`detect_writes_since_last()` 检测主模型是否已自行写入 → 自动跳过
- **合并暂存**：提取进行中时到达的新请求暂存，执行完后以 trailing run 补齐
- **历史查询**：`team-memory extract history` 查看累计统计

**命令**：
```bash
team-memory extract history   # 查看提取历史
team-memory extract status    # 增强：显示类型统计 + 上次提取信息
```

### 3.8 记忆验证（V4.6 新增）

**对应 claude-code**：`extractWrittenPaths()` + frontmatter 校验（`extractMemories.ts:437`）

```bash
team-memory verify
```

检查项：
- frontmatter 必需字段（name、description、type）
- type 值合法性（user/feedback/project/reference）
- MEMORY.md 条目与实际文件一致性
- 重复 name 检测
- 孤立索引条目 / 未索引文件

**Push 前置集成**：`do_push()` 调用前自动执行 `verify_before_push()`，验证失败阻止 push（`--force` 跳过）。

### 3.9 记忆整合（V4.6 新增）

**对应 claude-code**：`initAutoDream()` 门控链（`autoDream.ts:123`）

```bash
team-memory consolidate [--dry-run] [--apply] [--force]
```

**门控顺序**（参考 autoDream：时间 → 会话数 → 锁）：
1. 时间门控：距上次整合 ≥ 24 小时
2. 文件数门控：记忆文件 ≥ 10
3. 锁机制：防并发整合（`.consolidation-lock`，1 小时过期）

**整合候选扫描**：
- **merge**：name 重复或语义相近的记忆
- **archive**：超过 90 天未更新的记忆 → 移至 `_archived/`
- **repair_index**：重建 MEMORY.md 索引，修复不一致

**选项**：
- `--dry-run`：仅预览，不执行变更
- `--apply`：执行整合
- `--force`：跳过门控

---

### 3.10 知识模块系统（V4.10 新增）

#### 3.10.1 问题

一级记忆抽取稳定运行，原始记忆持续累积。但 `load auto` 将全部原始记忆平铺注入上下文，导致"发散"——大量不相关碎片记忆稀释有效上下文。记忆是原子事实，知识是经过归纳、结构化、可被选择性应用的认知模块。当前只有一级提取（对话 → 记忆），缺少二级提取（记忆 → 知识模块）。

#### 3.10.2 知识价值定义

从原始记忆中提取两类知识：

| 类型 | 内容 | 形式 | 来源 |
|------|------|------|------|
| **踩坑记录** | 架构不理解、模块关联不清、业务需求/流程理解偏差 | Q&A 结构化（问题 + 正确理解） | type=feedback 记忆 |
| **体系化知识** | 需求澄清、流程介绍、架构设计、未来规划 | 结构化知识文档 | type=project/reference 记忆 |

**边界**：大模型执行犯错不归此类（那是 cc memory 管的事）。

#### 3.10.3 整体流程

```
一级提取（现有，不变）
  对话 transcript
    → Stop hook: extract run
    → AI 读取对话，生成原始记忆 .md
    → 写入 _staging/

二级提取 = 审核（V4.10 新增，取代原 review 流程）
  team-memory knowledge extract
    → git pull 拉取远程 _staging/ 增量
    → 增量读取 _staging/ 中未处理的待审核记忆（通过 .extracted-staging.json 跟踪）
    → 按提取器分类，AI 归纳为结构化知识文档
    → 写入 knowledge/ 目录（扁平结构）
    → 记录已处理文件到 .extracted-staging.json
    → git commit（不 push）

知识拉取（V4.10 新增）
  team-memory knowledge pull [--tags "..."] [--all]
    → git pull 同步远端 knowledge/
    → 按过滤条件匹配文档（或 --all 全量）
    → 注入到 shared/ 和 projects/（现有记忆目录）
    → load auto 照常读取，无需改动
```

**关键设计决策**：
- **提取即审核**：不再有单独的 review → approve → integrate 流程。二次提取本身就是对 `_staging/` 的"审核"——原始记忆被 AI 归纳为结构化知识
- **输入源是 `_staging/`**：二次提取处理全部 `_staging/` 中的待审核记忆
- **输出在中央仓库**：知识文档存放在 team-memory 仓库 `knowledge/` 目录，随 git 跨项目同步
- **提取可重复**：知识文档通过 `doc_id` 实现 upsert，同主题更新覆盖旧文档（`doc_id` 由 Python 对源文件路径 SHA256 hash 生成）
- **拉取注入记忆目录**：过滤后的知识写入 `shared/` 和 `projects/`，与现有记忆共存。`load auto` 无需任何改动
- **_staging/ 保留不动**：提取后 `_staging/` 文件保留，后续 extract 通过 doc_id upsert 自然去重

#### 3.10.4 知识存储模型

所有知识以 markdown 文件存在 team-memory git 仓库的 `knowledge/` 目录下（**扁平结构**，domain 存储在 frontmatter 中）：

```
team-memory/
├── _staging/                  # 一级提取产出（现有）
├── shared/                    # 审核通过的记忆 + 注入的知识文档
├── projects/                  # 项目记忆 + 注入的知识文档
└── knowledge/                 # 知识文档（新增，扁平，跨项目共享）
    ├── KNOWLEDGE.md           # 知识索引（自动维护）
    ├── .tag-dict.yaml         # 团队仓库级标签字典
    ├── shared/                # 共享知识（拉取时全量注入，无过滤）
    │   └── kn-xxx.md
    ├── kn-xxx-a3f2c1d8.md     # tags: [支付, 后端, 架构]
    └── kn-yyy-b4e3d2c9.md     # tags: [支付, 后端, 踩坑]
```

> `knowledge/shared/` 中的文档在拉取时始终全量注入，不受 tags/domain/time 过滤。用于跨业务线通用的架构规范、编码标准等。

**注入到 shared/ 和 projects/ 的知识文件加 `kn-` 前缀**，与原始记忆区分：

```
shared/
├── kn-团队编码规范.md          ← 知识文档
├── user_prefs.md                ← 原始记忆
└── MEMORY.md

projects/业务线A/
├── kn-支付系统架构.md           ← 知识文档
├── auth-middleware.md           ← 原始记忆
└── MEMORY.md
```

**注入规则**：文档 tags 含 `Public` → 注入到 `shared/`（跨项目共享）；否则 → 注入到 `projects/<name>/`（项目私有）。

**知识文档 frontmatter**：

```yaml
---
doc_id: "a3f2c1d8e9b0"                        # 稳定 ID（Python 对源文件路径 hash 生成）
type: "knowledge"                              # "knowledge" | "qa_pair"
domain: "architecture"                         # 知识领域
title: "项目目录结构与设计模式"
business_line_id: ""                           # 预留占位符，独立的业务线 ID
tags: ["架构", "前端"]                         # 核心关联字段（人工定义，AI 仅从中选取）
source_files: ["_staging/mem-001.md", ...]     # 来源记忆
source_count: 5
generated_at: "2026-05-11T15:30:00Z"
extractor: "architecture"
---
```

#### 3.10.5 标签系统

标签是知识发现和关联匹配的核心机制：

| 维度 | 示例 | 作用 |
|------|------|------|
| 业务领域 | `支付`, `订单`, `用户` | 跨业务线的领域关联 |
| 技术栈 | `前端`, `后端`, `数据库` | 技术视角归类 |
| 知识类型 | `架构`, `流程`, `规范`, `踩坑` | 与 domain 互补 |
| 角色 | `研发`, `测试`, `产品` | 按角色分发 |
| 业务线 | `业务线A`, `业务线B` | 归属标识 |
| 注入 | `Public` | 控制注入目标（shared/ vs projects/） |

**标签维护原则**：
- 标签**完全由人工定义**，AI 不生成、不建议新标签
- 提取器在 prompt 中携带标签字典，AI 只能从已有标签中选取匹配的标注到文档

**标签字典配置链**（优先级从低到高，后层覆盖前层）：

```
1. 框架内置标签字典    → src/team_memory/knowledge/tags/default.yaml
2. 团队仓库标签字典    → team-memory/knowledge/.tag-dict.yaml
3. 项目级标签字典      → ccb-annto-memory.yaml 的 knowledge.tags 段
```

`.tag-dict.yaml` 格式：

```yaml
# 团队仓库级标签字典（人工维护）
业务线: [业务线A, 业务线B]
业务领域: [支付, 订单, 账户, 营销]
技术栈: [前端, 后端, 数据库, 中间件]
知识类型: [架构, 流程, 规范, 踩坑]
角色: [研发, 测试, 产品, 运维]
Public: [Public]
```

#### 3.10.6 提取器架构

```python
class KnowledgeExtractor(ABC):
    name: str                # 提取器标识
    doc_type: str            # "knowledge" | "qa_pair"
    domain: str              # 知识领域
    tags: list[str]          # 默认标签（从配置注入）

    def input_filter(self, staging_files: list[dict]) -> list[dict]:
        """从 _staging/ 中筛选本提取器处理的文件。
        每个 dict 含: path (str), name, description, type, scope（来自 frontmatter）。
        基于元数据筛选，无需读取完整文件内容。
        """

    def build_prompt(self, memories: list[dict], tag_dict: dict) -> str:
        """构建归纳提示词。
        注入 tag_dict 约束 AI 标签选择——AI 只能从已有标签中选取。
        注入预生成的 doc_id，要求 AI 在 frontmatter 中使用该值。
        """

    def generate_doc_id(self, memories: list[dict]) -> str:
        """生成稳定的文档 ID，用于 upsert。
        基于源文件路径列表做确定性 hash（sha256 取前 12 位），保证：
        - 相同源文件集合 → 相同 doc_id → upsert 覆盖
        - 不同源文件集合 → 不同 doc_id → 新增文档
        此方法在调用 AI 前执行，doc_id 注入到 prompt 中。
        """
```

**首批提取器**：

| 提取器 | 输出类型 | 筛选逻辑 |
|--------|---------|---------|
| QA | qa_pair | type=feedback，含架构理解/流程困惑关键词 |
| 架构 | knowledge | type=project，含文件结构/设计模式/技术选型 |
| 流程 | knowledge | type=project，含开发流程/部署/发布 |
| 需求 | knowledge | type=project/reference，含需求澄清/业务理解 |

**可扩展性**：在 `src/team_memory/knowledge/extractors/` 目录新增 `.py` 文件即自动注册。

#### 3.10.7 项目级配置

配置定义在 `ccb-annto-memory.yaml` 的 `knowledge` 段（项目级配置属于项目，不跨项目共享）：

```yaml
# ccb-annto-memory.yaml（新增 knowledge 段）
knowledge:
  # repo: git@github.com:org/team-memories.git  # 知识文档远程仓库（不配则用 team_memory.repo）
  # path: "knowledge/"                         # 远程仓库内路径

  # 提取器开关
  extractors:
    enabled: [qa, architecture]

  # 加载过滤（knowledge pull 默认使用）
  load:
    auto_tags: [研发, 业务线A]
    auto_domains: [architecture, qa]
    doc_types: [knowledge, qa_pair]
    time_range:
      since: "7d"
    max_docs: 8

  # 项目级标签扩展（追加到团队标签字典）
  tags:
    业务领域: [风控]
```

#### 3.10.8 知识拉取与加载

**核心模型**：

```
中央仓库（team-memory/knowledge/）
  │
  │  team-memory knowledge pull --tags "支付,后端"
  │    → git pull 同步远端 knowledge/
  │    → 按条件匹配知识文档
  │    → 写入 shared/ 和 projects/（现有记忆目录）
  │    → 更新 MEMORY.md 索引
  │
  ▼
shared/ + projects/（现有记忆目录）
  - 原始记忆 + 知识文档 共存
  - load auto 照常读取 MEMORY.md
```

**拉取命令**：

```bash
# 按配置过滤拉取
team-memory knowledge pull

# 按指定标签拉取
team-memory knowledge pull --tags "支付,后端"

# 拉取全部知识（无过滤）
team-memory knowledge pull --all

# 按领域拉取
team-memory knowledge pull --domain architecture

# 按 doc_id 精确拉取
team-memory knowledge pull --doc-id "a3f2c1d8e9b0"

# 预览（仅展示将注入的文件，不实际写入）
team-memory knowledge pull --tags "支付" --dry-run
```

**注入规则**：文档 tags 含 `Public` → 注入到 `shared/`；否则 → 注入到 `projects/<name>/`。`knowledge/shared/` 下的文档始终全量注入，不受过滤。

**默认行为**：SessionStart 自动执行 `knowledge pull`（使用配置中的过滤条件），通过 `auto_load()` 前置调用实现，静默失败不影响记忆加载。

**过滤维度**（各维度 AND 关系）：

| 维度 | 配置项 | CLI 覆盖 | 说明 |
|------|--------|----------|------|
| 标签 | `load.auto_tags` | `--tags` | 文档 tags 与过滤 tags 交集非空即匹配 |
| 领域 | `load.auto_domains` | `--domain` | 匹配 frontmatter `domain` 字段 |
| 类型 | `load.doc_types` | — | `knowledge` 或 `qa_pair` |
| 时间 | `load.time_range` | — | 相对时间（`7d`/`24h`）或绝对日期 |
| 数量 | `load.max_docs` | — | 最多 N 篇，按 `generated_at` 倒序 |
| 全量 | — | `--all` | 忽略所有过滤，拉取全部 |

#### 3.10.9 提取触发与边界

二级提取为**手动触发**，不挂 hook：

```bash
team-memory knowledge extract [--extractor NAME] [--force] [--dry-run]
```

流程：
1. git pull 获取远端 `_staging/` 增量
2. 扫描 `_staging/` 中所有 .md 文件（含 frontmatter 元数据）
3. 对每个启用提取器执行 `input_filter()` 筛选
4. Python 预生成 `doc_id`（对源文件路径 hash）
5. 调用 AI 生成结构化知识文档（单次 API 调用）
6. 按 `doc_id` 写入 `knowledge/`（新增或替换）
7. 更新 `KNOWLEDGE.md` 索引
8. git commit（不 push）

**边界情况与错误处理**：

| 场景 | 行为 |
|------|------|
| `_staging/` 为空 | 输出 "无待审核记忆，跳过知识提取" 并退出（退出码 0） |
| `git pull` 失败 | 警告但继续（用本地已有 `_staging/` 内容提取） |
| 某提取器 `input_filter()` 无匹配 | 跳过该提取器，输出 "提取器 X: 无匹配记忆" |
| AI 返回内容 frontmatter 格式错误 | 重试一次；仍失败则写入 `.knowledge-errors/` 待人工处理 |
| 两个提取器生成相同 doc_id | 后执行的覆盖前者（视为同一主题不同视角合并） |
| `--dry-run` 模式 | 展示将生成的文档摘要（标题、doc_id、tags），不写入文件 |

#### 3.10.10 多业务线关联

```
                    ┌────────────────────────┐
                    │   团队共享知识           │
                    │   knowledge/shared/     │  全量拉取，无过滤
                    │   跨业务线通用           │
                    └────────┬───────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
  ┌───────▼───────┐  ┌──────▼──────┐  ┌───────▼───────┐
  │  业务线 A     │  │  业务线 B   │  │  业务线 C     │
  │  tags: [A..]  │  │  tags: [B..]│  │  tags: [C..] │
  └───────────────┘  └─────────────┘  └───────────────┘
          │                  │                  │
          │        标签交集自动发现关联          │
          └────────────────┬───────────────────┘
```

- `business_line_id`：独立字段（预留占位符），不与项目名耦合
- 跨业务线关联通过标签交集自动发现，不依赖显式引用

---

## 四、配置

### 4.1 ccb-annto-memory.yaml（推荐，V4.1 新增，V4.2 增强）

**文件位置**：项目根目录（`ccb-annto-memory.yaml`）

**项目发现**：`find_project_root()` 纯 YAML 驱动，不依赖 git。在当前目录查找 `ccb-annto-memory.yaml`，找到即视为项目根目录。

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

# 知识模块配置（V4.10 新增）
knowledge:
  extractors:
    enabled: [qa, architecture]         # 按需启用提取器
  load:
    auto_tags: [研发]                   # SessionStart 自动拉取标签
    auto_domains: [architecture, qa]   # SessionStart 自动拉取领域
    doc_types: [knowledge, qa_pair]
    time_range:
      since: "7d"                      # 时间过滤
    max_docs: 8                        # 最多拉取 N 篇
  tags: {}                              # 项目级标签扩展
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
| `knowledge.extractors.enabled` | 否 | 全启用 | 启用的知识提取器列表（qa, architecture, workflow, requirements） |
| `knowledge.load.auto_tags` | 否 | `[]` | SessionStart 自动拉取的知识标签 |
| `knowledge.load.auto_domains` | 否 | `[]` | SessionStart 自动拉取的知识领域 |
| `knowledge.load.doc_types` | 否 | `["knowledge", "qa_pair"]` | 可拉取的知识类型 |
| `knowledge.load.time_range.since` | 否 | 无 | 时间过滤（`7d`/`24h`/绝对日期） |
| `knowledge.load.max_docs` | 否 | 无限制 | 每次拉取最多文档数 |
| `knowledge.tags` | 否 | `{}` | 项目级标签扩展（追加到团队标签字典） |

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
  1. 在当前目录查找 ccb-annto-memory.yaml → 找到则 CWD 即为项目根目录
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
| `<project>/ccb-annto-memory.yaml` | **团队+项目记忆配置（V4.5 更新）** | 否（手动管理） |
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
│   ├── run      [--push-on-count N] [--push-on-minutes M]  运行自动提取（异步 agent loop，可选条件推送）
│   ├── status                                   查看提取状态
│   └── history                                  查看提取历史
├── load
│   ├── auto                                     自动加载记忆摘要
│   ├── search [query] [--type]                  搜索并加载记忆
│   └── list                                     列出所有记忆文件
├── review    （V4.10 已由 knowledge extract 取代，保留向后兼容）
│   ├── list                                      列出待审核记忆
│   ├── approve <N> [--all]                       批准记忆
│   ├── reject <N> [--all]                        拒绝记忆
│   └── integrate [--dry-run] [--no-pull]         批量整合远程 staging
├── knowledge                                     知识模块系统（V4.10）
│   ├── extract  [--extractor NAME] [--force] [--dry-run]  运行知识提取（取代 review）
│   ├── pull     [--tags TAGS] [--domain D] [--doc-id ID] [--all] [--dry-run]
│   │                                                    拉取知识，注入 shared/projects
│   ├── review                                          审核知识文档变更（V4.10 补丁）
│   │   ├── list                                        列出未发布的知识 commit
│   │   ├── show <hash> [--full]                        显示指定 commit 详情
│   │   ├── approve                                     发布所有待审 commit（push）
│   │   └── reject <hash> [--message]                   撤销指定 commit（git revert）
│   ├── list    [--stale]                                列出知识模块
│   ├── show    <DOC_ID>                                 显示模块完整内容
│   ├── status                                           模块统计
│   └── clean   [--stale-only]                           清理模块
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
│   │   └── knowledge_cmd.py     # cmd_knowledge_*（V4.10）
│   │
│   ├── services/               # 业务层 — 领域逻辑
│   │   ├── __init__.py
│   │   ├── extract.py          # 提取 prompt 生成 + manifest
│   │   ├── agent_loop.py       # 微型 Agent Loop（异步提取核心，V4.7）
│   │   ├── api_client.py       # Anthropic API 客户端（urllib，零依赖，V4.7）
│   │   ├── loader.py           # 记忆加载（自动/手动）
│   │   ├── sync.py             # pull/push 编排
│   │   ├── scanner.py          # 密钥扫描（36 规则）
│   │   └── installer.py        # hooks + Skill 注册 + rules wrapper
│   │
│   ├── knowledge/              # 知识模块层 — 二级提取框架（V4.10）
│   │   ├── __init__.py
│   │   ├── base.py             # KnowledgeExtractor ABC
│   │   ├── registry.py         # 提取器自动发现
│   │   ├── runner.py           # 提取与拉取编排
│   │   ├── store.py            # 知识文档读写 + KNOWLEDGE.md 索引
│   │   ├── tags/
│   │   │   └── default.yaml    # 框架内置标签字典
│   │   └── extractors/         # 可扩展提取器
│   │       ├── qa.py
│   │       ├── architecture.py
│   │       ├── workflow.py
│   │       └── requirements.py
│   │
│   ├── config/                 # 配置层 — 数据模型 + YAML 解析 + 项目发现
│   │   ├── __init__.py
│   │   ├── settings.py         # TeamMemoryConfig + settings.json 读写 + 路径工具
│   │   └── annto.py            # AnntoMemoryConfig + YAML 解析/生成 + 身份校验
│   │
│   ├── utils/                  # 工具层 — 底层通用操作
│   │   ├── __init__.py
│   │   ├── git.py              # Git subprocess 封装
│   │   └── transcript.py       # Session transcript 读取（V4.7）
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

### Phase 6：自动提取 Agent Loop（V4.7）
21. `utils/transcript.py` — session JSONL 读取、消息过滤、API 格式转换
22. `services/api_client.py` — Anthropic API 客户端（urllib，凭证获取）
23. `services/agent_loop.py` — 微型 Agent Loop（tool-use 循环、路径沙箱）
24. `services/extraction_manager.py` — `should_extract()` 冷却门控
25. `cli/extract.py` — `cmd_extract_run` 命令
26. `services/installer.py` — Stop hook 改为 `extract run`

---

## 十、验证

1. **YAML 发现验证**：在项目目录放置 `ccb-annto-memory.yaml`，运行 `team-memory pull` 自动发现并拉取
2. **非 git 项目验证**（V4.2）：纯 YAML 驱动，无 git 目录也能发现项目
3. **身份校验通过**（V4.2）：`project.url` = 本地 git remote → push 允许
4. **身份校验拒绝**（V4.2）：`project.url` 为空/不匹配/非 git → push 拒绝
5. **向后兼容验证**：无 YAML 文件时，从 `.claude/settings.json` 读取配置正常工作
6. **提取验证**：提供一个对话样本，验证提取 prompt 是否生成正确
7. **同步验证**：init → 写入记忆 → push → 另一目录 pull → 内容一致
8. **安全验证**：含密钥的记忆文件 → push 被阻断
9. **加载验证**：SessionStart 自动 load 摘要注入模型上下文
10. **端到端**：ccb 中完整走通 extract → write → push → pull → load
