"""知识文档读写 + KNOWLEDGE.md 索引维护。

支持：
- 按 doc_id 写入（upsert：相同 doc_id 覆盖，新 doc_id 新增）
- KNOWLEDGE.md 索引自动更新
- 按过滤条件扫描匹配文档
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from dataclasses import dataclass, field


# ─── 知识文档 frontmatter 解析 ────────────────────────────────────────────

@dataclass
class KnowledgeDoc:
    """知识文档的完整信息。"""
    path: Path               # 相对于 knowledge/ 的文件路径
    doc_id: str = ""         # 稳定 ID
    doc_type: str = ""       # "knowledge" | "qa_pair"
    domain: str = ""         # 知识领域
    title: str = ""          # 文档标题
    business_line_id: str = ""  # 业务线 ID（预留）
    tags: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    source_count: int = 0
    generated_at: str = ""
    extractor: str = ""


def _parse_knowledge_frontmatter(content: str) -> dict:
    """解析知识文档的 YAML frontmatter。"""
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not m:
        return {}
    result: dict = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # tags 是列表格式
            if key == "tags":
                # 简单解析: tags: [tag1, tag2] 或 YAML 列表
                value = value.strip("[] ")
                result[key] = [t.strip().strip("'\"") for t in value.split(",") if t.strip()]
            elif key == "source_files":
                value = value.strip("[] ")
                result[key] = [t.strip().strip("'\"") for t in value.split(",") if t.strip()]
            elif key == "source_count":
                try:
                    result[key] = int(_strip_yaml_quotes(value))
                except ValueError:
                    result[key] = 0
            else:
                result[key] = _strip_yaml_quotes(value)
    return result


def _strip_yaml_quotes(s: str) -> str:
    """去掉 YAML 值的引号包裹。'\"abc\"' → 'abc'。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def scan_knowledge_docs(knowledge_dir: Path) -> list[KnowledgeDoc]:
    """扫描 knowledge/ 目录中所有知识文档。

    Args:
        knowledge_dir: knowledge/ 目录路径

    Returns:
        KnowledgeDoc 列表（按 generated_at 倒序）
    """
    docs: list[KnowledgeDoc] = []
    if not knowledge_dir.is_dir():
        return docs

    for md_file in sorted(knowledge_dir.rglob("*.md")):
        if ".git" in md_file.parts:
            continue
        if md_file.name == "KNOWLEDGE.md":
            continue
        # 跳过 tag-dict（不是知识文档）
        if md_file.name == ".tag-dict.yaml":
            continue
        # 跳过 staging 增量状态文件
        if md_file.name == ".extracted-staging.json":
            continue
        try:
            content = md_file.read_text()
            fm = _parse_knowledge_frontmatter(content)
            docs.append(KnowledgeDoc(
                path=md_file.relative_to(knowledge_dir),
                doc_id=fm.get("doc_id", ""),
                doc_type=fm.get("type", ""),
                domain=fm.get("domain", ""),
                title=fm.get("title", ""),
                business_line_id=fm.get("business_line_id", ""),
                tags=fm.get("tags", []),
                source_files=fm.get("source_files", []),
                source_count=fm.get("source_count", 0),
                generated_at=fm.get("generated_at", ""),
                extractor=fm.get("extractor", ""),
            ))
        except OSError:
            continue

    docs.sort(key=lambda d: d.generated_at, reverse=True)
    return docs


# ─── 文档写入 ──────────────────────────────────────────────────────────

def write_knowledge_doc(
    knowledge_dir: Path,
    doc_id: str,
    title: str,
    content: str,
) -> Path:
    """写入知识文档（upsert）。

    按 doc_id 查找已有文件：存在则覆盖，不存在则新建。
    文件名格式: kn-{doc_id}-{slug}.md

    Args:
        knowledge_dir: knowledge/ 目录路径
        doc_id: 稳定文档 ID
        title: 文档标题（用于生成文件名）
        content: 完整文档内容（含 frontmatter）

    Returns:
        写入的文件路径
    """
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    # 查找是否已有相同 doc_id 的文档
    existing: Path | None = None
    for md_file in knowledge_dir.rglob("*.md"):
        if md_file.name == "KNOWLEDGE.md":
            continue
        if ".git" in md_file.parts:
            continue
        try:
            text = md_file.read_text()
            fm = _parse_knowledge_frontmatter(text)
            if fm.get("doc_id") == doc_id:
                existing = md_file
                break
        except OSError:
            continue

    if existing:
        existing.write_text(content)
        return existing

    # 新文件：用 doc_id + title slug 命名
    slug = _make_slug(title, max_len=50)
    filename = f"kn-{slug}-{doc_id}.md"
    filepath = knowledge_dir / filename
    filepath.write_text(content)
    return filepath


