"""
Engine: planner — 项目分析 → 芯片检测 → 规则推荐 → 执行计划

用法:
    planner = Planner()
    plan = planner.analyze("path/to/project")
    print(plan.summary())
"""

import os
import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from ..skills.fs import ProjectFS
from ..skills.parse import Parser as SkillParser
from ..loader import load_all_rules
from ..models import Rule


@dataclass
class Plan:
    """分析结果 + 执行建议"""
    project_path: str
    chip_detected: Optional[str] = None
    chip_vendor: Optional[str] = None
    library: Optional[str] = None
    recommended_rules: List[str] = field(default_factory=list)
    recommended_contract: Optional[str] = None
    estimated_errors: int = 0
    estimated_warnings: int = 0
    estimated_fixable: int = 0
    execution_order: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    file_count: int = 0

    def summary(self) -> str:
        """格式化的计划摘要"""
        C = '\033[96m'; R = '\033[0m'; B = '\033[1m'
        G = '\033[92m'; Y = '\033[93m'; D = '\033[2m'

        lines = [
            f"{B}{C}EmbedAI Guard — Project Plan{R}",
            f"  Project: {self.project_path}",
            f"  Source files: {self.file_count}",
            "",
        ]

        if self.chip_detected:
            lines.append(f"  {B}Chip:{R} {G}{self.chip_detected}{R}"
                         + (f" ({self.chip_vendor})" if self.chip_vendor else ""))
        if self.library:
            lines.append(f"  {B}Library:{R} {self.library}")

        if self.warnings:
            lines.append(f"\n  {Y}{B}Warnings:{R}")
            for w in self.warnings:
                lines.append(f"    {Y}⚠{R} {w}")

        if self.recommended_rules:
            lines.append(f"\n  {B}Recommended Rules ({len(self.recommended_rules)}):{R}")
            lines.append(f"    {D}{', '.join(self.recommended_rules)}{R}")

        if self.recommended_contract:
            lines.append(f"\n  {B}Recommended Contract:{R}")
            lines.append(f"    {self.recommended_contract}")

        lines.append(f"\n  {B}Estimated:{R}")
        lines.append(f"    {Y if self.estimated_errors else G}"
                     f"ERROR: {self.estimated_errors}{R}"
                     f"  WARNING: {self.estimated_warnings}"
                     f"  Fixable: ~{self.estimated_fixable}")

        if self.execution_order:
            arrows = ' → '.join(self.execution_order)
            lines.append(f"\n  {B}Execution Plan:{R}")
            lines.append(f"    {G}{arrows}{R}")

        return '\n'.join(lines)


# ── 芯片/库检测模式 ──────────────────────

_CHIP_PATTERNS = [
    # (chip_name, vendor, patterns_in_code)
    ('STM32L475VET6', 'ST', ['stm32l475', 'STM32L475', 'STM32L4']),
    ('STM32L4xx', 'ST', ['stm32l4xx_hal', 'STM32L4']),
    ('CIU32F003F5U6', '华大电子', ['ciu32f003', 'CIU32F003']),
    ('YS32F003', '汇春', ['ys32f003', 'YS32F003']),
]

_LIB_PATTERNS = [
    ('HAL', ['HAL_GPIO_Init', 'stm32l4xx_hal']),
    ('std_gpio', ['std_gpio_init']),
    ('标准外设库', ['GPIO_InitTypeDef', 'GPIO_Init']),
]

# 规则触发条件：(rule_id, patterns_in_code, 说明)
_RULE_TRIGGERS = [
    ('EMBED-001', ['delay_ms', 'delay_us', 'HAL_Delay'], '阻塞延时'),
    ('EMBED-002', ['malloc', 'free', 'calloc'], '动态内存'),
    ('EMBED-003', [], '乘除运算'),  # 总是推荐（几乎总有）
    ('EMBED-004', ['IRQHandler'], 'ISR 函数'),
    ('EMBED-005', ['float ', 'double '], '浮点类型'),
    ('EMBED-006', ['HAL_', 'std_'], '未检查返回值'),
    ('EMBED-007', [], 'volatile'),  # 总是推荐
    ('EMBED-008', [], '空指针'),
    ('EMBED-009', ['[]'], '数组越界'),
    ('EMBED-010', [], '未初始化'),
    ('EMBED-011', ['uint8_t', 'uint16_t', 'u8 ', 'u16 '], '整数溢出'),
    ('EMBED-012', ['uint8_t', 'uint16_t', 'unsigned '], '冗余比较'),
    ('EMBED-015', ['switch '], 'switch default'),
]


