"""
Skill: parse — tree-sitter C 解析 + 缓存

用法:
    parser = Parser()
    tree = parser.parse_file("main.c")
    # 第二次调用同一文件走缓存
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field

import tree_sitter_c as tsc
from tree_sitter import Language, Parser as TSParser, Node


@dataclass
class SourceTree:
    """单文件解析结果"""
    file_path: str
    source: str
    root_node: Node
    has_errors: bool = False

    @property
    def lines(self) -> List[str]:
        return self.source.split('\n')

    @property
    def size_bytes(self) -> int:
        return len(self.source)


class Parser:
    """多文件 C 源码解析器，内置缓存"""

    def __init__(self):
        self._lang = Language(tsc.language())
        self._parser = TSParser(self._lang)
        self._cache: Dict[str, SourceTree] = {}

    def parse(self, source: str, file_path: str = "<string>") -> SourceTree:
        """解析源码字符串"""
        tree = self._parser.parse(source.encode('utf-8'))
        st = SourceTree(
            file_path=file_path,
            source=source,
            root_node=tree.root_node,
            has_errors=tree.root_node.has_error,
        )
        return st

    def parse_file(self, file_path: str) -> Optional[SourceTree]:
        """解析文件（带缓存）"""
        if file_path in self._cache:
            return self._cache[file_path]

        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
        except Exception:
            return None

        st = self.parse(source, file_path)
        self._cache[file_path] = st
        return st

    def clear_cache(self):
        self._cache.clear()
