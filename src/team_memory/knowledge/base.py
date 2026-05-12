"""知识提取器抽象基类。

每个提取器负责一类知识的归纳逻辑：
筛选哪些原始记忆 → 构建什么样的提示词 → 输出什么格式的知识文档。
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path


class KnowledgeExtractor(ABC):
    """知识提取器抽象基类。

    子类必须定义 name、doc_type、domain，并实现三个核心方法。
    新增提取器 = 在 extractors/ 目录加一个 .py 文件，框架自动发现。
    """

    # ── 类属性（子类必须覆盖） ──

    name: str = ""          # 提取器标识（如 "qa", "architecture"）
    doc_type: str = ""      # "knowledge" | "qa_pair"
    domain: str = ""        # 知识领域（如 "architecture", "workflow"）
    tags: list[str] = []    # 默认标签（从配置注入）

    # ── 抽象方法 ──

    @abstractmethod
    def input_filter(self, staging_files: list[dict]) -> list[dict]:
        """从 _staging/ 中筛选本提取器处理的文件。

        Args:
            staging_files: 每个元素含:
                path (Path): 文件路径
                name (str): frontmatter name
                description (str): frontmatter description
                type (str): frontmatter type
                scope (str): frontmatter scope

        Returns:
            匹配的文件列表（子集），供 build_prompt 使用。
        """
        ...

    @abstractmethod
    def build_prompt(self, memories: list[dict], tag_dict: dict) -> str:
        """构建归纳提示词。

        Args:
            memories: 已筛选的记忆列表，每项含 path + frontmatter 字段
            tag_dict: 合并后的标签字典（框架 + 团队仓库 + 项目级）。
                      AI 只能从已有标签中选择匹配的标注到文档。

        Returns:
            提示词文本，将作为 API 调用的 system prompt。
            提示词应包含 doc_id 指令（由 generate_doc_id 预生成），
            要求 AI 在 frontmatter 中使用该值。
        """
        ...

    def generate_doc_id(self, memories: list[dict]) -> str:
        """生成稳定的文档 ID，用于 upsert。

        默认实现：对源文件路径列表排序后做 sha256，取前 12 位 hex。
        保证相同源文件集合 → 相同 doc_id → upsert 覆盖。
        子类可覆盖以实现不同的 ID 生成策略。

        Args:
            memories: 源记忆列表

        Returns:
            12 位 hex 字符串
        """
        paths = sorted(m.get("path", "") for m in memories)
        joined = "\n".join(paths)
        return hashlib.sha256(joined.encode()).hexdigest()[:12]
