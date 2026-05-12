# ccb-team-memory 现状与未来规划报告

**日期**: 2026-05-09 | **版本**: V4.6.2

---

## 一、项目概述

ccb-team-memory 是一个企业级团队记忆管理系统，通过 Git 同步 + AI 提取 + 人工审核，实现跨项目/跨成员的团队知识沉淀。

### 架构

```
src/team_memory/
├── cli/              接口层（10 文件）— argparse 定义，零业务逻辑
├── services/         业务层（11 文件）— 核心领域逻辑
├── config/           配置层（2 文件）— YAML 解析 + 数据模型
├── utils/            工具层（2 文件）— git 子进程 + transcript 读取
└── templates/        资源层（2 文件）— SKILL.md + extract.md
```

单向依赖：`cli → services → {config, utils}`

### 部署模式

- uv tool install（全局 CLI）+ editable .pth（源码热更新）
- 全局 hooks 注册到 `~/.ccb-dev/settings.json`
- 项目配置通过 `ccb-annto-memory.yaml` 自动发现

---

## 二、功能矩阵

### 2.1 CLI 命令

```
team-memory
├── init                初始化项目团队记忆
├── pull                拉取最新团队/项目记忆
├── push                推送本地变更（前置 scan 密钥扫描）
├── scan                扫描密钥
├── status              显示配置与同步状态
├── extract
│   ├── prompt          生成提取提示词（手动/指令/自动）
│   ├── status          显示提取状态与诊断信息
│   ├── history         查看提取历史
│   └── run             自动提取（Stop hook 触发入口）
├── load
│   ├── auto            生成自动加载摘要（SessionStart hook）
│   └── manual [查询]   搜索并加载特定记忆
├── install             注册 hooks + skill 到 ccb-dev
├── uninstall           移除 hooks
├── verify              验证记忆文件完整性（frontmatter 校验）
├── consolidate         记忆整合（合并/归档/修复索引）
│   ├── --dry-run       预览模式
│   └── --apply         执行
└── review              审核 _staging/ 待审核记忆
    ├── list            列出待审核
    ├── approve <N>     批准单条 / --all 全部
    ├── reject <N>      拒绝单条 / --all 全部
    └── integrate       批量整合远程增量 → git commit（不 push）
```

### 2.2 Hooks 体系

| Hook | 触发时机 | 命令 | 状态 |
|------|---------|------|------|
| SessionStart | ccb-dev 启动 | `pull --quiet && load auto` | 正常 |
| PostToolUse | Write/Edit 工具调用后 | `push --quiet` | 正常 |
| **Stop** | **每次模型响应后** | **`extract run`** | 待验证 |
| SessionEnd | 会话结束时 | `push --quiet` | 正常 |

### 2.3 核心流程

```
ccb-dev 启动
  → SessionStart: pull + load auto（自动注入团队记忆到上下文）
  
每次模型响应
  → Stop: extract run
    → 读取 session transcript
    → API 调用 AI 模型分析对话
    → 写入新记忆到 _staging/
    → 条件 push（≥5 文件 或 ≥30 分钟）
    
管理员定期
  → review list（查看待审核）
  → review integrate（批量去重合并 → git commit）
  → 审核 commit 后手动 push
```

---

## 三、当前状态评估

### 3.1 已完成的版本演进

| 版本 | 内容 |
|------|------|
| V4.1 | ccb-annto-memory.yaml 自动发现机制 |
| V4.2 | 项目身份强校验，取消 git 限制 |
| V4.3 | 全面中文化 |
| V4.3.1 | Skill 全局安装 + 嵌入式模板中文化 |
| V4.3.2 | 记忆可追溯性（extracted_at + contributor） |
| V4.4 | 五层模块化架构重构 |
| V4.5 | find_annto_yaml 仅检查当前目录 |
| V4.6 | 提取层完善（状态追踪、验证、整合） |
| V4.6.1 | 修复安装版污染问题 |
| V4.6.2 | auto-load 路径修复 + 提取诊断追踪 |

### 3.2 测试覆盖

| 模块 | 测试数 | 覆盖状态 |
|------|--------|---------|
| test_config.py | 11 | ✅ |
| test_extract.py | 15 | ✅ |
| test_extraction_manager.py | 14 | ✅ |
| test_scanner.py | 15 | ✅ |
| test_verify.py | 13 | ✅ |
| **agent_loop.py** | **0** | ❌ 核心提取引擎 |
| **integration.py** | **0** | ❌ Stage 3 核心 |
| **sync.py** | **0** | ❌ |
| **loader.py** | **0** | ❌ |
| **installer.py** | **0** | ❌ |
| **consolidation.py** | **0** | ❌ |
| **api_client.py** | **0** | ❌ |
| **annto.py** | **0** | ❌ |
| **transcript.py** | **0** | ❌ |
| **git.py** | **0** | ❌ |
| **所有 CLI 命令** | **0** | ❌ |
| **总计** | **68** | **约 25% 模块有测试** |

