"""需求提取器：从 type=project/reference 的记忆中提取需求知识。

筛选含需求澄清/业务理解关键词的记忆，归纳为结构化需求文档。
"""

from ..base import KnowledgeExtractor


class RequirementsExtractor(KnowledgeExtractor):
    name = "requirements"
    doc_type = "knowledge"
    domain = "requirements"
    tags = ["需求"]

    _KEYWORDS = [
        "需求", "业务", "产品", "澄清", "确认",
        "理解", "规则", "合规", "法律", "约束",
        "业务逻辑", "场景", "用例", "用户故事",
    ]

    def input_filter(self, staging_files: list[dict]) -> list[dict]:
        """筛选 type=project 或 reference 且含需求/业务关键词的记忆。"""
        result = []
        for f in staging_files:
            ftype = f.get("type", "")
            if ftype not in ("project", "reference"):
                continue
            text = f.get("description", "") + " " + f.get("name", "")
            if any(kw in text for kw in self._KEYWORDS):
                result.append(f)
        return result

    def build_prompt(self, memories: list[dict], tag_dict: dict) -> str:
        doc_id = self.generate_doc_id(memories)
        tag_hint = self._build_tag_hint(tag_dict)
        mem_list = self._format_memory_list(memories)

        return f"""# 知识提取：需求知识

## 任务
从以下原始记忆（含需求澄清/业务理解相关内容）中提取并归纳需求知识。

## 输出结构
1. **业务背景**：相关的业务场景、领域知识
2. **需求澄清**：重要的需求确认、边界条件、约束
3. **与架构/流程的关联**：需求如何影响技术决策
4. **待确认/已知变更**：尚未确定的需求点、计划中的变更

## 输出要求
- 文档 frontmatter 必须使用以下 doc_id: `{doc_id}`

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
domain: "requirements"
title: "需求知识标题（用中文概括）"
business_line_id: ""
tags: ["需求", ...]
source_files: [{self._format_source_files(memories)}]
source_count: {len(memories)}
generated_at: "{{current_time}}"
extractor: "requirements"
---
```

请分析源记忆，归纳需求知识，生成完整文档。
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
