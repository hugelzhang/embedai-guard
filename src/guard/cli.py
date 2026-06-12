"""
EmbedAI Guard CLI — 嵌入式固件代码质量守卫
"""

import sys
import os
import json
import glob
from pathlib import Path
from typing import List

from .loader import load_all_rules
from .scanner import Scanner
from .models import ScanResult, Violation


# ── 格式化输出 ──────────────────────────────

_RESET  = '\033[0m'
_RED    = '\033[91m'
_GREEN  = '\033[92m'
_YELLOW = '\033[93m'
_CYAN   = '\033[96m'
_BOLD   = '\033[1m'
_DIM    = '\033[2m'


def _severity_color(sev: str) -> str:
    if sev == 'error':   return _RED
    if sev == 'warning': return _YELLOW
    return _DIM


def format_terminal(result: ScanResult) -> str:
    """终端彩色输出"""
    lines = []
    lines.append(f"{_BOLD}{_CYAN}EmbedAI Guard — Scan Results{_RESET}")
    lines.append(f"  Files: {result.files_scanned} scanned"
                 + (f", {result.files_skipped} skipped" if result.files_skipped else ""))
    lines.append(f"  {_RED}ERROR: {result.error_count}{_RESET}  "
                 f"{_YELLOW}WARNING: {result.warning_count}{_RESET}  "
                 f"INFO: {result.info_count}")
    lines.append("")

    if not result.violations:
        lines.append(f"  {_GREEN}{_BOLD}✓ All checks passed!{_RESET}")
        return "\n".join(lines)

    # 按文件分组
    by_file: dict = {}
    for v in result.violations:
        by_file.setdefault(v.file, []).append(v)

    for file_path, violations in sorted(by_file.items()):
        fname = os.path.basename(file_path)
        lines.append(f"  {_BOLD}{fname}{_RESET}  ({len(violations)} issue(s))")
        for v in violations:
            color = _severity_color(v.severity.value)
            lines.append(f"    {color}{v.severity.value.upper():7s}{_RESET} "
                         f"L{v.line:4d}:{v.column:<3d}  [{v.rule_id}]  {v.message}")
            if v.code_snippet:
                lines.append(f"            {_DIM}> {v.code_snippet.strip()[:120]}{_RESET}")
        lines.append("")

    if result.errors:
        lines.append(f"  {_RED}Parse errors ({len(result.errors)}):{_RESET}")
        for e in result.errors[:5]:
            lines.append(f"    {_DIM}{e}{_RESET}")

    # 汇总
    if result.passed:
        lines.append(f"  {_GREEN}{_BOLD}✓ 0 errors — PASSED{_RESET}")
    else:
        lines.append(f"  {_RED}{_BOLD}✗ {result.error_count} error(s) — FIX REQUIRED{_RESET}")

    return "\n".join(lines)


def format_json(result: ScanResult) -> str:
    """JSON 输出（适合 CI 集成）"""
    violations = []
    for v in result.violations:
        violations.append({
            "rule_id": v.rule_id,
            "severity": v.severity.value,
            "file": v.file,
            "line": v.line,
            "column": v.column,
            "message": v.message,
            "suggestion": v.suggestion,
            "code_snippet": v.code_snippet,
        })
    return json.dumps({
        "files_scanned": result.files_scanned,
        "files_skipped": result.files_skipped,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "info_count": result.info_count,
        "passed": result.passed,
        "violations": violations,
        "errors": result.errors,
    }, indent=2, ensure_ascii=False)


def format_junit(result: ScanResult) -> str:
    """JUnit XML 输出（CI 集成）"""
    total = len(result.violations)
    failures = result.error_count
    skipped = result.warning_count + result.info_count

    xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuite name="EmbedAI Guard" tests="{total}" '
        f'failures="{failures}" errors="0" skipped="{skipped}">',
    ]
    for v in result.violations:
        is_failure = v.severity.value == 'error'
        xml.append(f'  <testcase classname="{v.rule_id}" '
                   f'name="{os.path.basename(v.file)}:{v.line}:{v.column}" '
                   f'time="0">')
        if is_failure:
            xml.append(f'    <failure message="{v.message}" '
                       f'type="{v.rule_id}"/>')
        xml.append('  </testcase>')
    xml.append('</testsuite>')
    return '\n'.join(xml)


