"""提取器自动发现与注册。

扫描 extractors/ 目录，自动发现所有 KnowledgeExtractor 子类。
新增提取器只需在 extractors/ 目录添加 .py 文件即可。
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

from .base import KnowledgeExtractor


def discover_extractors(package_path: str = "team_memory.knowledge.extractors") -> list[type[KnowledgeExtractor]]:
    """自动发现 extractors/ 包中的所有 KnowledgeExtractor 子类。

    遍历 extractors/ 目录下的 .py 模块，导入并检查是否有
    KnowledgeExtractor 的具体子类（非 ABC 本身）。

    Args:
        package_path: 提取器包的完整导入路径

    Returns:
        KnowledgeExtractor 子类列表（按 name 排序）
    """
    extractors: list[type[KnowledgeExtractor]] = []

    try:
        package = importlib.import_module(package_path)
    except ImportError:
        return extractors

    pkg_dir = Path(package.__file__).parent if package.__file__ else None
    if pkg_dir is None:
        return extractors

    for _, module_name, _ in pkgutil.iter_modules([str(pkg_dir)]):
        if module_name.startswith("_"):
            continue

        try:
            module = importlib.import_module(f"{package_path}.{module_name}")
        except ImportError:
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, KnowledgeExtractor)
                and obj is not KnowledgeExtractor
                and not inspect.isabstract(obj)
            ):
                extractors.append(obj)

    extractors.sort(key=lambda cls: cls.name)
    return extractors


def get_extractor_by_name(name: str) -> type[KnowledgeExtractor] | None:
    """按名称查找提取器。

    Args:
        name: 提取器标识（如 "qa", "architecture"）

    Returns:
        提取器类，未找到返回 None
    """
    for cls in discover_extractors():
        if cls.name == name:
            return cls
    return None
