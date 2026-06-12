"""
芯片契约验证引擎：引脚冲突 / 时钟配置 / DMA 通道 / 中断优先级检测
"""

import json
import os
import re
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field

from tree_sitter import Node
from .skills.parse import Parser as SkillParser, SourceTree


# ── 数据结构 ──────────────────────────────

@dataclass
class PinAssignment:
    """从代码中提取的一个引脚配置"""
    port: str           # GPIOA, GPIOB, ...
    pin: int            # 0-15
    pin_name: str       # PA0, PB1, ...
    function: str       # GPIO, USART1_TX, SPI1_SCK, ...
    af: Optional[int]   # AF 号 (0-15)
    file: str           # 源文件
    line: int           # 行号

@dataclass
class ContractViolation:
    """契约违规"""
    severity: str       # error / warning
    category: str       # pin_conflict / clock / dma / interrupt
    message: str
    file: str
    line: int
    detail: str = ""


@dataclass
class ContractResult:
    """契约验证结果"""
    violations: List[ContractViolation] = field(default_factory=list)
    assignments: List[PinAssignment] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == 'error')

    @property
    def passed(self) -> bool:
        return self.error_count == 0


# ── 契约加载 ──────────────────────────────

class ChipContract:
    """芯片契约：引脚复用 / DMA / 中断 / 时钟约束"""

    def __init__(self, data: dict):
        self.data = data
        self.pins: dict = data.get('pins', {})
        self.dma: dict = data.get('dma', {})
        self.interrupts: dict = data.get('interrupts', {})
        self.clocks: dict = data.get('clocks', {})

    @classmethod
    def from_file(cls, path: str) -> 'ChipContract':
        with open(path, 'r', encoding='utf-8') as f:
            return cls(json.load(f))

    def get_pin_functions(self, pin_name: str) -> dict:
        """获取引脚的所有可用功能 {function: af_number}"""
        pin = self.pins.get(pin_name.upper())
        if not pin:
            return {}
        return pin.get('functions', {})

    def is_valid_function(self, pin_name: str, function: str) -> bool:
        """检查某个功能是否可用于该引脚（支持前缀匹配 TIM2→TIM2_CH1）"""
        funcs = self.get_pin_functions(pin_name)
        if function in funcs:
            return True
        # 前缀匹配：TIM2 匹配 TIM2_CH1, TIM2_CH2, ...
        for fname in funcs:
            if fname.startswith(function + '_') or fname.startswith(function):
                return True
        return False

    def get_af_for_function(self, pin_name: str, function: str) -> Optional[int]:
        """获取功能对应的 AF 号"""
        funcs = self.get_pin_functions(pin_name)
        return funcs.get(function)

    def is_5v_tolerant(self, pin_name: str) -> bool:
        pin = self.pins.get(pin_name.upper())
        if not pin:
            return False
        return pin.get('5v_tolerant', False)

    def get_note(self, pin_name: str) -> Optional[str]:
        pin = self.pins.get(pin_name.upper())
        if not pin:
            return None
        return pin.get('note')


# ── 库模式注册 ────────────────────────────

# GPIO 初始化函数模式: (函数名匹配, AF 解析方式)
_GPIO_INIT_PATTERNS = [
    # STM32 HAL: HAL_GPIO_Init(GPIOA, &GPIO_Initure)
    # AF 命名: GPIO_AF7_USART1 → AF=7, 功能=USART1
    {
        'init_func': r'HAL_GPIO_Init',
        'af_pattern': r'GPIO_AF(\d+)_(\w+)',
    },
    # CIU32F003 std lib: std_gpio_init(GPIOA, &gpio_config)
    # AF 命名: GPIO_AF1_UART1 → AF=1, 功能=UART1
    {
        'init_func': r'std_gpio_init',
        'af_pattern': r'GPIO_AF(\d+)_(\w+)',
    },
    # YS32F003 / STM32 标准外设库: GPIO_Init(GPIOA, &GPIO_InitStruct)
    # AF 可能直接使用数值或宏
    {
        'init_func': r'\bGPIO_Init\b',
        'af_pattern': r'GPIO_AF(\d+)_?(\w*)',
    },
]


