# PRD 变更记录

## 变更 V4.10.1 — 远程路径可配置 + Knowledge Review

**日期**: 2026-05-12
**版本**: V4.10 → V4.10.1
**变更类型**: 功能新增 — 知识路径可配置 + commit 审核

### 背景

知识文档远程存储路径硬编码为 `knowledge/`，且 extract 自动 commit 后缺少审核环节。

### 变更

**路径可配置**：`ccb-annto-memory.yaml` 的 `knowledge` 段新增 `repo` 和 `path` 字段，控制知识文档的远程仓库和路径。本地路径 `.claude/team-memory/knowledge/` 不变。

**Knowledge Review**：新增 `knowledge review` 子命令组，基于 unpushed git commit 的审核流程：
- `knowledge review list` — 列出未发布的知识 commit
- `knowledge review show <hash> [--full]` — 显示 commit 详情
- `knowledge review approve` — push 发布
- `knowledge review reject <hash>` — git revert 撤销

### 修改文件

| 文件 | 改动 |
|------|------|
| `config/annto.py` | `KnowledgeConfig` 新增 `repo` + `path` 字段；`load_annto_yaml()` 解析；`generate_annto_yaml()` 模板更新 |
| `knowledge/runner.py` | `_get_knowledge_config()` 返回 `path`；`_git_commit()` 使用动态路径；`_load_tag_dict()` 路径动态化；新增 4 个 review 函数 |
| `cli/knowledge_cmd.py` | 注册 review 子命令组；新增 4 个命令处理器 |
| `PRD.md` | knowledge 配置段 + CLI 命令树更新 |

### PRD 变更

- PRD.md：§4.1 knowledge 配置段新增 `repo`/`path`；§6 CLI 命令树新增 review 子命令组

---

## 变更 V4.10 — 知识模块系统（V4.10）

**日期**: 2026-05-12
**版本**: V4.9 → V4.10
**变更类型**: 功能新增 + 架构变更 — 二级知识提取框架，取代 review 流程

### 背景

一级记忆抽取已稳定，原始记忆持续累积。但 `load auto` 将全部原始记忆平铺注入上下文，导致"发散"——大量不相关碎片记忆稀释有效上下文。需要二级提取对记忆进行归纳，并支持按需拉取。

### 核心设计决策

1. **提取即审核**：`knowledge extract` 取代原有的 review/approve/integrate 流程。`_staging/` 中的记忆统一通过二次提取进行"审核"——AI 归纳为结构化知识文档
2. **存入中央仓库**：知识文档写入 team-memory git 仓库的 `knowledge/` 目录（扁平结构）
3. **拉取注入记忆目录**：`knowledge pull` 按过滤条件拉取知识文档，注入到 `shared/` 和 `projects/`（现有记忆目录）。注入规则：含 `Public` 标签 → `shared/`，否则 → `projects/<name>/`
4. **注入文件加 `kn-` 前缀**：与原始记忆文件区分
5. **`load auto` 无需改动**：知识注入到 shared/projects 后，照常读取 MEMORY.md
6. **SessionStart 自动拉取**：`auto_load()` 前置调用 `run_knowledge_pull()`，静默失败不阻塞记忆加载
7. **配置在项目级**：过滤条件定义在 `ccb-annto-memory.yaml` 的 `knowledge` 段
8. **标签完全人工定义**：AI 只能从已有标签字典中选取，不生成新标签
9. **`doc_id` 由 Python 预生成**：对源文件路径 SHA256 hash，注入到 AI prompt 中
10. **`_staging/` 保留不动**：提取后不删除，通过 doc_id upsert 自然去重

### 文件清单

**新增**：
- `src/team_memory/knowledge/__init__.py`
- `src/team_memory/knowledge/base.py` — KnowledgeExtractor ABC
- `src/team_memory/knowledge/registry.py` — 提取器自动发现
- `src/team_memory/knowledge/runner.py` — 提取与拉取编排 + 辅助函数
- `src/team_memory/knowledge/store.py` — 知识文档读写 + KNOWLEDGE.md 索引 + 过滤逻辑
- `src/team_memory/knowledge/tags/default.yaml` — 框架内置标签字典
- `src/team_memory/knowledge/extractors/__init__.py`
- `src/team_memory/knowledge/extractors/qa.py` — QA 提取器（feedback → qa_pair）
- `src/team_memory/knowledge/extractors/architecture.py` — 架构提取器
- `src/team_memory/knowledge/extractors/workflow.py` — 流程提取器
- `src/team_memory/knowledge/extractors/requirements.py` — 需求提取器
- `src/team_memory/cli/knowledge_cmd.py` — knowledge 子命令组

**修改**：
- `src/team_memory/cli/main.py` — 注册 knowledge 子命令
- `src/team_memory/config/annto.py` — 新增 `KnowledgeConfig` 数据类；解析 YAML `knowledge` 段；新增 `_normalize_yaml_value()` 辅助
- `src/team_memory/config/__init__.py` — 导出 `KnowledgeConfig`
- `src/team_memory/services/loader.py` — `auto_load()` 前置执行 `_auto_knowledge_pull()`