# ── 主命令 ──────────────────────────────────

def scan_command(path: str, rules_dir: str, fmt: str = 'terminal',
                 exclude: List[str] = None) -> int:
    """扫描目录或文件"""
    # 加载规则
    rules = load_all_rules(rules_dir)
    if not rules:
        print(f"ERROR: No rules found in {rules_dir}", file=sys.stderr)
        return 1

    scanner = Scanner(rules)

    # 默认排除模式（与 check_code.ps1 对齐）
    if exclude is None:
        exclude = []

    # 扫描
    path = os.path.abspath(path)
    if os.path.isfile(path):
        result = ScanResult()
        try:
            result.violations = scanner.scan_file(path)
            result.files_scanned = 1
        except Exception as e:
            result.errors.append(str(e))
            result.files_skipped = 1
    elif os.path.isdir(path):
        result = scanner.scan_directory(path, exclude)
    else:
        print(f"ERROR: {path} is not a valid file or directory", file=sys.stderr)
        return 1

    # 输出
    if fmt == 'json':
        print(format_json(result))
    elif fmt == 'junit':
        print(format_junit(result))
    else:
        print(format_terminal(result))

    return 0 if result.passed else 1


def fix_command(path: str, rules_dir: str, exclude: List[str],
                dry_run: bool = True) -> int:
    """扫描 + 生成修复 + 预览/应用"""
    import glob as _glob
    import re as _re
    from .fixer import generate_fixes, apply_fixes, format_diff

    rules = load_all_rules(rules_dir)
    if not rules:
        print(f"ERROR: No rules found in {rules_dir}", file=sys.stderr)
        return 1

    scanner = Scanner(rules)
    path = os.path.abspath(path)

    # 收集源文件
    violations = []
    source_by_file = {}

    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                source_by_file[path] = f.read()
            violations = scanner.scan_file(path)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    elif os.path.isdir(path):
        import fnmatch as _fnmatch
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not any(_re.match(p, d) for p in exclude)]
            for f in files:
                if not f.endswith('.c') and not f.endswith('.h'):
                    continue
                if any(_fnmatch.fnmatch(f, p) for p in exclude):
                    continue
                fp = os.path.join(root, f)
                try:
                    with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
                        source_by_file[fp] = fh.read()
                    violations += scanner.scan_file(fp)
                except Exception:
                    pass
    else:
        print(f"ERROR: {path} is not valid", file=sys.stderr)
        return 1

    # 只保留可自动修复的规则
    fixable_ids = {r.id for r in rules if r.auto_fix}
    fixable_violations = [v for v in violations if v.rule_id in fixable_ids]

    if not fixable_violations:
        print(f"{_GREEN}No auto-fixable violations found.{_RESET}")
        return 0

    # 生成修复
    result = generate_fixes(fixable_violations, source_by_file)

    # 预览
    print(f"{_BOLD}{_CYAN}EmbedAI Guard — Auto Fix{_RESET}")
    print(f"  Scanned: {len(source_by_file)} files")
    print(f"  Fixable violations: {len(fixable_violations)}")
    print(f"  Fixes generated: {result.applied_count}/{result.total_count}")
    print()

    if not result.fixes:
        print(f"  {_YELLOW}No fixes could be generated.{_RESET}")
        return 0

    print(format_diff(result.fixes))

    if not dry_run:
        print(f"{_YELLOW}Applying fixes...{_RESET}")
        apply_fixes(result.fixes, source_by_file, dry_run=False)
        print(f"{_GREEN}{result.applied_count} fixes applied.{_RESET}")
    else:
        print(f"{_DIM}Use --apply to write changes to disk.{_RESET}")

    return 0


