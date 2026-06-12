"""
自动修复引擎：对可安全自动修复的违规，执行代码变换并生成 diff。
"""

import os
import re
from typing import List, Tuple, Optional
from .models import Violation, Rule


class Fix:
    """单条修复操作"""
    def __init__(self, violation: Violation, old_text: str, new_text: str,
                 file_path: str, line: int):
        self.violation = violation
        self.old_text = old_text
        self.new_text = new_text
        self.file_path = file_path
        self.line = line

    @property
    def applied(self) -> bool:
        return self.old_text != self.new_text


class FixResult:
    """修复结果汇总"""
    def __init__(self):
        self.fixes: List[Fix] = []
        self.failed: List[str] = []

    @property
    def applied_count(self) -> int:
        return sum(1 for f in self.fixes if f.applied)

    @property
    def total_count(self) -> int:
        return len(self.fixes)


def _is_power_of_two(n: int) -> bool:
    """判断整数是否为 2 的幂"""
    return n > 0 and (n & (n - 1)) == 0


def _compute_power_of_two_shift(text: str, operator: str) -> Tuple[Optional[str], Optional[str]]:
    """
    对乘除的常量操作数计算移位替代。
    返回 (new_operator, new_right_operand) 或 (None, None) 表示无法修复。
    """
    text = text.strip()
    # 提取数值
    m = re.match(r'(\d+)\s*(U?L?L?)?$', text)
    if not m:
        return None, None
    val = int(m.group(1))
    suffix = m.group(2) or ''

    if not _is_power_of_two(val):
        return None, None

    shift = val.bit_length() - 1  # log2

    if operator == '/':
        return '>>', f'{shift}{suffix}'
    elif operator == '*':
        return '<<', f'{shift}{suffix}'
    return None, None


def generate_fixes(violations: List[Violation], source_by_file: dict) -> FixResult:
    """根据违规列表生成修复操作"""
    result = FixResult()

    # 按文件分组，从后往前修复（避免行号偏移）
    by_file: dict = {}
    for v in violations:
        by_file.setdefault(v.file, []).append(v)

    for file_path, file_violations in by_file.items():
        if file_path not in source_by_file:
            continue
        source = source_by_file[file_path]
        lines = source.split('\n')

        # 按行号倒序排列（从文件末尾往开头修，保证行号不变）
        sorted_v = sorted(file_violations, key=lambda v: (v.line, v.column), reverse=True)

        for v in sorted_v:
            fix = _try_fix(v, lines, file_path)
            if fix:
                result.fixes.append(fix)
            else:
                result.failed.append(f"{v.rule_id}:{os.path.basename(file_path)}:{v.line}")

    return result


def _try_fix(v: Violation, lines: List[str], file_path: str) -> Optional[Fix]:
    """尝试对单条违规生成修复"""
    rid = v.rule_id

    if rid == "EMBED-003":
        return _fix_divide_multiply(v, lines, file_path)
    if rid == "EMBED-010":
        return _fix_uninitialized(v, lines, file_path)

    return None


def _fix_divide_multiply(v: Violation, lines: List[str], file_path: str) -> Optional[Fix]:
    """修复乘除: /2→>>1, *4→<<2"""
    if v.line < 1 or v.line > len(lines):
        return None

    line = lines[v.line - 1]
    col = v.column - 1  # 转为 0-based

    # 定位 operator 位置（column 指向 binary_expression 的开始，需要定位到 * 或 /）
    # 从 column 往后找 * 或 /
    op_pos = -1
    for i in range(col, min(col + 60, len(line))):
        if line[i] in ('*', '/'):
            # 确认是运算符而非注释或字符串
            if i > 0 and line[i-1] != '/' and line[i+1] != '/' and line[i+1] != '*':
                op_pos = i
                break

    if op_pos < 0:
        return None

    operator = line[op_pos]

    # 提取右操作数
    right_start = op_pos + 1
    while right_start < len(line) and line[right_start] in (' ', '\t'):
        right_start += 1

    right_end = right_start
    while right_end < len(line) and (line[right_end].isalnum() or line[right_end] in ('_', 'U', 'L')):
        right_end += 1

    right_text = line[right_start:right_end]
    new_op, new_right = _compute_power_of_two_shift(right_text, operator)

    if new_op is None:
        return None

    # 构造新行 — << 优先级低于 / 和 *，若右侧紧跟乘除需加括号
    before = line[:op_pos].rstrip()
    after = line[right_end:]
    after_stripped = after.lstrip()

    # 提取左操作数的文本（从 op_pos 往回找）
    left_start = op_pos - 1
    while left_start >= 0 and line[left_start] in (' ', '\t'):
        left_start -= 1
    left_end = left_start + 1
    while left_start >= 0 and (line[left_start].isalnum() or line[left_start] in ('_', '.')):
        left_start -= 1
    left_start += 1
    left_text = line[left_start:left_end]

    if after_stripped and after_stripped[0] in ('/', '*', '%'):
        # 需要括号：prefix (left << N) / rest
        prefix = line[:left_start]
        # 去掉 before 中已包含的 left_text 部分
        new_line = prefix + '(' + left_text + ' ' + new_op + ' ' + new_right + ')' + after
    else:
        new_line = before + ' ' + new_op + ' ' + new_right + after

    return Fix(v, line, new_line, file_path, v.line)