def _detect_library(source: str) -> dict:
    """检测代码使用的库，返回对应的模式"""
    for pattern in _GPIO_INIT_PATTERNS:
        if re.search(pattern['init_func'], source):
            return pattern
    return _GPIO_INIT_PATTERNS[0]  # 默认 HAL


# ── AST 引脚配置提取 ───────────────────────

# GPIO 端口 → 寄存器基址的映射
_PORT_CLK_ENABLE_MAP = {
    'GPIOA': 'AHB2', 'GPIOB': 'AHB2', 'GPIOC': 'AHB2',
    'GPIOD': 'AHB2', 'GPIOE': 'AHB2', 'GPIOF': 'AHB2',
    'GPIOG': 'AHB2', 'GPIOH': 'AHB2',
}

# GPIO_PIN_x → pin number
_PIN_MAP = {
    'GPIO_PIN_0': 0, 'GPIO_PIN_1': 1, 'GPIO_PIN_2': 2, 'GPIO_PIN_3': 3,
    'GPIO_PIN_4': 4, 'GPIO_PIN_5': 5, 'GPIO_PIN_6': 6, 'GPIO_PIN_7': 7,
    'GPIO_PIN_8': 8, 'GPIO_PIN_9': 9, 'GPIO_PIN_10': 10, 'GPIO_PIN_11': 11,
    'GPIO_PIN_12': 12, 'GPIO_PIN_13': 13, 'GPIO_PIN_14': 14, 'GPIO_PIN_15': 15,
    'GPIO_PIN_All': -1,
}

# GPIO_AFx_XXX → AF number
def _parse_af_from_name(name: str, lib: dict = None) -> Optional[int]:
    """从 GPIO_AF7_USART1 或 GPIO_AF1_UART1 提取 AF 号"""
    pattern = lib.get('af_pattern', r'GPIO_AF(\d+)_(\w+)') if lib else r'GPIO_AF(\d+)_(\w+)'
    m = re.search(pattern, name)
    if m:
        return int(m.group(1))
    return None


def _parse_af_function_name(name: str, lib: dict = None) -> str:
    """从 GPIO_AF7_USART1 提取功能名 USART1"""
    pattern = lib.get('af_pattern', r'GPIO_AF(\d+)_(\w+)') if lib else r'GPIO_AF(\d+)_(\w+)'
    m = re.search(pattern, name)
    if m:
        return m.group(2)
    return name