def list_rules_command(rules_dir: str) -> int:
    """列出所有可用规则"""
    rules = load_all_rules(rules_dir)
    if not rules:
        print(f"No rules found in {rules_dir}")
        return 1
    print(f"{_BOLD}{_CYAN}Available Rules ({len(rules)}){_RESET}\n")
    for r in rules:
        sev_color = _severity_color(r.severity.value)
        print(f"  [{sev_color}{r.id}{_RESET}] {r.name}")
        print(f"      {_DIM}{r.category} | severity: {r.severity.value}{_RESET}")
        print(f"      {r.description[:100]}")
        print()
    return 0


def plan_command(path: str) -> int:
    """项目分析 + 推荐执行计划"""
    from .engines.planner import Planner

    planner = Planner()
    plan = planner.analyze(path)
    print(plan.summary())

    if plan.execution_order:
        print(f"\n{_BOLD}Run:{_RESET}")
        if 'scan' in plan.execution_order:
            print(f"  {_CYAN}guard scan .{_RESET}")
        if 'fix' in plan.execution_order:
            print(f"  {_CYAN}guard fix . --dry-run{_RESET}")
        if 'check' in plan.execution_order:
            print(f"  {_CYAN}guard check . --contract {plan.recommended_contract}{_RESET}")
    return 0


def execute_command(path: str) -> int:
    """一键执行完整计划：plan → scan → fix → check"""
    from .engines.planner import Planner
    from .contract import ContractViolation

    print(f"{_BOLD}{_CYAN}EmbedAI Guard — Full Execution{_RESET}\n")

    planner = Planner()
    plan, results = planner.execute(path)

    # ── Scan 结果 ──
    store = results.get('scan')
    if store:
        print(store.format('terminal'))

    # ── Fix 结果 ──
    fix_result = results.get('fix')
    if fix_result and fix_result.fixes:
        print(f"\n{_BOLD}{_CYAN}[Fix] Auto-fix applied:{_RESET} "
              f"{fix_result.applied_count} changes")
        for f in fix_result.fixes[:5]:
            fname = os.path.basename(f.file_path)
            print(f"  {_GREEN}{fname}:{f.line}{_RESET}  "
                  f"{f.old_text.strip()[:50]} {_DIM}→{_RESET} "
                  f"{f.new_text.strip()[:50]}")

    # ── Check 结果 ──
    violations = results.get('check', [])
    pins = results.get('check_pins', [])
    if pins:
        print(f"\n{_BOLD}{_CYAN}[Check] Chip: {plan.chip_detected}{_RESET}")
        print(f"  Pins assigned: {len(pins)}")
        by_pin = {}
        for a in pins:
            by_pin.setdefault(a.pin_name, []).append(a)
        for pn in sorted(by_pin.keys())[:10]:
            funcs = set(a.function for a in by_pin[pn])
            print(f"  {pn:5s} → {', '.join(funcs)}")
        if len(by_pin) > 10:
            print(f"  ... and {len(by_pin)-10} more")

    if violations:
        errors = [v for v in violations if v.severity == 'error']
        warns = [v for v in violations if v.severity == 'warning']
        print(f"\n  {_RED}Contract: {len(errors)} errors{_RESET}, "
              f"{_YELLOW}{len(warns)} warnings{_RESET}")
        for v in violations[:5]:
            c = _RED if v.severity == 'error' else _YELLOW
            print(f"    {c}[{v.severity.upper()}]{_RESET} {v.message}")
    elif pins:
        print(f"  {_GREEN}✓ No contract violations{_RESET}")

    # ── 总结 ──
    scan_ok = store.passed if store else True
    check_errs = sum(1 for v in (violations or []) if v.severity == 'error')
    all_ok = scan_ok and check_errs == 0

    print(f"\n{'═' * 50}")
    if all_ok:
        print(f"{_GREEN}{_BOLD}  ALL CHECKS PASSED{_RESET}")
    else:
        print(f"{_RED}{_BOLD}  ISSUES FOUND{_RESET}"
              f"  |  scan: {'PASS' if scan_ok else 'FAIL'}"
              f"  |  contract: {check_errs} errors")
    print(f"{'═' * 50}")

    return 0 if all_ok else 1


