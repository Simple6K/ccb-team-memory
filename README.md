# ccb-team-memory

企业级团队记忆同步与提取系统。基于 Git 的跨项目团队知识管理工具。

通过 AI 从对话中自动提取记忆，支持二级知识归纳，通过 Git 仓库跨项目同步共享。

## 特性

- **AI 记忆提取** — 从对话中自动提取结构化记忆，支持团队/项目两级范围
- **二级知识归纳** — 对原始记忆进行 AI 二次提取，生成结构化知识文档（踩坑记录、架构文档等）
- **Git 驱动同步** — 多项目共享同一 Git 仓库，pull/push 即可同步团队记忆
- **零外部依赖** — 纯 Python 3.13 标准库实现，仅需 git CLI
- **标签系统** — 多维标签用于知识发现和按需拉取
- **审核机制** — 基于 unpushed commit 的 review 流程，审核后推送发布
- **密钥扫描** — push 前自动扫描，防止敏感信息泄露

## 安装

```bash
# 通过 pip 安装
pip install ccb-team-memory

# 直接使用 uv
uv tool install ccb-team-memory

# 从源码安装
git clone https://github.com/Simple6K/ccb-team-memory.git
cd ccb-team-memory
uv tool install .

# 安装到 ccb（可选）
team-memory install --config-dir ~/.ccb-dev
```

## 快速开始

### 1. 初始化项目

在项目根目录创建 `ccb-annto-memory.yaml`：

```bash
team-memory init --generate-yaml \
  --team-repo git@github.com:your-org/team-memories.git \
  --project-repo git@github.com:your-org/team-memories.git
```

或参考 `ccb-annto-memory.example.yaml` 手动创建。

### 2. 拉取团队记忆

```bash
team-memory pull
```

### 3. 提取记忆

```bash
# 自动提取对话中的记忆（需配置 hook）
team-memory extract run

# 生成提取 prompt（手动模式）
team-memory extract prompt
```

### 4. 知识归纳

```bash
# 从 _staging/ 中提取知识文档（取代 review/approve 流程）
team-memory knowledge extract

# 审核待发布的知识文档
team-memory knowledge review list

# 批准发布
team-memory knowledge review approve
```

## CLI 命令总览

```
team-memory
├── init             初始化项目团队记忆
├── pull             拉取最新团队记忆
├── push             推送本地变更
├── scan             密钥扫描
├── status           查看状态
├── extract          记忆提取
│   ├── prompt       生成提取 prompt
│   ├── run          运行自动提取
│   ├── status       查看提取状态
│   ├── history      查看提取历史
│   └── batch        批量历史会话提取（交互选择 + 时间筛选 + 断点续传，V4.11）
├── load             记忆加载
│   ├── auto         自动加载记忆摘要
│   └── search       搜索并加载记忆
├── review           待提取记忆审核（V4.10 起由 knowledge extract 取代）
├── knowledge        知识模块系统
│   ├── extract      运行知识提取
│   ├── pull         拉取知识文档到 shared/projects
│   ├── review       审核知识文档变更
│   │   ├── list     列出未发布的知识 commit
│   │   ├── show     显示指定 commit 详情
│   │   ├── approve  发布所有待审 commit
│   │   └── reject   撤销指定 commit
│   ├── list         列出知识模块
│   ├── show         显示模块完整内容
│   ├── status       模块统计
│   └── clean        清理模块
├── install          安装 hooks 和 Skill 到 ccb
└── uninstall        移除 hooks
```

## 配置

通过项目根目录的 `ccb-annto-memory.yaml` 配置：

```yaml
# 团队记忆（跨项目共享）
team_memory:
  repo: git@github.com:your-org/team-memories.git
  path: shared/

# 项目记忆（本项目专属）
project_memory:
  repo: git@github.com:your-org/team-memories.git
  path: projects/your-project/

# 项目身份（push 必填）
project:
  url: git@github.com:your-org/your-project.git

# 知识模块配置
knowledge:
  path: "knowledge/"                     # 远程仓库内的知识文档路径
  extractors:
    enabled: [qa, architecture]          # 启用的提取器
  load:
    auto_tags: [研发]                    # SessionStart 自动拉取标签
    auto_domains: [architecture, qa]    # SessionStart 自动拉取领域
    doc_types: [knowledge, qa_pair]
    time_range:
      since: "7d"
    max_docs: 8
  tags: {}                               # 项目级标签扩展
```

## 架构

```
ccb-team-memory/
├── src/team_memory/
│   ├── cli/          # CLI 接口层（argparse）
│   ├── services/     # 业务逻辑层（提取、加载、同步等）
│   ├── knowledge/    # 知识模块层（二级提取框架）
│   │   ├── base.py           # KnowledgeExtractor ABC
│   │   ├── registry.py       # 提取器自动发现
│   │   ├── runner.py         # 提取与拉取编排
│   │   ├── store.py          # 知识文档读写 + 索引
│   │   ├── tags/             # 标签字典
│   │   └── extractors/       # 可扩展提取器
│   ├── config/       # 配置层（YAML 解析 + 数据模型）
│   ├── utils/        # 工具层（git 操作等）
│   └── templates/    # 静态模板
└── tests/
```

### 两层提取架构

```
一级提取（对话 → 原始记忆）
  对话 transcript
    → Stop hook: extract run
    → AI 提取结构化记忆
    → 写入 _staging/

二级提取（记忆 → 知识文档）
  team-memory knowledge extract
    → 读取 _staging/ 待审核记忆
    → 提取器分类筛选
    → AI 归纳为知识文档
    → 写入 knowledge/ → git commit

知识拉取（注入项目）
  team-memory knowledge pull
    → 按标签/领域/时间过滤
    → 注入 shared/ 和 projects/
    → load auto 照常读取
```

## 知识提取器

内置 4 个提取器，可扩展：

| 提取器 | 输出类型 | 来源 | 说明 |
|--------|---------|------|------|
| QA | qa_pair | feedback 记忆 | 架构理解、流程困惑 → Q&A |
| 架构 | knowledge | project 记忆 | 文件结构、设计模式、技术选型 |
| 流程 | knowledge | project 记忆 | 开发流程、部署、发布 |
| 需求 | knowledge | project/reference | 需求澄清、业务理解 |

新增提取器 = 在 `src/team_memory/knowledge/extractors/` 目录新增一个 `.py` 文件，框架自动发现。

## 标签字典配置链

```
优先级（后层覆盖前层）：
  1. 框架内置标签字典    → src/team_memory/knowledge/tags/default.yaml
  2. 团队仓库标签字典    → team-memory/knowledge/.tag-dict.yaml
  3. 项目级标签扩展      → ccb-annto-memory.yaml 的 knowledge.tags 段
```

标签完全由人工定义，AI 仅从已有标签中选择，不生成新标签。

## 许可证

MIT License

## 技术栈

- Python 3.13+（标准库，零外部依赖）
- Git CLI（subprocess 调用）
- Anthropic API（兼容 DeepSeek 端点）