### CLI 命令

```
team-memory knowledge
├── extract  [--extractor NAME] [--force] [--dry-run]  运行知识提取（取代 review）
├── pull     [--tags TAGS] [--domain D] [--doc-id ID] [--all] [--dry-run]
│                                                       拉取知识，注入 shared/projects
├── list     [--stale]                                 列出知识模块
├── show     <DOC_ID>                                  显示模块完整内容
├── status                                              模块统计
└── clean    [--stale-only]                            清理模块
```

### 过滤维度（knowledge pull）

| 维度 | 配置项 | CLI 覆盖 | 说明 |
|------|--------|----------|------|
| 标签 | `load.auto_tags` | `--tags` | 文档 tags 与过滤 tags 交集非空即匹配 |
| 领域 | `load.auto_domains` | `--domain` | 匹配 frontmatter `domain` 字段 |
| 类型 | `load.doc_types` | — | `knowledge` 或 `qa_pair` |
| 时间 | `load.time_range` | — | 相对时间（`7d`/`24h`）或绝对日期 |
| 数量 | `load.max_docs` | — | 最多 N 篇，按 `generated_at` 倒序 |
| 全量 | — | `--all` | 忽略所有过滤，拉取全部 |

各维度 AND 关系。`knowledge/shared/` 始终全量，不受过滤。

### 标签字典配置链

```
优先级（后层覆盖前层）：
  1. 框架内置标签字典    → src/team_memory/knowledge/tags/default.yaml
  2. 团队仓库标签字典    → team-memory/knowledge/.tag-dict.yaml
  3. 项目级标签字典      → ccb-annto-memory.yaml 的 knowledge.tags 段
```

### PRD 变更

- PRD.md：§3.1A 标记 review 被取代；§3.10 重写为最终设计；§4.1 新增 knowledge 配置段；§6 CLI 命令更新；§7.2 包结构更新
- PRD-CHANGELOG.md：本条目更新为最终设计

---

## 变更 V4.6.2 — auto-load 路径修复 + 提取诊断追踪

**日期**: 2026-05-09
**版本**: V4.6.1 → V4.6.2
**变更类型**: 缺陷修复 + 诊断增强

### 背景

两个问题：
1. `generate_auto_load_summary` 使用 `get_project_name()` 构造项目记忆路径，与 YAML 配置中 `project_memory.path` 的目录命名可能不一致，导致项目记忆无法加载
2. Stop hook 触发 `extract run` 后无任何诊断途径，用户无法判断是 hook 未触发还是提取无新内容

### 核心变更

**auto-load 路径修复** (`services/extract.py`):
- `generate_auto_load_summary` 优先从 `config.project_path` 提取父目录作为项目路径
- `project_path` 如为 `.md` 文件路径，取其父目录；否则直接使用
- `project_path` 为空时回退到 `get_project_name()`

**提取诊断追踪** (`services/extraction_manager.py` + `cli/extract.py`):
- `ExtractionState` 新增 `last_invocation_at` 和 `last_invocation_result` 字段
- `ExtractionManager` 新增 `record_invocation()` 方法，记录每次调用（不影响冷却计时）
- `mark_done()` / `mark_skipped()` 同步记录调用时间和结果
- `cmd_extract_run` 冷却跳过时调用 `record_invocation("cooldown: ...")`
- `cmd_extract_status` 展示 `Last invoked` + `Invoke result` 诊断信息

### 影响范围

| 文件 | 改动 |
|------|------|
| `services/extract.py` | `generate_auto_load_summary` 项目路径逻辑 |
| `services/extraction_manager.py` | `ExtractionState` 新增字段、`record_invocation`、`mark_done`/`mark_skipped` 记录 |
| `cli/extract.py` | `cmd_extract_status` 展示诊断字段、`cmd_extract_run` 冷却时记录 |

### 诊断用法

```bash
team-memory extract status
# 关注: Last invoked / Invoke result
# - (never) → Stop hook 未触发
# - extracted N → 成功提取
# - skipped: 无新记忆 → 模型判断无新内容
# - cooldown: ... → 距上次不足 60 秒
```

---

## 变更 V4.6.1 — 修复安装版污染问题（父级 YAML 泄露）

**日期**: 2026-04-30
**版本**: V4.6 → V4.6.1
**变更类型**: 缺陷修复 — 安装版与源码版行为不一致

### 背景

V4.5 已将 `find_annto_yaml()` 改为只检查当前目录（不向上遍历），源码版行为正确。但通过 `uv tool install` 安装的二进制仍为旧版（向上遍历 + `generate_annto_yaml` 生成在父目录），导致：

1. 父目录的 `ccb-annto-memory.yaml` 污染了所有子目录
2. 新项目无任何配置，`team-memory pull` 却成功拉取团队记忆

### 核心变更

