"""
Skill: fs — 项目文件系统遍历 + 排除规则 + 源码缓存

用法:
    fs = ProjectFS("path/to/project")
    for fp, source in fs.iter_files():
        ...
"""

import os
import re
import fnmatch
from typing import List, Iterator, Tuple, Optional

# 默认排除的目录和文件模式
DEFAULT_EXCLUDE = [
    'HALLIB', 'CORE', 'OBJ',
    'stm32l4*', 'core_cm*', 'cmsis_*',
    'system_stm32*', 'stm32_assert*',
]

# 默认源文件扩展名
DEFAULT_EXTENSIONS = ('.c', '.h')


class ProjectFS:
    """项目文件系统：遍历 + 排除 + 缓存"""

    def __init__(self, root: str, exclude: List[str] = None,
                 extensions: Tuple[str, ...] = None):
        self.root = os.path.abspath(root)
        self.exclude = exclude or DEFAULT_EXCLUDE
        self.extensions = extensions or DEFAULT_EXTENSIONS
        self._source_cache: dict = {}    # filepath → source

    def find_files(self) -> List[str]:
        """查找所有源文件路径"""
        files = []
        if os.path.isfile(self.root):
            if self.root.endswith(self.extensions):
                return [self.root]
            return []

        for root, dirs, fnames in os.walk(self.root):
            # 排除目录
            dirs[:] = [d for d in dirs
                       if not any(re.match(p, d) for p in self.exclude)]
            for f in fnames:
                if not f.endswith(self.extensions):
                    continue
                if any(fnmatch.fnmatch(f, p) for p in self.exclude):
                    continue
                files.append(os.path.join(root, f))
        return sorted(files)

    def read(self, file_path: str) -> Optional[str]:
        """读取文件（带缓存）"""
        if file_path in self._source_cache:
            return self._source_cache[file_path]
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
            self._source_cache[file_path] = source
            return source
        except Exception:
            return None

    def iter_files(self) -> Iterator[Tuple[str, str]]:
        """迭代所有源文件 (path, source)"""
        for fp in self.find_files():
            source = self.read(fp)
            if source is not None:
                yield fp, source

    def count_files(self) -> int:
        return len(self.find_files())
