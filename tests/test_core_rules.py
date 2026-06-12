"""
EmbedAI Guard — 核心规则单元测试
每个规则至少覆盖: 正确检出、正确放过、边界情况
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from guard.models import Severity, Violation, ScanResult
from guard.loader import load_all_rules, load_rule_from_yaml
from guard.scanner import Scanner


# ── 辅助函数 ──────────────────────────────

RULES_DIR = os.path.join(os.path.dirname(__file__), '..', 'src', 'rules')


def scan_str(code: str, rule_ids: list = None) -> list:
    """对代码字符串执行指定规则的扫描"""
    all_rules = load_all_rules(RULES_DIR)
    if rule_ids:
        rules = [r for r in all_rules if r.id in rule_ids]
    else:
        rules = all_rules
    scanner = Scanner(rules)
    tree = scanner._parser.parse(code, "<test>")
    lines = tree.lines
    violations = []
    for rule in rules:
        violations += scanner._check_ast(tree.root_node, rule, "<test>", lines)
        violations += scanner._check_text(code, rule, "<test>", lines)
        if rule.id in ('EMBED-008', 'EMBED-009', 'EMBED-010', 'EMBED-011', 'EMBED-012', 'EMBED-015'):
            violations += scanner._check_function_level(tree.root_node, rule, "<test>", lines)
    return violations


def violation_count(violations: list, rule_id: str) -> int:
    return sum(1 for v in violations if v.rule_id == rule_id)


# ── EMBED-001: 禁止阻塞延时 ─────────────────

class TestNoDelay:
    def test_detect_delay_ms(self):
        v = scan_str("void f(void) { delay_ms(500); }", ["EMBED-001"])
        assert violation_count(v, "EMBED-001") == 1

    def test_detect_hal_delay(self):
        v = scan_str("void f(void) { HAL_Delay(100); }", ["EMBED-001"])
        assert violation_count(v, "EMBED-001") == 1

    def test_skip_function_definition(self):
        """delay_ms 函数定义本身不应被标记"""
        v = scan_str("void delay_ms(u16 nms) { /* impl */ }", ["EMBED-001"])
        assert violation_count(v, "EMBED-001") == 0

    def test_skip_delay_init_definition(self):
        v = scan_str("void delay_init(u8 sysclk) { /* impl */ }", ["EMBED-001"])
        assert violation_count(v, "EMBED-001") == 0

    def test_no_delay_call(self):
        v = scan_str("void f(void) { int x = 1; }", ["EMBED-001"])
        assert violation_count(v, "EMBED-001") == 0


# ── EMBED-002: 禁止动态内存 ─────────────────

class TestNoMalloc:
    def test_detect_malloc(self):
        v = scan_str("void f(void) { void *p = malloc(100); }", ["EMBED-002"])
        assert violation_count(v, "EMBED-002") == 1

    def test_detect_free(self):
        v = scan_str("void f(void) { free(p); }", ["EMBED-002"])
        assert violation_count(v, "EMBED-002") == 1

    def test_no_malloc(self):
        v = scan_str("static uint8_t buf[100];", ["EMBED-002"])
        assert violation_count(v, "EMBED-002") == 0


# ── EMBED-003: 禁止乘除 ─────────────────────

class TestNoMultDiv:
    def test_detect_divide(self):
        v = scan_str("int f(int a) { return a / 2; }", ["EMBED-003"])
        assert violation_count(v, "EMBED-003") == 1

    def test_detect_multiply(self):
        v = scan_str("int f(int a) { return a * 4; }", ["EMBED-003"])
        assert violation_count(v, "EMBED-003") == 1

    def test_skip_shift(self):
        """移位不是违规"""
        v = scan_str("int f(int a) { return a << 2; }", ["EMBED-003"])
        assert violation_count(v, "EMBED-003") == 0

    def test_skip_add(self):
        """加法不是违规"""
        v = scan_str("int f(int a) { return a + 2; }", ["EMBED-003"])
        assert violation_count(v, "EMBED-003") == 0


# ── EMBED-004: ISR 简洁性 ────────────────────

class TestIsrLean:
    def test_detect_large_isr(self):
        code = "void USART1_IRQHandler(void) {\n" + "  int x;\n" * 15 + "}"
        v = scan_str(code, ["EMBED-004"])
        assert violation_count(v, "EMBED-004") == 1

    def test_skip_small_isr(self):
        code = "void EXTI0_IRQHandler(void) {\n  flag = 1;\n}"
        v = scan_str(code, ["EMBED-004"])
        assert violation_count(v, "EMBED-004") == 0

    def test_skip_normal_function(self):
        """普通函数即使大也不应标记"""
        code = "void big_func(void) {\n" + "  int x;\n" * 20 + "}"
        v = scan_str(code, ["EMBED-004"])
        assert violation_count(v, "EMBED-004") == 0


# ── EMBED-005: 禁止浮点 ──────────────────────

class TestNoFloat:
    def test_detect_float_var(self):
        v = scan_str("void f(void) { float temp; }", ["EMBED-005"])
        assert violation_count(v, "EMBED-005") == 1

    def test_skip_comment_float(self):
        """注释中的 'float' 不应标记"""
        v = scan_str("// float is a type\nvoid f(void) { int x; }", ["EMBED-005"])
        assert violation_count(v, "EMBED-005") == 0

    def test_no_float(self):
        v = scan_str("void f(void) { int temp; }", ["EMBED-005"])
        assert violation_count(v, "EMBED-005") == 0


# ── EMBED-006: 未检查返回值 ──────────────────

class TestUncheckedReturn:
    def test_detect_unchecked_hal(self):
        v = scan_str("void f(void) {\n    HAL_GPIO_Init(GPIOA, &cfg);\n}", ["EMBED-006"])
        assert violation_count(v, "EMBED-006") == 1

    def test_skip_if_checked(self):
        """返回值被检查（if语句）的情况"""
        v = scan_str("void f(void) {\n    if (HAL_GPIO_Init(GPIOA, &cfg) != HAL_OK) return;\n}", ["EMBED-006"])
        assert violation_count(v, "EMBED-006") == 0

    def test_skip_assigned(self):
        """返回值被赋值的情况"""
        v = scan_str("void f(void) {\n    HAL_StatusTypeDef ret = HAL_GPIO_Init(GPIOA, &cfg);\n}", ["EMBED-006"])
        assert violation_count(v, "EMBED-006") == 0


# ── EMBED-007: 缺失 volatile ─────────────────

class TestMissingVolatile:
    def test_detect_global_var(self):
        v = scan_str("int flag;", ["EMBED-007"])
        assert violation_count(v, "EMBED-007") == 1

    def test_skip_static(self):
        v = scan_str("static int flag;", ["EMBED-007"])
        assert violation_count(v, "EMBED-007") == 0

    def test_skip_volatile(self):
        v = scan_str("volatile int flag;", ["EMBED-007"])
        assert violation_count(v, "EMBED-007") == 0

    def test_skip_local(self):
        """局部变量不需要 volatile"""
        v = scan_str("void f(void) { int flag; }", ["EMBED-007"])
        assert violation_count(v, "EMBED-007") == 0


# ── EMBED-008: 空指针 ─────────────────────────

class TestNullPointer:
    def test_detect_unchecked_ptr(self):
        code = "void f(TIM_TypeDef *htim) {\n    if (htim->Instance == TIM3) {}\n}"
        v = scan_str(code, ["EMBED-008"])
        assert violation_count(v, "EMBED-008") == 1

    def test_skip_if_null_checked(self):
        code = "void f(TIM_TypeDef *htim) {\n    if (htim == NULL) return;\n    if (htim->Instance == TIM3) {}\n}"
        v = scan_str(code, ["EMBED-008"])
        assert violation_count(v, "EMBED-008") == 0

    def test_skip_non_ptr_param(self):
        code = "void f(int x) {\n    if (x > 5) {}\n}"
        v = scan_str(code, ["EMBED-008"])
        assert violation_count(v, "EMBED-008") == 0


# ── EMBED-009: 数组越界 ───────────────────────

class TestArrayBounds:
    def test_detect_unchecked_array(self):
        code = "void f(void) {\n    int arr[10];\n    arr[3] = 1;\n}"
        v = scan_str(code, ["EMBED-009"])
        assert violation_count(v, "EMBED-009") == 1

    def test_skip_if_bounds_checked(self):
        code = "void f(void) {\n    int arr[10];\n    if (idx < 10) arr[idx] = 1;\n}"
        v = scan_str(code, ["EMBED-009"])
        assert violation_count(v, "EMBED-009") == 0

    def test_no_array(self):
        code = "void f(void) { int x = 1; }"
        v = scan_str(code, ["EMBED-009"])
        assert violation_count(v, "EMBED-009") == 0


# ── EMBED-010: 未初始化变量 ────────────────────

class TestUninitialized:
    def test_detect_uninit_use(self):
        code = "void f(void) {\n    int x;\n    if (x > 5) {}\n}"
        v = scan_str(code, ["EMBED-010"])
        assert violation_count(v, "EMBED-010") == 1

    def test_skip_if_assigned_before_use(self):
        code = "void f(void) {\n    int x;\n    x = 10;\n    if (x > 5) {}\n}"
        v = scan_str(code, ["EMBED-010"])
        assert violation_count(v, "EMBED-010") == 0

    def test_skip_initialized(self):
        code = "void f(void) { int x = 0; if (x > 5) {} }"
        v = scan_str(code, ["EMBED-010"])
        assert violation_count(v, "EMBED-010") == 0


# ── EMBED-011: 整数溢出 ────────────────────────

class TestOverflow:
    def test_detect_uint8_inc(self):
        code = "void f(void) { uint8_t i; i++; }"
        v = scan_str(code, ["EMBED-011"])
        assert violation_count(v, "EMBED-011") == 1

    def test_detect_uint16_add_assign(self):
        code = "void f(void) { uint16_t i; i += 1; }"
        v = scan_str(code, ["EMBED-011"])
        assert violation_count(v, "EMBED-011") == 1

    def test_skip_uint32(self):
        """uint32_t 不太可能溢出，不标记"""
        code = "void f(void) { uint32_t i; i++; }"
        v = scan_str(code, ["EMBED-011"])
        assert violation_count(v, "EMBED-011") == 0


# ── EMBED-012: 冗余比较 ────────────────────────

class TestTautological:
    def test_detect_u8_ge_zero(self):
        code = "void f(void) { uint8_t x; if (x >= 0) {} }"
        v = scan_str(code, ["EMBED-012"])
        assert violation_count(v, "EMBED-012") == 1

    def test_detect_u16_lt_zero(self):
        code = "void f(void) { uint16_t x; if (x < 0) {} }"
        v = scan_str(code, ["EMBED-012"])
        assert violation_count(v, "EMBED-012") == 1

    def test_skip_signed_type(self):
        code = "void f(void) { int x; if (x < 0) {} }"
        v = scan_str(code, ["EMBED-012"])
        assert violation_count(v, "EMBED-012") == 0


# ── 集成测试 ───────────────────────────────────

class TestIntegration:
    def test_load_all_rules(self):
        rules = load_all_rules(RULES_DIR)
        assert len(rules) >= 12

    def test_scanner_no_crash(self):
        """确保扫描器不崩溃"""
        code = "void main(void) { int x = 0; x++; }"
        v = scan_str(code)
        assert isinstance(v, list)

    def test_auto_fix_flags(self):
        """确认可自动修复的规则已启用"""
        rules = load_all_rules(RULES_DIR)
        auto_fixable = [r for r in rules if r.auto_fix]
        assert len(auto_fixable) >= 2  # EMBED-003, EMBED-010
