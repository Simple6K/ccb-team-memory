"""架构提取器：从 type=project 的记忆中提取架构知识。

筛选含文件结构/设计模式/技术选型关键词的记忆，归纳为结构化架构文档。
"""

from ..base import KnowledgeExtractor


class ArchitectureExtractor(KnowledgeExtractor):
    name = "architecture"
    doc_type = "knowledge"
    domain = "architecture"
    tags = ["架构"]

    _KEYWORDS = [
        "架构", "设计模式", "技术选型", "目录结构", "文件结构",
        "模块", "分层", "依赖", "组件", "服务",
        "接口", "抽象", "解耦", "中间件",
    ]

    def input_filter(self, staging_files: list[dict]) -> list[dict]:
        """筛选 type=project 且含架构相关关键词的记忆。"""
        result = []
        for f in staging_files:
            if f.get("type") != "project":
                continue
            text = f.get("description", "") + " " + f.get("name", "")
            if any(kw in text for kw in self._KEYWORDS):
                result.append(f)
        return result

    def build_prompt(self, memories: list[dict], tag_dict: dict) -> str:
        doc_id = self.generate_doc_id(memories)
        tag_hint = self._build_tag_hint(tag_dict)
        mem_list = self._format_memory_list(memories)

        return f"""# 知识提取：架构知识

## 任务
从以下原始记忆（type=project，含架构/设计相关内容）中提取并归纳架构知识。
需要形成体系化的架构描述，而非简单罗列记忆。

## 输出结构
1. **整体架构概览**：项目的分层结构、核心模块
2. **关键设计决策**：技术选型、设计模式选择的原因
3. **模块间依赖关系**：调用链、数据流
4. **注意事项**：与架构相关的约束、约定

## 输出要求
- 文档 frontmatter 必须使用以下 doc_id: `{doc_id}`
- 内容以中文为主

## 可用标签
{tag_hint}

## 源记忆
{mem_list}

## 输出格式
直接输出完整的 Markdown 知识文档，含 frontmatter：

```yaml
---
doc_id: "{doc_id}"
type: "knowledge"
domain: "architecture"
title: "架构知识标题（用中文概括）"
business_line_id: ""
tags: ["架构", ...]       # 从可用标签中选择匹配的
source_files: [{self._format_source_files(memories)}]
source_count: {len(memories)}
generated_at: "{{current_time}}"
extractor: "architecture"
---
```

请分析源记忆，归纳架构知识，生成完整文档。
"""

    def _build_tag_hint(self, tag_dict: dict) -> str:
        lines = ["从以下标签中选择匹配的标注到文档（只能选择，不能新建）："]
        for dimension, tags in tag_dict.items():
            if tags:
                lines.append(f"- {dimension}: {', '.join(tags)}")
        return "\n".join(lines)

    def _format_memory_list(self, memories: list[dict]) -> str:
        lines = []
        for i, m in enumerate(memories, 1):
            name = m.get("name", "未命名")
            desc = m.get("description", "")
            path = m.get("path", "")
            lines.append(f"{i}. [{name}] — {desc}（来源: {path}）")
        return "\n".join(lines)

    def _format_source_files(self, memories: list[dict]) -> str:
        paths = [str(m.get("path", "")) for m in memories]
        return ", ".join(f'"{p}"' for p in paths if p)