---

## 四、缺口清单（按优先级）

### P0 — 阻塞性缺口

| # | 内容 | 影响 | 工作量 |
|---|------|------|--------|
| 1 | **Stop hook 可靠性验证** | 核心自动化链路是否真正运行不清楚，诊断字段已加但需实际验证 | S |
| 2 | **review approve 不更新 MEMORY.md** | 审核批准后索引过期，需手动运行 verify→consolidate，流程断裂 | M |
| 3 | **agent_loop.py 零测试** | 核心提取引擎，每次改动都有回归风险，只能手动测 | M |

### P1 — 重要缺口

| # | 内容 | 影响 | 工作量 |
|---|------|------|--------|
| 4 | **review integrate 端到端未验证** | Stage 3 成果，integration.py 已写但从未跑过完整流程 | M |
| 5 | **integration.py 零测试** | Stage 3 核心逻辑，整合去重/合并/提交无自动化保护 | M |
| 6 | **提取冷却 60s 硬编码** | 调试期想缩短、正式期想延长，无法调整 | S |
| 7 | **API 错误静默吞掉** | `api_client.py` 返回 error dict 而非 raise，上层无感知 | S |
| 8 | **模型选择 fallback** | `deepseek-v4-flash` 不可用，已改为 `deepseek-v4-pro` 兜底 | S（已修复） |

### P2 — 体验与稳定性

| # | 内容 | 影响 | 工作量 |
|---|------|------|--------|
| 9 | **CI/CD 完全缺失** | 无自动测试/格式化/类型检查，全靠手动 | L |
| 10 | **无结构化日志** | 调试靠 `--verbose` stderr，无分级/无持久化 | M |
| 11 | **改代码后必须 uv tool install** | 虽然是 editable 但二进制路径可能缓存旧代码 | S |
| 12 | **review approve 后无 git commit** | 审核通过但只移文件，不做 commit，历史难追溯 | M |

### P3 — 长期演进

| # | 内容 | 影响 | 工作量 |
|---|------|------|--------|
| 13 | 记忆过期/归档机制 | 记忆只增不减，长期膨胀 | L |
| 14 | 记忆质量反馈（使用率统计） | 无法判断提取策略是否有效 | L |
| 15 | 提取去重前置（提取阶段即去重） | 当前去重在 integrate 阶段，_staging 可能积压大量重复 | M |
| 16 | 项目级 hook 开关 | 无法对特定项目禁用自动提取，非团队项目也会触发（浪费 API 调用） | M |
| 17 | 跨项目记忆共享/复用 | shared/ 机制已存在，但缺乏自动发现相关项目记忆的能力 | L |
| 18 | dev 模式热重载 | 改善开发效率 | M |
| 19 | 发送通知（整合冲突等） | 多人团队场景 | M |

---

## 五、推荐路线图

### Phase A：稳定化（当前 → 1 周）

```
目标：核心链路可信、可观测、可回滚

1. Stop hook 端到端验证（利用诊断字段在真实 session 中确认）
2. review approve 后自动更新 MEMORY.md
3. agent_loop 核心路径单元测试（mock API）
4. 提取冷却改为可配置项（--cooldown 或 settings.json 字段）
```

### Phase B：Stage 3 收尾（1-2 周）

```
目标：审核→整合→提交 完整闭环

5. review integrate 端到端测试 + dry-run 验证
6. integration.py 单元测试（去重/合并/提交信息生成）
7. review approve 执行 git commit
8. CI 骨架（GitHub Actions: lint + test）
```

### Phase C：规模化（2-4 周）

```
目标：多人团队可用

9. 结构化日志（JSON 格式，按天轮转）
10. 项目级 hook 开关
11. 提取去重前置
12. 通知机制（整合冲突等）
13. 记忆使用率统计
```

---

## 六、技术债清单

| 项 | 说明 |
|----|------|
| `find_project_root` 不走目录树 | 依赖 CWD 巧合匹配，已按设计如此但脆弱 |
| `_parse_frontmatter` 简单手写解析器 | 不处理引号/多行值，仅支持 `key: value` |
| `strip_metadata_fields` 用正则 | 对嵌套 YAML 可能有边缘情况 |
| `read_recent_messages` 不传 `since` 游标 | 每次都读最近 100 条，效率低 |
| 无 `TEAM_MEMORY_EXTRACT_MODEL` 文档 | 自定义模型的环境变量未在文档中说明 |
| `consolidation.py` 依赖 `verify.py` 的私有函数 | 跨模块私有函数引用，重构风险 |
