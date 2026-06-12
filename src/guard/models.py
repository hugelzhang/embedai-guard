"""
数据模型：Rule（规则定义）和 Violation（违规记录）
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


class Severity(Enum):
    ERROR   = "error"
    WARNING = "warning"
    INFO    = "info"


@dataclass
class Violation:
    """单条违规记录"""
    rule_id:      str            # 规则 ID，如 EMBED-001
    severity:     Severity       # 严重级别
    file:         str            # 文件路径
    line:         int            # 行号 (1-based)
    column:       int            # 列号 (1-based)
    message:      str            # 违规描述
    suggestion:   str            # 修复建议
    code_snippet: str = ""       # 违规代码片段（可选）


@dataclass
class Rule:
    """单条规则定义"""
    id:             str                        # 唯一 ID
    name:           str                        # 规则名称
    severity:       Severity                   # 默认严重级别
    category:       str                        # 分类标签
    description:    str                        # 规则说明
    # 匹配方式（二选一或组合）
    ast_types:      List[str] = field(default_factory=list)  # AST 节点类型
    text_patterns:  List[str] = field(default_factory=list)  # 文本正则
    # 修复策略
    fix_strategy:   Dict[str, Any] = field(default_factory=dict)
    auto_fix:       bool = False               # 是否可自动修复
    chip_specific:  bool = False               # 是否芯片相关


@dataclass
class ScanResult:
    """扫描结果汇总"""
    files_scanned:  int = 0
    files_skipped:  int = 0
    violations:     List[Violation] = field(default_factory=list)
    errors:         List[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.INFO)

    @property
    def passed(self) -> bool:
        return self.error_count == 0 and len(self.errors) == 0
