"""
核心扫描引擎：AST 遍历 + 文本模式匹配 → 违规检测
"""

import os
import re
from typing import List, Optional
from tree_sitter import Node

from .models import Rule, Violation, Severity, ScanResult
from .skills.parse import Parser as SkillParser, SourceTree


class Scanner:
    """代码扫描器：加载规则 → 解析源码 → 检测违规"""

    def __init__(self, rules: List[Rule], parser: SkillParser = None):
        self.rules = rules
        self._parser = parser or SkillParser()

    # ── 公开 API ──────────────────────────────

    def scan_file(self, file_path: str) -> List[Violation]:
        """扫描单个文件，返回违规列表"""
        tree = self._parser.parse_file(file_path)
        if tree is None:
            return [Violation(
                rule_id="EMBED-000", severity=Severity.ERROR,
                file=file_path, line=0, column=0,
                message="Failed to read or parse file.",
                suggestion="Check file permissions and encoding.")]

        source = tree.source
        lines = tree.lines

        # 需要函数级分析的规则 ID 集合
        FUNC_LEVEL_RULES = {'EMBED-008', 'EMBED-009', 'EMBED-010', 'EMBED-011', 'EMBED-012', 'EMBED-015'}

        violations = []
        for rule in self.rules:
            # 函数级分析（跨节点上下文，如空指针检测）
            if rule.id in FUNC_LEVEL_RULES:
                violations += self._check_function_level(tree.root_node, rule, file_path, lines)
            # AST 节点匹配
            elif rule.ast_types:
                violations += self._check_ast(tree.root_node, rule, file_path, lines)
            # 文本模式匹配（fallback / 补充）
            if rule.text_patterns:
                violations += self._check_text(source, rule, file_path, lines)

        return violations

    def scan_directory(self, path: str, exclude_patterns: List[str] = None) -> ScanResult:
        """递归扫描目录

        exclude_patterns: 目录名或文件名通配符列表，匹配的会被跳过。
          目录匹配用 re.match，文件匹配用 fnmatch。
        """
        result = ScanResult()
        if exclude_patterns is None:
            exclude_patterns = []

        import fnmatch as _fnmatch

        for root, dirs, files in os.walk(path):
            # 跳过排除目录
            dirs[:] = [d for d in dirs if not any(
                re.match(p, d) for p in exclude_patterns)]
            for f in files:
                if not f.endswith('.c') and not f.endswith('.h'):
                    continue
                # 跳过排除文件的通配符匹配
                if any(_fnmatch.fnmatch(f, p) for p in exclude_patterns):
                    continue
                full_path = os.path.join(root, f)
                try:
                    violations = self.scan_file(full_path)
                    result.violations.extend(violations)
                    result.files_scanned += 1
                except Exception as e:
                    result.files_skipped += 1
                    result.errors.append(f"{full_path}: {e}")

        return result

    # ── AST 模式匹配 ──────────────────────────

    def _check_ast(self, root: Node, rule: Rule, file_path: str,
                   lines: List[str]) -> List[Violation]:
        """遍历 AST，按节点类型匹配规则，经规则特定过滤后返回违规"""
        violations = []

        def walk(node: Node):
            if node.type in rule.ast_types:
                if self._filter_ast_node(node, rule):
                    extra = self._ast_match_detail(node, rule)
                    if extra is None:
                        pass  # 被过滤，跳过
                    else:
                        violation = self._make_violation(
                            rule, node, file_path, lines, extra=extra)
                        if violation:
                            violations.append(violation)
            for child in node.children:
                walk(child)

        walk(root)
        return violations

    def _filter_ast_node(self, node: Node, rule: Rule) -> bool:
        """规则特定过滤：判断 AST 节点是否真正违规"""
        rid = rule.id

        if rid == "EMBED-003":  # 只匹配 * 和 /，跳过 + - % << >> 等
            op = node.child_by_field_name('operator')
            if op:
                op_text = op.text.decode('utf-8', errors='replace')
                return op_text in ('*', '/')
            return False

        if rid == "EMBED-004":  # 只匹配 IRQHandler 且函数体 > 10 行
            func_name = self._get_function_name(node)
            if 'Handler' not in func_name and 'IRQ' not in func_name:
                return False
            line_count = node.end_point[0] - node.start_point[0] + 1
            if line_count <= 10:
                return False
            return True

        if rid == "EMBED-007":  # 只匹配全局变量声明，跳过函数原型
            node_text = node.text.decode('utf-8', errors='replace')
            # 跳过函数声明/定义（包含括号对）
            if '(' in node_text and ')' in node_text:
                return False
            # 跳过已有 volatile / static / extern
            if any(kw in node_text for kw in ('volatile', 'static', 'extern')):
                return False
            # 检查是否在函数体内部（局部变量不需要 volatile）
            parent = node.parent
            while parent:
                if parent.type == 'function_definition':
                    return False  # 局部变量，跳过
                if parent.type == 'translation_unit':
                    break
                parent = parent.parent
            return True

        return True  # 默认不过滤

    def _ast_match_detail(self, node: Node, rule: Rule) -> str:
        """根据规则 ID 生成具体的违规描述"""
        rid = rule.id

        if rid == "EMBED-003":  # 禁止乘除
            op = node.child_by_field_name('operator')
            if op:
                op_text = op.text.decode('utf-8', errors='replace')
                left = node.child_by_field_name('left')
                right = node.child_by_field_name('right')
                l_text = left.text.decode('utf-8', errors='replace')[:20] if left else '?'
                r_text = right.text.decode('utf-8', errors='replace')[:20] if right else '?'
                return f"表达式 `{l_text} {op_text} {r_text}` 使用了 '{op_text}' 运算符"

        if rid == "EMBED-004":  # ISR 简洁性
            func_name = self._get_function_name(node)
            line_count = node.end_point[0] - node.start_point[0] + 1
            return f"ISR `{func_name}` 共 {line_count} 行（建议 ≤10 行）"

        if rid == "EMBED-007":  # 缺少 volatile
            node_text = node.text.decode('utf-8', errors='replace')[:80]
            return f"全局变量未声明 volatile: `{node_text.strip()}`"

        return rule.description

    def _get_function_name(self, node: Node) -> str:
        """从 function_definition 节点提取函数名"""
        for child in node.children:
            if child.type == 'function_declarator':
                for c in child.children:
                    if c.type in ('identifier', 'field_identifier'):
                        return c.text.decode('utf-8', errors='replace')
        return '<unknown>'

    # ── 文本模式匹配 ──────────────────────────

    def _check_text(self, source: str, rule: Rule, file_path: str,
                    lines: List[str]) -> List[Violation]:
        """基于文本正则匹配"""
        violations = []
        for pattern in rule.text_patterns:
            for m in re.finditer(pattern, source, re.MULTILINE):
                line_no = source[:m.start()].count('\n') + 1
                col_no  = m.start() - (source[:m.start()].rfind('\n') + 1) + 1
                snippet = lines[line_no - 1].strip()[:100] if line_no <= len(lines) else ''

                extra = self._text_match_detail(m, rule)
                if not extra:
                    continue  # 被过滤（如函数定义误匹配）
                violations.append(Violation(
                    rule_id      = rule.id,
                    severity     = rule.severity,
                    file         = file_path,
                    line         = line_no,
                    column       = col_no,
                    message      = extra or rule.description,
                    suggestion   = rule.fix_strategy.get('template', ''),
                    code_snippet = snippet,
                ))
        return violations

    def _text_match_detail(self, match: re.Match, rule: Rule) -> str:
        """根据规则 ID 生成文本匹配的描述，返回空字符串表示跳过（误报过滤）"""
        rid = rule.id
        matched = match.group()

        if rid == "EMBED-001":
            # 过滤函数定义：匹配位置前是返回类型（void/u8等）则为定义非调用
            line_before = match.string[:match.start()].split('\n')[-1].strip()
            if re.match(r'^(static\s+)?(void|u8|u16|u32|int|float|double|uint\d+_t)\s*$', line_before):
                return ""  # 跳过函数定义
            return f"发现阻塞延时调用 `{matched}`"
        if rid == "EMBED-002":
            return f"发现动态内存分配 `{matched}`"
        if rid == "EMBED-005":
            # 过滤注释行：跳过 // 或 /* 或 * 开头的行
            line_text = match.string[:match.start()].split('\n')[-1].strip()
            if line_text.startswith('//') or line_text.startswith('/*') or line_text.startswith('*'):
                return ""  # 跳过注释中的 "float"/"double" 单词
            return f"发现浮点类型声明 `{matched}`"
        if rid == "EMBED-006":
            # 过滤：如果匹配的 HAL_ 调用前有 = 号，说明返回值已被检查
            line_before = match.string[:match.start()].split('\n')[-1]
            if re.search(r'=\s*$', line_before):
                return ""  # 返回值已被赋值/检查
            # 再检查整行：是否在 if/while/assert 条件中
            full_line = match.string[match.start()-len(line_before):match.end()+60].split('\n')[0]
            if re.search(r'(if|while|assert)\s*\(.*HAL_', full_line):
                return ""  # 已在条件语句中检查
            return f"HAL 函数返回值未被检查 `{matched}`"

        return f"匹配到违规模式 `{matched}`"

    # ── 函数级分析（EMBED-008 / EMBED-010 等需要跨节点上下文）──

    def _check_function_level(self, root: Node, rule: Rule, file_path: str,
                               lines: List[str]) -> List[Violation]:
        """逐函数分析：按规则 ID 分发到对应的检测方法"""
        violations = []

        for func_node in self._iter_nodes(root, 'function_definition'):
            if rule.id == "EMBED-008":
                violations += self._check_null_guard(func_node, rule, file_path, lines)
            elif rule.id == "EMBED-009":
                violations += self._check_array_bounds(func_node, rule, file_path, lines)
            elif rule.id == "EMBED-010":
                violations += self._check_uninitialized(func_node, rule, file_path, lines)
            elif rule.id == "EMBED-011":
                violations += self._check_overflow(func_node, rule, file_path, lines)
            elif rule.id == "EMBED-012":
                violations += self._check_tautological(func_node, rule, file_path, lines)
            elif rule.id == "EMBED-015":
                violations += self._check_switch_default(func_node, rule, file_path, lines)

        return violations

    def _check_null_guard(self, func_node: Node, rule: Rule,
                          file_path: str, lines: List[str]) -> List[Violation]:
        """检测函数内指针参数使用前是否做了 NULL 检查"""
        violations = []

        # 1. 收集指针参数名
        ptr_params = set()
        for param in self._iter_nodes(func_node, 'parameter_declaration'):
            if param.child_by_field_name('type') and \
               any(c.type == 'pointer_declarator' for c in param.children):
                name_node = self._get_declarator_name(param)
                if name_node:
                    ptr_params.add(name_node.text.decode('utf-8', errors='replace'))

        if not ptr_params:
            return []

        # 2. 收集函数体内所有 NULL 检查的变量名
        null_checked = set()
        body = func_node.child_by_field_name('body')
        if body:
            for node in self._iter_nodes(body, 'binary_expression'):
                op = node.child_by_field_name('operator')
                if not op: continue
                op_text = op.text.decode('utf-8', errors='replace')
                if op_text not in ('==', '!='): continue
                # 任一边是 NULL 或 0
                for side in ('left', 'right'):
                    child = node.child_by_field_name(side)
                    if child:
                        child_text = child.text.decode('utf-8', errors='replace').strip()
                        if child_text in ('NULL', '0', 'nullptr'):
                            other = node.child_by_field_name('left' if side == 'right' else 'right')
                            if other:
                                null_checked.add(other.text.decode('utf-8', errors='replace').strip())
            # 也检测 !ptr 模式
            for node in self._iter_nodes(body, 'unary_expression'):
                op = node.child_by_field_name('operator')
                if op and op.text.decode('utf-8', errors='replace') == '!':
                    arg = node.child_by_field_name('argument')
                    if arg:
                        null_checked.add(arg.text.decode('utf-8', errors='replace').strip())

        # 3. 查找指针解引用：-> 运算符
        for node in self._iter_nodes(body or func_node, 'field_expression'):
            arg = node.child_by_field_name('argument')
            if arg:
                arg_text = arg.text.decode('utf-8', errors='replace').strip()
                # 提取变量名（忽略成员链）
                base_var = arg_text.split('.')[0].split('->')[0].split('[')[0].strip()
                if base_var in ptr_params and base_var not in null_checked:
                    violations.append(self._make_violation(
                        rule, node, file_path, lines,
                        extra=f"指针参数 `{base_var}` 使用 `->` 前未做 NULL 检查"))

        return violations

    def _check_uninitialized(self, func_node: Node, rule: Rule,
                              file_path: str, lines: List[str]) -> List[Violation]:
        """检测局部变量声明后使用前未赋值"""
        violations = []
        body = func_node.child_by_field_name('body')
        if not body:
            return []

        # 1. 收集未初始化的局部变量: {var_name: (line, col)}
        # 注意：tree-sitter C 中，未初始化的声明没有 init_declarator，
        # identifier 是 declaration 的直接子节点
        uninit_vars = {}
        for decl in self._iter_nodes(body, 'declaration'):
            has_init_declarator = any(c.type == 'init_declarator' for c in decl.children)
            if has_init_declarator:
                # 有 init_declarator → 检查是否有初始值
                for init_decl in self._iter_nodes(decl, 'init_declarator'):
                    name_node = init_decl.child_by_field_name('declarator')
                    value_node = init_decl.child_by_field_name('value')
                    if name_node and not value_node:
                        var_name = name_node.text.decode('utf-8', errors='replace').strip()
                        uninit_vars[var_name] = (name_node.start_point[0] + 1, name_node.start_point[1] + 1)
            else:
                # 无 init_declarator → identifier 是 declaration 的直接子节点 → 未初始化
                for child in decl.children:
                    if child.type == 'identifier':
                        var_name = child.text.decode('utf-8', errors='replace').strip()
                        uninit_vars[var_name] = (child.start_point[0] + 1, child.start_point[1] + 1)
                        break  # 每个 declaration 只有一个 identifier 直接子节点

        if not uninit_vars:
            return []

        # 2. 按行序遍历函数体，跟踪赋值和引用
        assigned = set()
        checked = set()

        for node in self._walk_in_order(body):
            node_text = node.text.decode('utf-8', errors='replace').strip()

            # 赋值表达式：var = ...
            if node.type == 'assignment_expression':
                left = node.child_by_field_name('left')
                if left:
                    left_text = left.text.decode('utf-8', errors='replace').strip()
                    assigned.add(left_text)

            # 标识符引用（在表达式中被使用）
            elif node.type == 'identifier':
                if node_text in uninit_vars and node_text not in assigned and node_text not in checked:
                    # 跳过变量声明自身的标识符（不是"使用"）
                    parent = node.parent
                    if parent and parent.type in ('declaration', 'init_declarator', 'declarator',
                                                   'pointer_declarator', 'array_declarator'):
                        continue
                    # 跳过赋值左值（不是"读取使用"）
                    if parent and parent.type == 'assignment_expression':
                        if parent.child_by_field_name('left') == node:
                            assigned.add(node_text)
                            continue
                    # 这是真正的读取使用
                    checked.add(node_text)
                    decl_line, decl_col = uninit_vars[node_text]
                    violations.append(self._make_violation(
                        rule, node, file_path, lines,
                        extra=f"变量 `{node_text}` （声明于 L{decl_line}）使用前未初始化"))

        return violations

    def _check_array_bounds(self, func_node: Node, rule: Rule,
                             file_path: str, lines: List[str]) -> List[Violation]:
        """检测固定大小数组的下标访问是否做了边界检查"""
        violations = []
        body = func_node.child_by_field_name('body')
        if not body:
            return []

        # 1. 收集函数内声明的固定大小数组: {array_name: size}
        arrays = {}
        for decl in self._iter_nodes(body, 'declaration'):
            for arr_decl in self._iter_nodes(decl, 'array_declarator'):
                name_node = arr_decl.child_by_field_name('declarator')
                size_node = arr_decl.child_by_field_name('size')
                if name_node and size_node:
                    name = name_node.text.decode('utf-8', errors='replace').strip()
                    size_text = size_node.text.decode('utf-8', errors='replace').strip()
                    try:
                        arrays[name] = int(size_text)
                    except ValueError:
                        arrays[name] = size_text  # 宏常量，保留名称

        if not arrays:
            return []

        # 2. 收集被边界检查过的索引变量
        # 模式: idx < SIZE 或 idx <= SIZE-1 或 idx < sizeof(arr)/...
        checked_index_vars = set()
        for bin_expr in self._iter_nodes(body, 'binary_expression'):
            op = bin_expr.child_by_field_name('operator')
            if not op: continue
            op_text = op.text.decode('utf-8', errors='replace')
            if op_text not in ('<', '<=', '>', '>='): continue
            left = bin_expr.child_by_field_name('left')
            right = bin_expr.child_by_field_name('right')
            if left and right:
                left_text = left.text.decode('utf-8', errors='replace').strip()
                right_text = right.text.decode('utf-8', errors='replace').strip()
                # 任一边是纯数字且另一边是变量 → 记录该变量被边界检查过
                if right_text.isdigit():
                    checked_index_vars.add(left_text)
                if left_text.isdigit():
                    checked_index_vars.add(right_text)
                # 也记录直接比较数组名的模式 (如 sizeof(arr) 相关)
                for arr_name in arrays:
                    if arr_name in left_text or arr_name in right_text:
                        checked_index_vars.add(left_text.replace(arr_name, '').strip('[]. '))
                        checked_index_vars.add(right_text.replace(arr_name, '').strip('[]. '))

        # 3. 查找对该数组的下标访问
        for sub in self._iter_nodes(body, 'subscript_expression'):
            arg = sub.child_by_field_name('argument')
            index = sub.child_by_field_name('index')
            if arg and index:
                arr_name = arg.text.decode('utf-8', errors='replace').strip()
                idx_text = index.text.decode('utf-8', errors='replace').strip()
                if arr_name in arrays and idx_text not in checked_index_vars:
                    arr_size = arrays[arr_name]
                    violations.append(self._make_violation(
                        rule, sub, file_path, lines,
                        extra=f"数组 `{arr_name}[{arr_size}]` 下标 `{idx_text}` 使用前未做边界检查"))

        return violations

    def _check_overflow(self, func_node: Node, rule: Rule,
                         file_path: str, lines: List[str]) -> List[Violation]:
        """检测小整数类型的递增/加减操作是否存在溢出风险"""
        violations = []
        body = func_node.child_by_field_name('body')
        if not body:
            return []

        SMALL_UINTS = {'uint8_t', 'u8', 'uint16_t', 'u16', 'unsigned char', 'uint_least8_t'}

        # 收集小无符号类型的局部变量
        small_vars = set()
        for decl in self._iter_nodes(body, 'declaration'):
            type_node = None
            for child in decl.children:
                if child.type in ('type_identifier', 'primitive_type'):
                    type_node = child
                    break
            if type_node:
                type_text = type_node.text.decode('utf-8', errors='replace').strip()
                if type_text in SMALL_UINTS:
                    for ident in self._iter_nodes(decl, 'identifier'):
                        small_vars.add(ident.text.decode('utf-8', errors='replace').strip())
                        break

        if not small_vars:
            return []

        # 检测 ++ 和 -- 操作
        for unary in self._iter_nodes(body, 'update_expression'):
            arg = unary.child_by_field_name('argument')
            if arg:
                var_name = arg.text.decode('utf-8', errors='replace').strip()
                if var_name in small_vars:
                    # 检查周围是否有边界保护
                    violations.append(self._make_violation(
                        rule, unary, file_path, lines,
                        extra=f"小类型变量 `{var_name}` 的自增/自减可能导致溢出（无边界检查）"))

        # 检测 += 和 *= 操作
        for assign in self._iter_nodes(body, 'assignment_expression'):
            left = assign.child_by_field_name('left')
            if left:
                left_text = left.text.decode('utf-8', errors='replace').strip()
                if left_text in small_vars:
                    op = None
                    for child in assign.children:
                        if child.type in ('+=', '-=', '*=', '<<='):
                            op = child.text.decode('utf-8', errors='replace')
                            break
                    if op:
                        violations.append(self._make_violation(
                            rule, assign, file_path, lines,
                            extra=f"小类型变量 `{left_text}` 的 `{op}` 操作可能导致溢出"))

        return violations

    def _check_tautological(self, func_node: Node, rule: Rule,
                             file_path: str, lines: List[str]) -> List[Violation]:
        """检测无符号类型与负数的冗余比较（条件恒真/恒假）"""
        violations = []
        body = func_node.child_by_field_name('body')
        if not body:
            return []

        UNSIGNED_TYPES = {'uint8_t', 'u8', 'uint16_t', 'u16', 'uint32_t', 'u32',
                          'size_t', 'unsigned', 'unsigned char', 'unsigned int',
                          'unsigned long', 'uint_least8_t', 'uint_least16_t'}

        # 收集无符号类型的局部变量
        unsigned_vars = set()
        for decl in self._iter_nodes(body, 'declaration'):
            type_node = None
            for child in decl.children:
                if child.type in ('type_identifier', 'primitive_type'):
                    type_node = child
                    break
            if type_node:
                type_text = type_node.text.decode('utf-8', errors='replace').strip()
                if type_text in UNSIGNED_TYPES:
                    for ident in self._iter_nodes(decl, 'identifier'):
                        unsigned_vars.add(ident.text.decode('utf-8', errors='replace').strip())
                        break

        # 也收集函数参数中的无符号类型
        for param in self._iter_nodes(func_node, 'parameter_declaration'):
            type_node = param.child_by_field_name('type')
            if type_node:
                type_text = type_node.text.decode('utf-8', errors='replace').strip()
                if type_text in UNSIGNED_TYPES:
                    name_node = self._get_declarator_name(param)
                    if name_node:
                        unsigned_vars.add(name_node.text.decode('utf-8', errors='replace').strip())

        if not unsigned_vars:
            return []

        # 检测无符号变量与 0 或负数的比较
        for comp in self._iter_nodes(body, 'binary_expression'):
            op_node = comp.child_by_field_name('operator')
            if not op_node: continue
            op = op_node.text.decode('utf-8', errors='replace')

            left = comp.child_by_field_name('left')
            right = comp.child_by_field_name('right')
            if not left or not right: continue

            left_text = left.text.decode('utf-8', errors='replace').strip()
            right_text = right.text.decode('utf-8', errors='replace').strip()

            # 检查：无符号变量 op 非正数
            var_side = None
            const_side = None
            if left_text in unsigned_vars:
                var_side, const_side = 'left', right_text
            elif right_text in unsigned_vars:
                var_side, const_side = 'right', left_text

            if var_side is None:
                continue

            var_name = left_text if var_side == 'left' else right_text

            # 模式 1: uvar >= 0 → 永远为真
            if op == '>=' and const_side == '0':
                violations.append(self._make_violation(
                    rule, comp, file_path, lines,
                    extra=f"`{var_name}` 是无符号类型，`>= 0` 永远为真"))

            # 模式 2: uvar < 0 → 永远为假
            elif op == '<' and const_side == '0':
                violations.append(self._make_violation(
                    rule, comp, file_path, lines,
                    extra=f"`{var_name}` 是无符号类型，`< 0` 永远为假"))

            # 模式 3: uvar == -1 → 永远为假（无符号不会是 -1）
            elif const_side.startswith('-') and const_side.lstrip('-').isdigit():
                violations.append(self._make_violation(
                    rule, comp, file_path, lines,
                    extra=f"`{var_name}` 是无符号类型，与负数 `{const_side}` 比较永远为假"))

        return violations

    def _check_switch_default(self, func_node: Node, rule: Rule,
                               file_path: str, lines: List[str]) -> List[Violation]:
        """检测 switch 语句是否缺少 default 分支"""
        violations = []
        body = func_node.child_by_field_name('body')
        if not body:
            return []

        for switch in self._iter_nodes(body, 'switch_statement'):
            # 检查 switch body 中是否有 'default' 标签
            has_default = False
            switch_body = None
            for child in switch.children:
                if child.type == 'compound_statement':
                    switch_body = child
                    break

            if switch_body:
                body_text = switch_body.text.decode('utf-8', errors='replace')
                # 检查是否有 default: 标签（在 switch body 中直接出现）
                if re.search(r'\bdefault\s*:', body_text):
                    has_default = True

            if not has_default:
                violations.append(self._make_violation(
                    rule, switch, file_path, lines,
                    extra="switch 语句缺少 default 分支"))

        return violations

    def _iter_nodes(self, root: Node, node_type: str):
        """生成器：遍历子树中指定类型的所有节点"""
        if root.type == node_type:
            yield root
        for child in root.children:
            yield from self._iter_nodes(child, node_type)

    def _walk_in_order(self, root: Node):
        """生成器：按源码顺序遍历所有节点"""
        if root.start_point[0] < root.end_point[0] or \
           (root.start_point[0] == root.end_point[0] and root.start_point[1] <= root.end_point[1]):
            yield root
        for child in root.children:
            yield from self._walk_in_order(child)

    def _get_declarator_name(self, node: Node):
        """从 parameter_declaration 中提取变量名"""
        for child in node.children:
            if child.type in ('identifier', 'field_identifier'):
                return child
            if child.type == 'pointer_declarator':
                for c in child.children:
                    if c.type in ('identifier', 'field_identifier'):
                        return c
            if child.type == 'type_identifier':
                continue
            result = self._get_declarator_name(child)
            if result:
                return result
        return None

    # ── 工具方法 ──────────────────────────────

    def _make_violation(self, rule: Rule, node: Node, file_path: str,
                        lines: List[str], extra: str = "") -> Optional[Violation]:
        """从 AST 节点构造 Violation 对象"""
        line_no = node.start_point[0] + 1
        col_no  = node.start_point[1] + 1
        snippet = lines[node.start_point[0]].strip()[:120] if node.start_point[0] < len(lines) else ''

        return Violation(
            rule_id      = rule.id,
            severity     = rule.severity,
            file         = file_path,
            line         = line_no,
            column       = col_no,
            message      = extra or rule.description,
            suggestion   = rule.fix_strategy.get('template', ''),
            code_snippet = snippet,
        )