def _make_slug(title: str, max_len: int = 50) -> str:
    """将标题转为文件名友好的 slug。"""
    slug = title.lower().strip()
    # 保留中文字符、字母、数字、连字符
    slug = re.sub(r'[^\w\u4e00-\u9fff-]', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip('-')
    return slug or "doc"


# ─── KNOWLEDGE.md 索引 ──────────────────────────────────────────────────

def update_knowledge_index(knowledge_dir: Path) -> None:
    """更新 KNOWLEDGE.md 索引文件。

    扫描 knowledge/ 中所有知识文档，按 domain 分组生成索引。

    Args:
        knowledge_dir: knowledge/ 目录路径
    """
    docs = scan_knowledge_docs(knowledge_dir)
    if not docs:
        return

    lines = [
        "# 团队知识索引",
        "",
        "> knowledge/shared/ 中的知识始终全量加载，不受过滤影响。",
        "> 顶层 shared/ 存放原始记忆，knowledge/ 存放知识文档——两者不同。",
        "",
    ]

    # 按 domain 分组
    domain_order = ["qa", "architecture", "workflow", "requirements"]
    grouped: dict[str, list[KnowledgeDoc]] = {}
    for doc in docs:
        d = doc.domain or "other"
        grouped.setdefault(d, []).append(doc)

    for domain in domain_order:
        if domain not in grouped:
            continue
        label = _domain_label(domain)
        lines.append(f"## {label}")
        lines.append("")
        for doc in grouped[domain]:
            tag_str = " ".join(f"#{t}" for t in doc.tags) if doc.tags else ""
            rel_path = str(doc.path)
            lines.append(f"- [{doc.title}]({rel_path}) {tag_str}")
        lines.append("")

    # 未分类的
    for domain, group in grouped.items():
        if domain in domain_order:
            continue
        label = _domain_label(domain)
        lines.append(f"## {label}")
        lines.append("")
        for doc in group:
            tag_str = " ".join(f"#{t}" for t in doc.tags) if doc.tags else ""
            rel_path = str(doc.path)
            lines.append(f"- [{doc.title}]({rel_path}) {tag_str}")
        lines.append("")

    index_path = knowledge_dir / "KNOWLEDGE.md"
    index_path.write_text("\n".join(lines))


def _domain_label(domain: str) -> str:
    """领域标识 → 中文标签。"""
    labels = {
        "qa": "踩坑记录",
        "architecture": "架构知识",
        "workflow": "流程知识",
        "requirements": "需求知识",
    }
    return labels.get(domain, domain)


# ─── 过滤匹配 ──────────────────────────────────────────────────────────

def filter_knowledge_docs(
    docs: list[KnowledgeDoc],
    *,
    tags: list[str] | None = None,
    domains: list[str] | None = None,
    doc_types: list[str] | None = None,
    since: str | None = None,
    max_docs: int | None = None,
) -> list[KnowledgeDoc]:
    """按条件过滤知识文档列表。

    各维度 AND 关系。文档必须同时满足所有条件。

    Args:
        docs: 知识文档列表
        tags: 文档 tags 与过滤 tags 交集非空即匹配
        domains: 匹配 domain 字段
        doc_types: 匹配 doc_type 字段
        since: 时间过滤（相对于 generated_at）
        max_docs: 最多 N 篇（按 generated_at 倒序）

    Returns:
        过滤后的文档列表
    """
    result = docs[:]

    if tags:
        tag_set = set(tags)
        result = [d for d in result if tag_set & set(d.tags)]

    if domains:
        result = [d for d in result if d.domain in domains]

    if doc_types:
        result = [d for d in result if d.doc_type in doc_types]

    if since:
        cutoff = _parse_time_filter(since)
        if cutoff:
            result = [d for d in result if d.generated_at >= cutoff]

    # docs 已按 generated_at 倒序排列
    if max_docs is not None and max_docs > 0:
        result = result[:max_docs]

    return result


def _parse_time_filter(since: str) -> str | None:
    """解析相对时间过滤字符串，返回 ISO 时间戳字符串。

    Args:
        since: "7d" / "24h" 等

    Returns:
        ISO 格式截止时间，无法解析返回 None
    """
    import re as _re

    m = _re.match(r'^(\d+)([dh])$', since)
    if not m:
        # 尝试作为绝对日期
        if _re.match(r'^\d{4}-\d{2}-\d{2}', since):
            return since + "T00:00:00Z"
        return None

    num = int(m.group(1))
    unit = m.group(2)

    now = time.time()
    if unit == "d":
        cutoff = now - num * 86400
    elif unit == "h":
        cutoff = now - num * 3600
    else:
        return None

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff))
