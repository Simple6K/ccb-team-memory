---
name: team-memory
description: 企业团队记忆同步与提取。通过 Git 同步管理共享团队知识，从对话中提取记忆，扫描密钥，加载团队上下文。
user-invocable: true
allowed-tools: [Bash, Read, Write, Edit, Glob, Grep]
argument-hint: "<命令> [参数]"
---

# 团队记忆

管理 ccb 的企业团队记忆。可用命令：

- `/team-memory init --repo <url>` — 初始化项目团队记忆
- `/team-memory pull` — 拉取最新团队记忆
- `/team-memory push` — 推送本地变更（自动验证 + 密钥扫描）
- `/team-memory scan` — 扫描密钥
- `/team-memory status` — 查看同步状态
- `/team-memory extract prompt` — 生成提取提示词
- `/team-memory extract run` — 运行自动记忆提取（异步 agent loop）
- `/team-memory extract status` — 查看提取配置
- `/team-memory extract history` — 查看提取历史
- `/team-memory verify` — 验证记忆文件完整性
- `/team-memory consolidate` — 整合和清理记忆
- `/team-memory load [查询]` — 搜索并加载团队记忆
- `/team-memory install` — 安装 hooks 和自动同步

## 使用方式

调用时通过 Bash 执行对应的 `team-memory` CLI 命令：

```bash
team-memory <命令> <参数>
```

`extract`：CLI 输出提取提示词，按提示从当前对话中识别、分类、保存记忆。

`verify`：检查 frontmatter 完整性、MEMORY.md 一致性、重复检测。

`consolidate`：检测可合并的相似记忆、归档过时记忆、修复索引不一致。

`load`：CLI 输出已加载的记忆内容，用于指导当前工作。

团队记忆存储在 `.claude/team-memory/`，通过 Git 同步。