class Planner:
    """项目分析器：检测芯片/库 → 推荐规则 → 预估违规 → 输出计划"""

    def __init__(self, rules_dir: str = None, plugin_dir: str = None):
        if rules_dir is None:
            rules_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'rules')
        if plugin_dir is None:
            plugin_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'plugins')
        self.rules_dir = rules_dir
        self.plugin_dir = plugin_dir
        self._parser = SkillParser()

    def analyze(self, project_path: str) -> Plan:
        """分析项目，生成执行计划"""
        project_path = os.path.abspath(project_path)
        plan = Plan(project_path=project_path)

        # 1. 遍历文件
        fs = ProjectFS(project_path)
        files = fs.find_files()
        plan.file_count = len(files)

        if plan.file_count == 0:
            plan.warnings.append("未找到 .c/.h 源文件")
            return plan

        # 2. 采样分析（前 20 个文件 + 所有 main/conf 文件）
        sample_files = self._sample_files(files)
        combined_source = ""
        for fp in sample_files:
            src = fs.read(fp)
            if src:
                combined_source += src + "\n"

        # 3. 检测芯片
        for chip, vendor, patterns in _CHIP_PATTERNS:
            if any(p in combined_source for p in patterns):
                plan.chip_detected = chip
                plan.chip_vendor = vendor
                break

        # 4. 检测库
        for lib, patterns in _LIB_PATTERNS:
            if any(p in combined_source for p in patterns):
                plan.library = lib
                break

        # 5. 推荐规则
        for rule_id, triggers, desc in _RULE_TRIGGERS:
            if not triggers or any(t in combined_source for t in triggers):
                plan.recommended_rules.append(rule_id)

        # 6. 推荐契约
        if plan.chip_detected:
            contract_name = plan.chip_detected.lower().replace('xx', '')
            candidates = [
                f for f in os.listdir(self.plugin_dir)
                if f.endswith('.json') and contract_name[:6] in f.lower()
            ]
            if candidates:
                plan.recommended_contract = os.path.normpath(
                    os.path.join(self.plugin_dir, candidates[0]))
            else:
                plan.warnings.append(f"未找到 {plan.chip_detected} 的芯片契约")

        # 7. 快速预估（采样扫描 5 个文件）
        plan.estimated_errors, plan.estimated_warnings, plan.estimated_fixable = \
            self._estimate(fs, sample_files[:5])

        # 8. 执行顺序
        plan.execution_order = ['scan']
        if plan.estimated_fixable > 0:
            plan.execution_order.append('fix')
        if plan.recommended_contract:
            plan.execution_order.append('check')

        return plan

    def _sample_files(self, files: List[str]) -> List[str]:
        """采样关键文件：main + config + 前 N 个"""
        key = [f for f in files
               if any(k in f.lower() for k in ['main', 'conf', 'init', 'system'])]
        rest = [f for f in files if f not in key]
        return (key + rest)[:20]

    def execute(self, project_path: str) -> Tuple['Plan', dict]:
        """执行完整计划：scan → fix → check，返回 (plan, results)"""
        plan = self.analyze(project_path)
        results = {}

        from ..scanner import Scanner
        from ..loader import load_all_rules
        from ..fixer import generate_fixes, apply_fixes, format_diff, FixResult
        from ..contract import (ChipContract, extract_pin_assignments,
                                validate_assignments, validate_clocks,
                                validate_dma, validate_interrupts)
        from ..skills.report import ReportStore

        fs = ProjectFS(project_path)
        rules = load_all_rules(self.rules_dir)

        # ── Step 1: Scan ──
        scanner = Scanner(rules, self._parser)
        store = ReportStore()
        for fp, source in fs.iter_files():
            violations = scanner.scan_file(fp)
            store.add(violations)
        results['scan'] = store

        # ── Step 2: Fix ──
        if store.violations:
            fixable_ids = {r.id for r in rules if r.auto_fix}
            fixable = [v for v in store.violations if v.rule_id in fixable_ids]
            if fixable:
                source_by_file = {}
                for fp in fs.find_files():
                    src = fs.read(fp)
                    if src:
                        source_by_file[fp] = src
                fix_result = generate_fixes(fixable, source_by_file)
                # 自动应用修复
                apply_fixes(fix_result.fixes, source_by_file, dry_run=False)
                results['fix'] = fix_result

        # ── Step 3: Check ──
        if plan.recommended_contract:
            contract = ChipContract.from_file(plan.recommended_contract)
            all_assignments = []
            all_violations = []
            src_files = [f for f in fs.find_files() if f.endswith('.c')]
            for fp in src_files:
                src = fs.read(fp)
                if src:
                    assigns = extract_pin_assignments(src, fp, self._parser)
                    all_assignments.extend(assigns)
                    all_violations += validate_clocks(src, fp, contract, self._parser)
                    all_violations += validate_dma(src, fp, contract, self._parser)
                    all_violations += validate_interrupts(src, fp, contract, self._parser)
            if all_assignments:
                cr = validate_assignments(all_assignments, contract)
                all_violations += cr.violations
                results['check_pins'] = all_assignments
            results['check'] = all_violations

        return plan, results

    def _estimate(self, fs: ProjectFS, sample_files: List[str]
                  ) -> Tuple[int, int, int]:
        """采样扫描预估违规数"""
        from ..scanner import Scanner
        rules = load_all_rules(self.rules_dir)
        scanner = Scanner(rules, self._parser)

        total_errors = 0
        total_warnings = 0
        total_fixable = 0

        for fp in sample_files[:5]:
            violations = scanner.scan_file(fp)
            for v in violations:
                if v.severity.value == 'error':
                    total_errors += 1
                else:
                    total_warnings += 1
                    if v.rule_id in ('EMBED-003', 'EMBED-010'):
                        total_fixable += 1

        return total_errors, total_warnings, total_fixable
