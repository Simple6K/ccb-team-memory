# 团队记忆提取逻辑文档

> 自动生成于 2026-04-29 | ccb-team-memory V4.6

## 一、提取触发模式

| 模式 | 触发方式 | 对应 claude-code |
|------|----------|-----------------|
| `manual` | 用户显式执行 `/team-memory extract prompt` | — |
| `instruction`（默认） | Skill 注入提取指令，模型按指令提取 | — |
| `auto` | **Stop Hook** 每轮对话结束后自动触发 | `stopHooks.ts` → `executeExtractMemories()` |

### Auto 模式完整流程

```
对话轮次结束
  → Stop Hook 触发
    → team-memory extract prompt --mode auto
      → ExtractionManager 加载状态（.extract-state.json）
      → 门控检查（should_run）
      → 互斥检测（detect_writes_since_last — 主模型是否已自行写入）
      → scan_manifest() 扫描已有记忆（解析 frontmatter）
      → build_extract_prompt() 生成提取 prompt
      → 输出到 stdout → ccb 注入为 additionalContext
  → 下一轮对话开始
    → 模型看到提取状态 + 已有记忆清单 + 提取指令
    → 模型分析对话，提取新记忆
    → 模型 Write 记忆文件到 .claude/team-memory/
  → PostToolUse Hook 触发
    → team-memory push（自动验证 + 提交 + 推送）
```

## 二、提取 Prompt 内容结构

`build_extract_prompt()` 生成的 prompt 包含以下部分：

### 2.1 提取状态上下文（V4.6 新增）

```
## 提取状态
- 上次提取时间: 2026-04-29T16:00:00+08:00
- 累计提取次数: 42
- 团队记忆数: 15，项目记忆数: 27
- 上次提取写入: user_prefs.md, feedback_testing.md

会话标识: `<project>@<timestamp>`
分析范围: 从上次提取时间至今的新对话内容
```

对应 claude-code: `runExtraction()` prompt 中的 `newMessageCount`

### 2.2 目标目录

根据 `config.extract.scope` 解析：
- `team` → `.claude/team-memory/shared/`
- `project` → `.claude/team-memory/projects/<name>/`
- `all` → 两者

### 2.3 四种记忆类型定义（XML 格式）

```xml
<types>
  <type>
    <name>user</name>
    <scope>默认: team</scope>
    <description>团队成员的角色、偏好和知识背景</description>
    <when_to_save>了解用户角色、偏好、职责或知识时</when_to_save>
    <how_to_use>定制协作方式</how_to_use>
    <examples>...</examples>
  </type>
  <type>
    <name>feedback</name>
    <scope>team > project</scope>
    <description>经验教训和纠正，成功和失败都要记录</description>
    <body_structure>规则 + **原因：** + **如何应用：**</body_structure>
    <examples>...</examples>
  </type>
  <type>
    <name>project</name>
    <scope>project > team</scope>
    <description>架构决策、约束、里程碑。相对日期→绝对日期</description>
    <examples>...</examples>
  </type>
  <type>
    <name>reference</name>
    <scope>team</scope>
    <description>外部系统信息指针</description>
    <examples>...</examples>
  </type>
</types>
```

对应 claude-code: `TYPES_SECTION_COMBINED`（memoryTypes.ts:37）

### 2.4 排除规则

- 代码片段或源文件内容
- 会话特定临时上下文
- CLAUDE.md 中已有信息
- 敏感数据（API 密钥、令牌、密码）
- 临时调试状态
- Git 分支名和 PR 号
- 可通过链接引用的文档原文
- **即使用户明确要求保存也适用**（需追问意外/非显而易见的点）

对应 claude-code: `WHAT_NOT_TO_SAVE_SECTION`（memoryTypes.ts:183）

### 2.5 文件格式

```yaml
---
name: 简短名称
description: 一句话描述
type: user|feedback|project|reference
scope: team|project
created: YYYY-MM-DD
extracted_at: YYYY-MM-DDTHH:mm:ss+08:00
contributor: 提取人
---
```

### 2.6 已有记忆清单（V4.6 增强）

由 `scan_manifest()` + `_format_manifest_grouped()` 生成：

```
## 已有记忆（按类型分组，含描述 — 用于去重判断）

### 团队 (shared/)（2 条）

### user
- `user_team_toolchain.md` — 团队开发工具链偏好：bun、Python 3.13+、uv

### feedback
- `feedback_ccb_team_memory.md` — ccb-team-memory 集成注意事项
```

对应 claude-code: `scanMemoryFiles()` + `formatMemoryManifest()`（memoryScan.ts）

