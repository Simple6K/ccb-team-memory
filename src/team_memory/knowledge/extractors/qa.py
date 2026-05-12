"""QA 提取器：从 type=feedback 的记忆中提取踩坑记录。

筛选含架构理解/流程困惑关键词的 feedback 记忆，
归纳为 Q&A 结构化文档。
"""

from ..base import KnowledgeExtractor


class QAExtractor(KnowledgeExtractor):
    name = "qa"
    doc_type = "qa_pair"
    domain = "qa"
    tags = ["踩坑"]

    # 筛选关键词
    _KEYWORDS = [
        "不理解", "不清楚", "搞混", "混淆", "误解",
        "以为", "错误理解", "理解偏差", "困惑",
        "架构", "模块", "关联", "依赖", "流程",
        "前置条件", "触发条件", "调用链",
    ]

    def input_filter(self, staging_files: list[dict]) -> list[dict]:
        """筛选 type=feedback 且含困惑/架构理解关键词的记忆。"""
        result = []
        for f in staging_files:
            if f.get("type") != "feedback":
                continue
            text = f.get("description", "") + " " + f.get("name", "")
            if any(kw in text for kw in self._KEYWORDS):
                result.append(f)
        return result

    def build_prompt(self, memories: list[dict], tag_dict: dict) -> str:
        """构建 QA 提取 prompt。"""
        doc_id = self.generate_doc_id(memories)
        tag_hint = self._build_tag_hint(tag_dict)

        mem_list = self._format_memory_list(memories)

        return f"""# 知识提取：踩坑记录（QA 对）

## 任务
从以下原始记忆（type=feedback，含架构理解/流程困惑相关内容）中提取踩坑记录。
每个踩坑记录以 Q&A 格式组织：
- **Q**: 遇到的具体问题（对什么不理解、有什么困惑、搞混了什么）
- **A**: 正确的理解或解决方案

## 输出要求
- 将所有踩坑记录写入一份知识文档
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
type: "qa_pair"
domain: "qa"
title: "踩坑记录标题（用中文概括）"
business_line_id: ""
tags: ["踩坑", ...]       # 从可用标签中选择匹配的（多选）
source_files: [{self._format_source_files(memories)}]
source_count: {len(memories)}
generated_at: "{{current_time}}"
extractor: "qa"
---
```

请分析源记忆，提取踩坑记录，生成完整的知识文档。
"""

    def _build_tag_hint(self, tag_dict: dict) -> str:
        """构建标签选择提示。"""
        lines = ["从以下标签中选择匹配的标注到文档（只能选择，不能新建）："]
        for dimension, tags in tag_dict.items():
            if tags:
                lines.append(f"- {dimension}: {', '.join(tags)}")
        return "\n".join(lines)

    def _format_memory_list(self, memories: list[dict]) -> str:
        """格式化记忆列表。"""
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