def extract_pin_assignments(source: str, file_path: str,
                            skill_parser: SkillParser) -> List[PinAssignment]:
    """从 C 源码中提取外设 → 引脚的映射关系（支持 HAL + std_gpio）"""
    assignments = []
    tree = skill_parser.parse(source, file_path)
    root = tree.root_node
    lib = _detect_library(source)

    # 1. 收集所有相关的赋值和 init 调用（按行序）
    events = []  # [(line, type, data)]
    for node in _iter_all(root):
        if node.type == 'call_expression':
            func = node.child_by_field_name('function')
            if not func:
                continue
            func_text = func.text.decode('utf-8', errors='replace')
            if not re.search(lib['init_func'], func_text):
                continue
            args = node.child_by_field_name('arguments')
            if not args:
                continue
            children = [c for c in args.children if c.type not in ('(', ')', ',')]
            if len(children) < 2:
                continue
            port_text = children[0].text.decode('utf-8', errors='replace').strip()
            port_match = re.match(r'(GPIO[A-H])', port_text)
            if not port_match:
                continue
            init_struct = children[1].text.decode('utf-8', errors='replace').strip().lstrip('&')
            events.append((node.start_point[0], 'init', {
                'port': port_match.group(1),
                'struct': init_struct,
                'line': node.start_point[0] + 1,
            }))
        elif node.type == 'assignment_expression':
            left = node.child_by_field_name('left')
            right = node.child_by_field_name('right')
            if not left or not right:
                continue
            left_text = left.text.decode('utf-8', errors='replace')
            right_text = right.text.decode('utf-8', errors='replace').strip()
            # .pin = ...
            if left_text.endswith('.pin') or left_text.endswith('.Pin'):
                struct_name = left_text.rsplit('.', 1)[0]
                pins = set()
                for name, num in _PIN_MAP.items():
                    if re.search(r'\b' + re.escape(name) + r'\b', right_text) and num >= 0:
                        pins.add(num)
                events.append((node.start_point[0], 'pin', {
                    'struct': struct_name, 'pins': pins,
                }))
            # .alternate = ...
            elif left_text.endswith('.alternate') or left_text.endswith('.Alternate'):
                struct_name = left_text.rsplit('.', 1)[0]
                events.append((node.start_point[0], 'af', {
                    'struct': struct_name, 'value': right_text,
                }))

    # 2. 按行序处理事件：为每个 init 调用匹配最近的前置 pin/af 设置
    events.sort()
    struct_state = {}  # struct_name -> {'pins': set(), 'af': 'GPIO'}
    for _line, etype, data in events:
        if etype == 'pin':
            s = data['struct']
            if s not in struct_state:
                struct_state[s] = {'pins': set(), 'af': 'GPIO'}
            struct_state[s]['pins'] = data['pins']  # 覆盖：新的 pin 设置替换旧的
        elif etype == 'af':
            s = data['struct']
            if s not in struct_state:
                struct_state[s] = {'pins': set(), 'af': 'GPIO'}
            struct_state[s]['af'] = data['value']
        elif etype == 'init':
            s = data['struct']
            if s in struct_state and struct_state[s]['pins']:
                state = struct_state[s]
                af_num = _parse_af_from_name(state['af'], lib)
                func_name = (_parse_af_function_name(state['af'], lib)
                             if state['af'].startswith('GPIO_AF') else
                             'GPIO' if state['af'] in ('0U', '0', 'GPIO') else state['af'])
                for pin_num in sorted(state['pins']):
                    pin_name = f"{data['port']}{pin_num}"
                    assignments.append(PinAssignment(
                        port=data['port'],
                        pin=pin_num,
                        pin_name=pin_name,
                        function=func_name,
                        af=af_num,
                        file=file_path,
                        line=data['line'],
                    ))

    return assignments


def _find_init_struct_name(call_node: Node) -> Optional[str]:
    """从 HAL_GPIO_Init(GPIOx, &xxx) 提取结构体名 xxx"""
    args = call_node.child_by_field_name('arguments')
    if not args:
        return None
    children = [c for c in args.children if c.type not in ('(', ')', ',')]
    if len(children) < 2:
        return None
    arg2 = children[1].text.decode('utf-8', errors='replace').strip()
    # 去掉 & 前缀
    if arg2.startswith('&'):
        arg2 = arg2[1:]
    return arg2


def _find_enclosing_function(node: Node) -> Optional[Node]:
    """向上查找包含此节点的函数定义"""
    while node:
        if node.type == 'function_definition':
            return node
        node = node.parent
    return None


def _iter_all(root: Node, node_type: str = None):
    """遍历所有子节点，可选按类型过滤"""
    if node_type is None or root.type == node_type:
        yield root
    for child in root.children:
        yield from _iter_all(child, node_type)


# ── 时钟验证 ──────────────────────────────