**关键特性**：
- 解析每个文件的 YAML frontmatter 读取 `name`/`description`/`type`
- 按 `type` 分组展示（user → feedback → project → reference）
- 显示 description 帮助模型判断去重
- 不再截断（每目录最多 200 条，全量展示）
- 排除 `.git/` 和 `MEMORY.md`

### 2.7 保存指令（V4.6 增强）

```
保存分为两步：
1. 写入记忆文件（含 extracted_at + contributor）
2. 更新 MEMORY.md 索引

MEMORY.md 约束：
- 每条索引 ≤ 150 字符
- 按类型分组
- 总行数 ≤ 200 行
- 更新时保持分组顺序
- 删除时同步移除索引
```

对应 claude-code: `MAX_ENTRYPOINT_LINES = 200`, `MAX_ENTRYPOINT_BYTES = 25000`

### 2.8 提取任务指令

```
1. 回顾上述对话，找出可提取的知识
2. 归类为四种记忆类型
3. 确定范围（team 还是 project）
4. 先检查已有记忆清单：如重复则更新现有文件
5. 写入文件
6. 更新 MEMORY.md 索引
```

## 三、提取状态管理（V4.6 新增）

### ExtractionManager

对应 claude-code: `initExtractMemories()` 闭包（extractMemories.ts:296）

**数据结构** (`.claude/team-memory/.extract-state.json`):
```json
{
  "last_extraction_at": "2026-04-29T16:00:00+08:00",
  "total_extractions": 42,
  "last_files_written": ["user_prefs.md", "feedback_testing.md"],
  "team_count": 15,
  "project_count": 27
}
```

**核心方法**：

| 方法 | 对应 claude-code | 说明 |
|------|-----------------|------|
| `should_run(mode)` | 门控检查链 | auto 模式检查 in_progress + 目录存在 |
| `detect_writes_since_last()` | `hasMemoryWritesSince()` | 检测主模型是否已自行写入记忆 |
| `mark_start()` | `inProgress = true` | 标记提取开始 |
| `mark_done(files)` | `lastMemoryMessageUuid = lastMessage.uuid` | 推进游标 |
| `mark_skipped(reason)` | 游标推进但不计数 | 互斥跳过 |
| `stash_pending(ctx)` / `pop_pending()` | `pendingContext` | 合并暂存模式 |
| `get_summary_for_prompt()` | prompt 中的 `newMessageCount` | 生成状态上下文 |

## 四、提取后流程

### 4.1 验证（verify）

**命令**: `team-memory verify`

对应 claude-code: `extractWrittenPaths()` + frontmatter 校验

检查项：
- frontmatter 必需字段（name, description, type）
- type 值合法性
- MEMORY.md 条目一致性
- 重复 name 检测
- 孤立索引/未索引文件

### 4.2 Push 前置验证

`do_push()` 调用前自动执行 `verify_before_push()`。
验证失败 → push 被阻止（`--force` 可跳过）。

### 4.3 整合（consolidate）

**命令**: `team-memory consolidate [--dry-run|--apply]`

对应 claude-code: `initAutoDream()` 门控链（autoDream.ts:123）

```
门控顺序: 时间（24h）→ 文件数（10）→ 锁
扫描候选: 合并相似记忆 / 归档过时记忆 / 修复索引
```

## 五、与 claude-code 原生系统的对应关系

| ccb-team-memory | claude-code |
|----------------|-------------|
| `ExtractionManager` | `initExtractMemories()` 闭包 |
| `ExtractionState.last_extraction_at` | `lastMemoryMessageUuid` |
| `detect_writes_since_last()` | `hasMemoryWritesSince()` |
| `stash_pending()` / `pop_pending()` | `pendingContext` |
| `scan_manifest()` | `scanMemoryFiles()` |
| `_format_manifest_grouped()` | `formatMemoryManifest()` |
| `build_extract_prompt()` | `buildExtractCombinedPrompt()` |
| `verify_before_push()` | `extractWrittenPaths()` 后校验 |
| `ConsolidationManager` | `initAutoDream()` 闭包 |
| `should_run()` (consolidation) | autoDream 门控链 |
| `_acquire_lock()` / `_release_lock()` | `consolidationLock.ts` |
| `cmd_extract_prompt` | `executeExtractMemories()` |
| Stop hook | `stopHooks.ts` 中的调用 |
| PostToolUse hook (push) | `watcher.ts` 中的 `notifyTeamMemoryWrite()` |

**关键区别**：
- claude-code 通过 `runForkedAgent()` 创建上下文继承的 fork 执行提取
- ccb-team-memory 生成 prompt 注入当前模型上下文，由模型自行执行提取
- claude-code 使用 API sync（OAuth），ccb-team-memory 使用 Git sync