- `uv tool install --reinstall` 从源码重新安装，安装版与源码版对齐
- 清理 `~/.local/share/uv/tools/ccb-team-memory/` 下的 `__pycache__` 缓存

### 预防措施

- 每次修改 ccb-team-memory 源码后，必须执行：

  ```bash
  cd ccb-team-memory && uv tool install --reinstall . && team-memory install --config-dir ~/.ccb-dev
  ```
- 考虑在 CI 中自动检测安装版与源码版的一致性

### 不涉及

- 源码（`find_annto_yaml` 行为不变）
- CLI 命令签名
- 配置格式

---

## 变更 V4.6 — 提取层完善（提取状态追踪、验证、整合）

**日期**: 2026-04-29
**版本**: V4.5 → V4.6
**变更类型**: 功能新增 + 行为增强
**参考实现**: claude-code `extractMemories.ts` + `autoDream.ts` + `memoryScan.ts`

### 背景

V4.5 提取层只具备 prompt 生成能力（`build_extract_prompt()`），缺失：
- 提取状态追踪和游标（无法知道哪些对话已处理）
- 提取后验证（不知道文件是否正确写入）
- 记忆整合机制（文件只增不减）
- auto 模式闭环（Stop hook 未注册）

本版本对照 claude-code 的实现模式全面完善提取层。

### 变更详情

#### 新增功能

| 功能 | 文件 | 对应 claude-code |
|------|------|-----------------|
| 提取状态追踪 | `services/extraction_manager.py` | `initExtractMemories()` 闭包 |
| 记忆验证 | `services/verify.py` + `cli/verify_cmd.py` | `extractWrittenPaths()` + frontmatter 校验 |
| 记忆整合 | `services/consolidation.py` + `cli/consolidate_cmd.py` | `initAutoDream()` 门控链 |

#### 提取 Prompt 增强

| 改动 | 问题 | 对应 claude-code |
|------|------|-----------------|
| `ManifestEntry` 增加 name/description/type/scope | 无法按内容去重 | `memoryScan.ts` MemoryHeader |
| `scan_manifest()` 解析 frontmatter | 只读文件名 | `scanMemoryFiles()` |
| 按类型分组展示已有记忆 | 平铺难读 | `formatMemoryManifest()` |
| session 上下文（标识+时间范围） | 不知道分析什么对话 | `runExtraction()` prompt |
| MEMORY.md 约束精确化（150字符/行、200行上限） | 索引膨胀 | `MAX_ENTRYPOINT_LINES/BYTES` |
| `get_scope_dirs()` 公共导出 | 重复实现 | `getTeamMemPath()` |

#### Hook 增强

- 新增 **Stop Hook** → `team-memory extract prompt --mode auto`，补齐 auto 模式闭环
- Push 前置集成 `verify_before_push()`

#### CLI 新增命令

- `team-memory extract history` — 查看提取历史
- `team-memory verify` — 验证记忆文件完整性
- `team-memory consolidate [--dry-run|--apply]` — 记忆整合

#### 模板更新

- `templates/extract.md` — 更新为中文，与代码内 prompt 一致
- `templates/SKILL.md` — 新增 verify、consolidate、extract history 命令

### 测试

- 新增 `test_extraction_manager.py`（14 tests）
- 新增 `test_verify.py`（13 tests）
- 全部 68 tests 通过

---

## 变更 V4.5 — find_annto_yaml 仅检查当前目录

**日期**: 2026-04-29
**版本**: V4.4 → V4.5
**变更类型**: 行为变更 — 移除向上遍历发现

### 背景

`find_annto_yaml()` 原实现从 cwd 向上逐级查找 `ccb-annto-memory.yaml`，直到文件系统根目录。这会导致在父目录存在同名 YAML 时意外匹配到非当前项目的配置，造成多项目共享的歧义。

### 核心变更

**`find_annto_yaml()`**：移除向上遍历的 while 循环，改为仅检查当前工作目录（或指定 `start` 目录）下是否存在 `ccb-annto-memory.yaml`。

**`find_project_root()`**：docstring 更新，移除 "Walks up" 表述，改为 "Checks start directory"。

**`cli/init.py`**：错误提示从 `在项目父目录创建` 改为 `在项目目录创建`。

### 影响范围

| 模块 | 变更 |
|------|------|
| `config/annto.py` | `find_annto_yaml()` 简化为直接检查；`find_project_root()` docstring 更新 |
| `cli/init.py` | 错误提示文本更新 |
| `PRD.md` | 7 处引用更新（文件位置、发现流程、验证计划等） |

### 向后兼容

已在使用中的项目，`ccb-annto-memory.yaml` 本就放置在项目根目录下，不受影响。

### 不涉及

- `load_annto_yaml()` — 行为不变
- `load_team_memory_config()` — 行为不变
- 密钥扫描、记忆提取、hooks 注册等模块

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
- `my-project/.claude/skills/team-memory/SKILL.md`：手动覆盖旧英文版为中文

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
  path: projects/my-project/  # 仓库内项目记忆目录
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