def validate_clocks(source: str, file_path: str,
                    contract: ChipContract,
                    skill_parser: SkillParser) -> List[ContractViolation]:
    """验证 SystemClock_Config 中的时钟配置是否在芯片规格内"""
    violations = []
    clocks = contract.clocks
    if not clocks:
        return violations

    tree = skill_parser.parse(source, file_path)
    root = tree.root_node

    # 提取关键参数值
    hse_value = None
    hsi_value = clocks.get('hsi_hz', 16000000)
    pllm = plln = None
    sysclk = None
    flash_latency = None

    for node in _iter_all(root):
        node_text = node.text.decode('utf-8', errors='replace')

        # 检测 HSE_VALUE 定义
        if node.type == 'preproc_def' and 'HSE_VALUE' in node_text:
            m = re.search(r'HSE_VALUE\s+\(?\(?(\d+)\)?\)?', node_text)
            if m:
                hse_value = int(m.group(1))

        # 检测 PLLM/PLLN 配置
        if node.type == 'field_expression':
            text = node_text
            if '.PLLM' in text or '.PLLM=' in text:
                m = re.search(r'=\s*(\d+)', text)
                if m:
                    pllm = int(m.group(1))
            if '.PLLN' in text or '.PLLN=' in text:
                m = re.search(r'=\s*(\d+)', text)
                if m:
                    plln = int(m.group(1))

        # 检测 SystemCoreClock 或 SYSCLK 频率
        if node.type == 'identifier' and node_text == 'SystemCoreClock':
            parent = node.parent
            if parent and parent.type == 'assignment_expression':
                right = parent.child_by_field_name('right')
                if right:
                    val_text = right.text.decode('utf-8', errors='replace').strip()
                    try:
                        sysclk = int(val_text)
                    except ValueError:
                        pass

        # 检测 Flash 延迟
        if node.type == 'call_expression':
            func = node.child_by_field_name('function')
            if func:
                func_text = func.text.decode('utf-8', errors='replace')
                if 'HAL_RCC_ClockConfig' in func_text:
                    args = node.child_by_field_name('arguments')
                    if args:
                        for arg_node in args.children:
                            arg_text = arg_node.text.decode('utf-8', errors='replace')
                            m = re.search(r'FLASH_LATENCY_(\d+)', arg_text)
                            if m:
                                flash_latency = int(m.group(1))

    # 验证
    line = 1

    # HSE 范围检查
    if hse_value:
        hse_min = clocks.get('hse_min_hz', 0)
        hse_max = clocks.get('hse_max_hz', 0)
        if hse_value < hse_min or hse_value > hse_max:
            violations.append(ContractViolation(
                severity='error', category='clock',
                message=f"HSE 频率 {hse_value/1e6:.1f}MHz 超出范围 [{hse_min/1e6:.0f}-{hse_max/1e6:.0f}MHz]",
                file=file_path, line=line))

    # PLL VCO 检查
    if pllm and plln:
        input_freq = hse_value or hsi_value
        vco = input_freq // pllm * plln
        vco_min = clocks.get('pll', {}).get('vco_min_hz', 0)
        vco_max = clocks.get('pll', {}).get('vco_max_hz', 0)
        if vco < vco_min or vco > vco_max:
            violations.append(ContractViolation(
                severity='error', category='clock',
                message=f"PLL VCO {vco/1e6:.1f}MHz 超出范围 [{vco_min/1e6:.0f}-{vco_max/1e6:.0f}MHz] "
                        f"(输入={input_freq/1e6:.1f}MHz, PLLM={pllm}, PLLN={plln})",
                file=file_path, line=line))

    # Flash 等待周期检查
    if sysclk and flash_latency is not None:
        flash_table = clocks.get('flash_latency', [])
        expected_ws = None
        for entry in flash_table:
            if sysclk <= entry[1]:
                expected_ws = entry[2]
                break
        if expected_ws is not None and expected_ws != flash_latency:
            violations.append(ContractViolation(
                severity='warning', category='clock',
                message=f"Flash 等待周期可能不匹配: HCLK={sysclk/1e6:.0f}MHz, "
                        f"代码中 FLASH_LATENCY={flash_latency}, 手册建议={expected_ws}",
                file=file_path, line=line))

    # APB 总线限制检查
    bus_max = clocks.get('bus_max_hz', {})
    if sysclk:
        for bus, max_hz in bus_max.items():
            if sysclk > max_hz:
                violations.append(ContractViolation(
                    severity='error', category='clock',
                    message=f"系统时钟 {sysclk/1e6:.0f}MHz 超过 {bus} 总线最大频率 {max_hz/1e6:.0f}MHz",
                    file=file_path, line=line))

    return violations