def _fix_uninitialized(v: Violation, lines: List[str], file_path: str) -> Optional[Fix]:
    """修复未初始化: u32 i; → u32 i = 0;

    violation 指向的是"使用"行，但修复应该在"声明"行。
    从 message 中提取声明行号：`变量 xxx （声明于 L68）使用前未初始化`
    """
    var_name = ""
    decl_line = v.line  # 默认使用 violation 行号

    # 提取变量名和声明行号
    m = re.search(r'变量 `(\w+)`\s*（声明于\s*L(\d+)）', v.message)
    if m:
        var_name = m.group(1)
        decl_line = int(m.group(2))
    else:
        # fallback: 可能只有变量名
        m2 = re.search(r'变量 `(\w+)`', v.message)
        if m2:
            var_name = m2.group(1)

    if not var_name or decl_line < 1 or decl_line > len(lines):
        return None

    line = lines[decl_line - 1]

    # 在声明行中找到变量并加 = 0
    # 模式: type_name var_name;  →  type_name var_name = 0;
    pattern = re.compile(r'\b(' + re.escape(var_name) + r')\s*;')
    m3 = pattern.search(line)
    if not m3:
        return None

    new_line = line[:m3.end(1)] + ' = 0' + line[m3.end(1):]
    return Fix(v, line, new_line, file_path, decl_line)


def apply_fixes(fixes: List[Fix], source_by_file: dict, dry_run: bool = True) -> dict:
    """应用修复：返回 {file_path: modified_source}"""
    modified = {}

    # 按文件分组
    by_file: dict = {}
    for fix in fixes:
        by_file.setdefault(fix.file_path, []).append(fix)

    for file_path, file_fixes in by_file.items():
        if file_path not in source_by_file:
            continue
        lines = source_by_file[file_path].split('\n')
        # 按行号排序
        sorted_f = sorted(file_fixes, key=lambda f: (f.line, f.line), reverse=True)
        for fix in sorted_f:
            if fix.line > 0 and fix.line <= len(lines):
                lines[fix.line - 1] = fix.new_text
        modified[file_path] = '\n'.join(lines)

        if not dry_run:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(modified[file_path])

    return modified


def format_diff(fixes: List[Fix]) -> str:
    """生成修复预览的 diff 风格输出"""
    if not fixes:
        return "  No fixes available."

    RED   = '\033[91m'
    GREEN = '\033[92m'
    CYAN  = '\033[96m'
    RESET = '\033[0m'
    BOLD  = '\033[1m'

    lines_out = []
    lines_out.append(f"{BOLD}{CYAN}Proposed fixes ({len(fixes)}):{RESET}\n")

    # 按文件分组
    by_file: dict = {}
    for fix in fixes:
        by_file.setdefault(fix.file_path, []).append(fix)

    for file_path, file_fixes in sorted(by_file.items()):
        fname = os.path.basename(file_path)
        lines_out.append(f"  {BOLD}{fname}{RESET}")

        for fix in file_fixes:
            lines_out.append(f"    {CYAN}L{fix.line:4d}{RESET}  [{fix.violation.rule_id}]")
            # 简单 diff: 显示删除行和新增行
            old_display = fix.old_text.strip()[:100]
            new_display = fix.new_text.strip()[:100]
            lines_out.append(f"    {RED}- {old_display}{RESET}")
            lines_out.append(f"    {GREEN}+ {new_display}{RESET}")
            lines_out.append("")

    applied = sum(1 for f in fixes if f.applied)
    lines_out.append(f"  {applied}/{len(fixes)} fixes ready to apply.")
    return '\n'.join(lines_out)
