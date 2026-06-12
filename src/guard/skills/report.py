"""
Skill: report — 违规存储 + 多格式输出

用法:
    store = ReportStore()
    store.add(violations)
    print(store.format('terminal'))
"""

import os
import json
from typing import List, Dict
from ..models import Violation, Severity


class ReportStore:
    """违规数据存储，支持增量更新和多格式输出"""

    def __init__(self):
        self.violations: List[Violation] = []
        self.errors: List[str] = []

    def add(self, violations: List[Violation]):
        self.violations.extend(violations)

    def add_error(self, msg: str):
        self.errors.append(msg)

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

    def format(self, fmt: str = 'terminal') -> str:
        if fmt == 'json':
            return self._format_json()
        elif fmt == 'junit':
            return self._format_junit()
        return self._format_terminal()

    def _format_terminal(self) -> str:
        RESET  = '\033[0m'
        RED    = '\033[91m'
        GREEN  = '\033[92m'
        YELLOW = '\033[93m'
        CYAN   = '\033[96m'
        BOLD   = '\033[1m'
        DIM    = '\033[2m'

        lines = [f"{BOLD}{CYAN}EmbedAI Guard — Scan Results{RESET}",
                 f"  {RED}ERROR: {self.error_count}{RESET}  "
                 f"{YELLOW}WARNING: {self.warning_count}{RESET}  "
                 f"INFO: {self.info_count}", ""]

        if not self.violations:
            lines.append(f"  {GREEN}{BOLD}✓ All checks passed!{RESET}")
            return "\n".join(lines)

        by_file: dict = {}
        for v in self.violations:
            by_file.setdefault(v.file, []).append(v)

        for fp, file_v in sorted(by_file.items()):
            fname = os.path.basename(fp)
            lines.append(f"  {BOLD}{fname}{RESET}  ({len(file_v)} issue(s))")
            for v in file_v:
                c = RED if v.severity == Severity.ERROR else (
                    YELLOW if v.severity == Severity.WARNING else DIM)
                lines.append(
                    f"    {c}{v.severity.value.upper():7s}{RESET} "
                    f"L{v.line:4d}:{v.column:<3d}  [{v.rule_id}]  {v.message}")
                if v.code_snippet:
                    lines.append(f"            {DIM}> {v.code_snippet.strip()[:120]}{RESET}")
            lines.append("")

        if self.errors:
            lines.append(f"  {RED}Parse errors ({len(self.errors)}):{RESET}")
            for e in self.errors[:5]:
                lines.append(f"    {DIM}{e}{RESET}")

        if self.passed:
            lines.append(f"  {GREEN}{BOLD}✓ 0 errors — PASSED{RESET}")
        else:
            lines.append(f"  {RED}{BOLD}✗ {self.error_count} error(s) — FIX REQUIRED{RESET}")
        return "\n".join(lines)

    def _format_json(self) -> str:
        return json.dumps({
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "passed": self.passed,
            "violations": [
                {"rule_id": v.rule_id, "severity": v.severity.value,
                 "file": v.file, "line": v.line, "column": v.column,
                 "message": v.message, "suggestion": v.suggestion,
                 "code_snippet": v.code_snippet}
                for v in self.violations
            ],
            "errors": self.errors,
        }, indent=2, ensure_ascii=False)

    def _format_junit(self) -> str:
        total = len(self.violations)
        failures = self.error_count
        skipped = self.warning_count + self.info_count
        xml = [f'<?xml version="1.0" encoding="UTF-8"?>',
               f'<testsuite name="EmbedAI Guard" tests="{total}" '
               f'failures="{failures}" errors="0" skipped="{skipped}">']
        for v in self.violations:
            fname = os.path.basename(v.file)
            xml.append(f'  <testcase classname="{v.rule_id}" '
                       f'name="{fname}:{v.line}:{v.column}" time="0">')
            if v.severity == Severity.ERROR:
                xml.append(f'    <failure message="{v.message}" '
                           f'type="{v.rule_id}"/>')
            xml.append('  </testcase>')
        xml.append('</testsuite>')
        return '\n'.join(xml)