def check_command(path: str, contract_path: str) -> int:
    """芯片契约验证：引脚冲突 / AF 号 / DMA 通道"""
    import glob as _glob
    from .skills.parse import Parser as SkillParser
    from .contract import (ChipContract, extract_pin_assignments,
                           validate_assignments, validate_clocks,
                           validate_dma, validate_interrupts, _iter_all)

    # 加载契约
    if not os.path.exists(contract_path):
        print(f"ERROR: Contract file not found: {contract_path}", file=sys.stderr)
        return 1
    contract = ChipContract.from_file(contract_path)
    skill_parser = SkillParser()

    # 收集源文件
    path = os.path.abspath(path)
    files = []
    if os.path.isfile(path):
        files = [path]
    elif os.path.isdir(path):
        for root, dirs, fnames in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ('HALLIB', 'CORE', 'OBJ')]
            for f in fnames:
                if f.endswith('.c'):
                    files.append(os.path.join(root, f))

    # 提取引脚分配
    all_assignments = []
    for fp in sorted(files):
        try:
            with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
                source = fh.read()
            assigns = extract_pin_assignments(source, fp, skill_parser)
            all_assignments.extend(assigns)
        except Exception:
            pass

    if not all_assignments:
        print(f"{_YELLOW}No pin assignments detected in source code.{_RESET}")
        print(f"  (HAL_GPIO_Init patterns with .Pin and .Alternate settings)")
        return 0

    # 验证
    result = validate_assignments(all_assignments, contract)

    # ── 时钟 / DMA / 中断验证 ──
    for fp in sorted(files):
        try:
            with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
                source = fh.read()
            result.violations += validate_clocks(source, fp, contract, skill_parser)
            result.violations += validate_dma(source, fp, contract, skill_parser)
            result.violations += validate_interrupts(source, fp, contract, skill_parser)
        except Exception:
            pass

    print(f"{_BOLD}{_CYAN}EmbedAI Guard — Contract Check{_RESET}")
    print(f"  Contract: {contract.data.get('chip', '?')} ({contract.data.get('package', '?')})")
    print(f"  Files scanned: {len(files)}")
    print(f"  Pin assignments found: {len(all_assignments)}")

    # 列出检测到的引脚分配
    print(f"\n{_BOLD}Detected pin assignments:{_RESET}")
    by_pin: dict = {}
    for a in all_assignments:
        by_pin.setdefault(a.pin_name, []).append(a)
    for pin_name in sorted(by_pin.keys()):
        assigns = by_pin[pin_name]
        funcs = set(a.function for a in assigns)
        fname = os.path.basename(assigns[0].file)
        af_str = f" AF{assigns[0].af}" if assigns[0].af is not None else ""
        print(f"  {pin_name:5s} → {', '.join(funcs):20s}{af_str}  ({fname}:{assigns[0].line})")

    # 冲突（按类别分组）
    if result.violations:
        error_count = sum(1 for v in result.violations if v.severity == 'error')
        warn_count = sum(1 for v in result.violations if v.severity == 'warning')
        print(f"\n{_BOLD}Contract violations: {_RED}{error_count} errors{_RESET}, "
              f"{_YELLOW}{warn_count} warnings{_RESET}")

        for cat in ['pin_conflict', 'clock', 'dma', 'interrupt']:
            cat_violations = [v for v in result.violations if v.category == cat]
            if not cat_violations:
                continue
            cat_names = {'pin_conflict': 'Pin Conflicts', 'clock': 'Clock Configuration',
                         'dma': 'DMA Channels', 'interrupt': 'Interrupt Priorities'}
            print(f"\n  {_BOLD}{cat_names.get(cat, cat)}{_RESET} ({len(cat_violations)})")
            for v in cat_violations:
                sev_color = _RED if v.severity == 'error' else _YELLOW
                fname = os.path.basename(v.file)
                print(f"    {sev_color}[{v.severity.upper():7s}]{_RESET} "
                      f"{fname}:{v.line} {v.message}")
                if v.detail:
                    print(f"              {_DIM}{v.detail}{_RESET}")
    else:
        print(f"\n{_GREEN}{_BOLD}✓ No contract violations.{_RESET}")

    return 0 if result.passed else 1