# ── DMA 验证 ───────────────────────────────

def validate_dma(source: str, file_path: str,
                 contract: ChipContract,
                 skill_parser: SkillParser) -> List[ContractViolation]:
    """检测 DMA 通道使用是否存在冲突"""
    violations = []
    dma_map = contract.dma
    if not dma_map:
        return violations

    # 从代码中提取外设列表（简单字符串扫描，比 AST 更适合这种宏观级别）
    dma_used_peripherals = set()

    # 检测 __HAL_RCC_DMAx_CLK_ENABLE
    dma_channels_used = set()
    for dma_name in ['DMA1', 'DMA2']:
        if f'__HAL_RCC_{dma_name}_CLK_ENABLE' in source:
            dma_channels_used.add(dma_name)

    # 检测哪些外设被使用，可用于交叉检查该外设的 DMA 通道是否可用
    for peripheral_set in dma_map.values():
        for channel, peripherals in peripheral_set.items():
            for periph in peripherals:
                if periph in source or periph.replace('_TX', '').replace('_RX', '') in source:
                    dma_used_peripherals.add(periph)

    # 如果使用了 SPI 但没使能对应 DMA，提示
    for periph in dma_used_peripherals:
        found_in_channel = False
        for dma, channels in dma_map.items():
            for ch, periphs in channels.items():
                if periph in periphs:
                    found_in_channel = True
                    if dma not in dma_channels_used:
                        violations.append(ContractViolation(
                            severity='warning', category='dma',
                            message=f"外设 {periph} 支持 DMA ({dma}/{ch})，但未检测到 {dma} 时钟使能",
                            file=file_path, line=1))
                    break
        if not found_in_channel:
            violations.append(ContractViolation(
                severity='warning', category='dma',
                message=f"外设 {periph} 在 DMA 映射表中未找到对应的 DMA 通道",
                file=file_path, line=1))

    return violations


# ── 中断验证 ──────────────────────────────

def validate_interrupts(source: str, file_path: str,
                        contract: ChipContract,
                        skill_parser: SkillParser) -> List[ContractViolation]:
    """检测中断优先级是否违反 FreeRTOS/裸机约束"""
    violations = []
    intr = contract.interrupts
    if not intr:
        return violations

    tree = skill_parser.parse(source, file_path)
    root = tree.root_node

    freertos_max = intr.get('freertos_max_syscall_priority', 5)

    # 查找 HAL_NVIC_SetPriority(IRQn, PreemptPriority, SubPriority)
    for node in _iter_all(root, 'call_expression'):
        func = node.child_by_field_name('function')
        if not func:
            continue
        func_text = func.text.decode('utf-8', errors='replace')
        if 'NVIC_SetPriority' not in func_text and 'HAL_NVIC_SetPriority' not in func_text:
            continue

        args = node.child_by_field_name('arguments')
        if not args:
            continue

        # 提取参数
        arg_children = [c for c in args.children if c.type not in ('(', ')', ',')]
        if len(arg_children) < 2:
            continue

        irq_name = arg_children[0].text.decode('utf-8', errors='replace').strip()
        # 提取优先级数字
        prio_text = arg_children[1].text.decode('utf-8', errors='replace').strip()
        try:
            prio_val = int(prio_text)
        except ValueError:
            # 可能是表达式，尝试简单提取
            m = re.search(r'(\d+)', prio_text)
            prio_val = int(m.group(1)) if m else None

        if prio_val is not None and prio_val <= freertos_max:
            # 检测是否使用 FreeRTOS（有 FreeRTOS 头文件则 error，否则 warning）
            uses_freertos = 'FreeRTOS' in source or 'taskENTER_CRITICAL' in source
            sev = 'error' if uses_freertos else 'warning'
            violations.append(ContractViolation(
                severity=sev,
                category='interrupt',
                message=f"中断 {irq_name} 优先级 {prio_val} ≤ "
                        f"configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY ({freertos_max})。"
                        f"在 FreeRTOS 中，ISR 优先级应严格大于 {freertos_max}",
                file=file_path,
                line=node.start_point[0] + 1,
            ))

    # 检查优先级分组
    for node in _iter_all(root, 'call_expression'):
        func = node.child_by_field_name('function')
        if not func:
            continue
        func_text = func.text.decode('utf-8', errors='replace')
        if 'NVIC_PriorityGroupConfig' not in func_text:
            continue

        args = node.child_by_field_name('arguments')
        if args:
            arg_text = args.text.decode('utf-8', errors='replace').strip('() ')
            if 'NVIC_PriorityGroup_4' not in arg_text:
                violations.append(ContractViolation(
                    severity='warning', category='interrupt',
                    message=f"FreeRTOS 建议使用 NVIC_PriorityGroup_4（4 位抢占优先级），"
                            f"当前配置为 {arg_text}",
                    file=file_path,
                    line=node.start_point[0] + 1,
                ))

    return violations


