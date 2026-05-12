"""流程提取器：从 type=project 的记忆中提取流程知识。

筛选含开发流程/部署/发布关键词的记忆，归纳为结构化流程文档。
"""

from ..base import KnowledgeExtractor


class WorkflowExtractor(KnowledgeExtractor):
    name = "workflow"
    doc_type = "knowledge"
    domain = "workflow"
    tags = ["流程"]

    _KEYWORDS = [
        "流程", "部署", "发布", "上线", "开发流程",
        "checklist", "步骤", "前置", "审核", "审批",
        "合并", "分支", "版本", "回滚", "测试流程",
        "CI", "CD", "流水线", "构建",
    ]

    def input_filter(self, staging_files: list[dict]) -> list[dict]:
        """筛选 type=project 且含流程相关关键词的记忆。"""
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

        return f"""# 知识提取：流程知识

## 任务
从以下原始记忆（type=project，含开发流程/部署/发布相关内容）中提取并归纳流程知识。

## 输出结构
1. **流程概述**：涉及的流程类型（开发、测试、部署、发布等）
2. **关键步骤**：每个流程的步骤、前置条件、触发条件
3. **注意事项/checklist**：容易遗漏或出错的环节
4. **工具与命令**：流程中使用的关键命令或工具

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
domain: "workflow"
title: "流程知识标题（用中文概括）"
business_line_id: ""
tags: ["流程", ...]
source_files: [{self._format_source_files(memories)}]
source_count: {len(memories)}
generated_at: "{{current_time}}"
extractor: "workflow"
---
```

请分析源记忆，归纳流程知识，生成完整文档。
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