# ── 入口点 ──────────────────────────────────

def main():
    """CLI 入口"""
    import argparse
    import io

    # 确保 stdout 支持 UTF-8（Windows GBK 环境兼容）
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(
        description='EmbedAI Guard — 嵌入式固件代码质量守卫',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  guard scan .                                    # 扫描当前目录
  guard scan Src/ --rules my_rules/               # 指定规则目录
  guard scan main.c --format json                 # 单文件 JSON 输出
  guard scan . --format junit > report.xml        # CI 集成
  guard rules                                     # 列出所有规则
        ''')

    sub = parser.add_subparsers(dest='command', help='子命令')

    # guard scan
    scan = sub.add_parser('scan', help='扫描代码')
    scan.add_argument('path', help='文件或目录路径')
    scan.add_argument('--rules', default=None,
                      help='规则目录 (默认: src/rules/)')
    scan.add_argument('--exclude', nargs='*', default=None,
                      help='排除的目录名或文件名通配符 (默认: HALLIB CORE stm32l4* cmsis_* core_cm* system_stm32*)')
    # guard fix
    fix = sub.add_parser('fix', help='自动修复违规')
    fix.add_argument('path', help='文件或目录路径')
    fix.add_argument('--rules', default=None,
                     help='规则目录 (默认: src/rules/)')
    fix.add_argument('--exclude', nargs='*', default=None,
                     help='排除的目录名或文件名通配符')
    fix.add_argument('--apply', action='store_true',
                     help='实际写入文件（默认仅预览）')
    fix.add_argument('--dry-run', action='store_true', default=True,
                     help='仅预览修复，不写入（默认行为）')

    # guard check
    check = sub.add_parser('check', help='芯片契约验证（引脚冲突/时钟/DMA）')
    check.add_argument('path', help='文件或目录路径')
    check.add_argument('--contract', required=True,
                       help='芯片契约 JSON 文件路径')

    # guard plan
    plan = sub.add_parser('plan', help='项目分析 + 推荐执行计划')
    plan.add_argument('path', nargs='?', default='.',
                      help='项目路径 (默认: 当前目录)')
    plan.add_argument('--execute', action='store_true',
                      help='自动执行推荐计划 (scan → fix → check)')

    scan.add_argument('--format', choices=['terminal', 'json', 'junit'],
                      default='terminal', help='输出格式 (默认: terminal)')

    # guard rules
    rules_cmd = sub.add_parser('rules', help='列出所有规则')
    rules_cmd.add_argument('--rules', default=None,
                           help='规则目录 (默认: src/rules/)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # 默认规则目录
    default_rules = os.path.join(os.path.dirname(__file__), '..', 'rules')
    rules_dir = os.path.abspath(getattr(args, 'rules', None)) if getattr(args, 'rules', None) else default_rules

    if args.command == 'scan':
        # 默认排除 vendor 目录和文件（与 check_code.ps1 对齐）
        if args.exclude is None:
            args.exclude = ['HALLIB', 'CORE', 'OBJ',
                           'stm32l4*', 'core_cm*', 'cmsis_*',
                           'system_stm32*', 'stm32_assert*']
        return scan_command(args.path, rules_dir, args.format, args.exclude)
    elif args.command == 'fix':
        if args.exclude is None:
            args.exclude = ['HALLIB', 'CORE', 'OBJ',
                           'stm32l4*', 'core_cm*', 'cmsis_*',
                           'system_stm32*', 'stm32_assert*']
        return fix_command(args.path, rules_dir, args.exclude,
                          dry_run=not args.apply)
    elif args.command == 'check':
        return check_command(args.path, os.path.abspath(args.contract))
    elif args.command == 'plan':
        if args.execute:
            return execute_command(args.path)
        return plan_command(args.path)
    elif args.command == 'rules':
        return list_rules_command(rules_dir)

    return 0


if __name__ == '__main__':
    sys.exit(main())