# ── 冲突检测 ──────────────────────────────

def validate_assignments(assignments: List[PinAssignment],
                          contract: ChipContract) -> ContractResult:
    """验证引脚分配是否违反芯片契约"""
    result = ContractResult(assignments=assignments)

    # 按引脚分组
    by_pin: Dict[str, List[PinAssignment]] = {}
    for a in assignments:
        by_pin.setdefault(a.pin_name, []).append(a)

    # 检测冲突
    for pin_name, assigns in sorted(by_pin.items()):
        if len(assigns) == 1:
            continue

        # 同一引脚被分配了不同功能
        functions = set(a.function for a in assigns)
        if len(functions) > 1:
            files = set(a.file for a in assigns)
            f_list = ', '.join(os.path.basename(f) for f in files)
            func_list = ', '.join(functions)

            # 检查是否有功能不在该引脚的能力范围内
            invalid_funcs = []
            for func in functions:
                if func != 'GPIO' and not contract.is_valid_function(pin_name, func):
                    invalid_funcs.append(func)

            if invalid_funcs:
                for func in invalid_funcs:
                    result.violations.append(ContractViolation(
                        severity='error',
                        category='pin_conflict',
                        message=f"引脚 {pin_name} 不支持功能 {func}",
                        file=assigns[0].file,
                        line=assigns[0].line,
                        detail=f"可用功能: {list(contract.get_pin_functions(pin_name).keys())}"
                    ))
            elif len(files) > 1:
                result.violations.append(ContractViolation(
                    severity='warning',
                    category='pin_conflict',
                    message=f"引脚 {pin_name} 在不同文件中被配置为不同功能: {func_list}",
                    file=os.path.basename(list(files)[0]),
                    line=assigns[0].line,
                    detail=f"涉及文件: {f_list}"
                ))

        # 检查 AF 号是否正确
        for a in assigns:
            if a.af is not None and a.function != 'GPIO':
                expected_af = contract.get_af_for_function(pin_name, a.function)
                if expected_af is not None and expected_af != a.af:
                    result.violations.append(ContractViolation(
                        severity='error',
                        category='pin_conflict',
                        message=f"引脚 {pin_name} 功能 {a.function} 的 AF 号错误: 代码中={a.af}, 手册中={expected_af}",
                        file=a.file,
                        line=a.line,
                    ))

        # 检查调试口冲突
        note = contract.get_note(pin_name)
        if note and 'SWD' in note and any(a.function != 'GPIO' for a in assigns):
            result.violations.append(ContractViolation(
                severity='error',
                category='pin_conflict',
                message=f"调试口 {pin_name} ({note}) 被复用为其他功能",
                file=assigns[0].file,
                line=assigns[0].line,
                detail="禁用调试口后 SWD 将无法连接"
            ))

    return result
