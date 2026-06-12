"""
规则加载器：从 YAML 文件加载 Rule 定义
"""

import os
import glob
import yaml
from typing import List, Optional
from .models import Rule, Severity


def _parse_severity(s: str) -> Severity:
    """字符串 -> Severity 枚举"""
    s = s.lower().strip()
    if s in ("error", "err"):
        return Severity.ERROR
    if s in ("warning", "warn"):
        return Severity.WARNING
    return Severity.INFO


def load_rule_from_yaml(path: str) -> Optional[Rule]:
    """从单个 YAML 文件加载规则"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"[WARN] Failed to load rule {path}: {e}")
        return None

    if not data or 'id' not in data:
        return None

    return Rule(
        id            = data.get('id', ''),
        name          = data.get('name', ''),
        severity      = _parse_severity(data.get('severity', 'warning')),
        category      = data.get('category', ''),
        description   = data.get('description', ''),
        ast_types     = data.get('ast_types', []),
        text_patterns = data.get('text_patterns', []),
        fix_strategy  = data.get('fix_strategy', {}),
        auto_fix      = data.get('auto_fix', False),
        chip_specific = data.get('chip_specific', False),
    )


def load_all_rules(rules_dir: str) -> List[Rule]:
    """加载目录下所有 .yml 规则文件"""
    rules = []
    pattern = os.path.join(rules_dir, '*.yml')
    for f in sorted(glob.glob(pattern)):
        rule = load_rule_from_yaml(f)
        if rule:
            rules.append(rule)
    return rules
