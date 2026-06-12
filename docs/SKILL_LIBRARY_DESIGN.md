# EmbedAI Guard — Skill Library 架构方案

> 目标：将 scanner / fixer / contract 的底层能力拆成独立、可组合的 skill，为 router / planner / golden-trace 打地基。

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户 / CLI / CI                       │
├─────────────────────────────────────────────────────────┤
│   engines/                    planner / router (未来)     │
│   ├── constraint_engine   ← scan                        │
│   ├── fix_engine          ← fix                         │
│   ├── contract_engine     ← check                       │
│   └── trace_engine        ← 未来                        │
├─────────────────────────────────────────────────────────┤
│   skills/                    可复用底层能力               │
│   ├── parse     tree-sitter 解析 → AST                   │
│   ├── walk      AST 遍历 + 节点查询                      │
│   ├── edit      源码行级修改 + diff                      │
│   ├── chip      芯片契约查询 (pin/clock/dma/irq)         │
│   ├── report    违规存储 + 序列化 (terminal/json/junit)  │
│   └── fs        目录遍历 + 排除 + 源码缓存               │
├─────────────────────────────────────────────────────────┤
│   types/                    跨 skill 共享数据类型          │
│   ├── Violation, Fix, PinAssignment, ContractViolation  │
│   ├── ScanResult, FixResult, ContractResult             │
│   └── SourceTree, ChipContract                          │
└─────────────────────────────────────────────────────────┘
```

**核心原则**：engine 只知道"做什么"，skill 负责"怎么做"。engine 之间不互相调用，只通过 skill 组合。

---

## 2. 数据类型层（types/）

### 统一的事件流

所有 engine 的输入输出都走同一套类型：

```python
# types/result.py

@dataclass
class QualitySignal:
    """每个 skill/engine 执行完返回的质量信号"""
    name: str            # skill 名称
    success: bool        # 是否成功
    items_processed: int # 处理量
    issues_found: int    # 发现问题数
    issues_fixed: int    # 修复问题数
    score: float         # 0.0 - 1.0 质量评分
    detail: dict         # 额外数据

@dataclass  
class WorkflowResult:
    """一次工作流执行的完整结果"""
    steps: List[QualitySignal]
    violations: List[Violation]
    fixes: List[Fix]
    contract_violations: List[ContractViolation]
    
    @property
    def passed(self) -> bool:
        return all(s.success for s in self.steps)
    
    @property 
    def overall_score(self) -> float:
        if not self.steps:
            return 1.0
        return sum(s.score for s in self.steps) / len(self.steps)
```

---

## 3. Skill 层（skills/）

### Skill 1: parse — tree-sitter 解析

```
输入: 源码字符串 或 文件路径
输出: SourceTree (AST + 源码行数组 + 文件路径)
```

```python
# skills/parse.py

@dataclass
class SourceTree:
    """单文件解析结果"""
    file_path: str
    source: str
    lines: List[str]
    root_node: Node          # tree-sitter AST 根节点
    language: str            # 'c' | 'c_header'
    has_errors: bool
    
    @property  
    def bytes(self) -> int:
        return len(self.source)

class Parser:
    """多文件解析器，内置缓存"""
    def __init__(self):
        self._lang = Language(tsc.language())
        self._parser = Parser(self._lang)
        self._cache: Dict[str, SourceTree] = {}
    
    def parse_file(self, path: str) -> SourceTree:
        if path in self._cache:
            return self._cache[path]
        # ... 解析 + 缓存
```

### Skill 2: walk — AST 遍历

```
输入: SourceTree + 查询条件
输出: 匹配的 AST 节点列表
```

```python
# skills/walk.py

class AstQuery:
    """AST 查询条件"""
    node_types: List[str] = []       # 按类型过滤
    contains_text: str = None        # 按文本过滤
    max_depth: int = None            # 最大深度
    parent_type: str = None          # 父节点类型

def find_nodes(tree: SourceTree, query: AstQuery) -> List[Node]:
    """在 AST 中查找匹配节点"""

def find_enclosing(node: Node, target_type: str) -> Node:
    """向上查找包含节点（如找 function_definition）"""

def iter_children(node: Node, types: List[str] = None):
    """遍历子节点，可选类型过滤"""

def get_node_text(node: Node) -> str:
    """提取节点文本（处理编码）"""
```

### Skill 3: edit — 源码修改

```
输入: SourceTree + 修改列表 [(line, col, old, new)]
输出: ModifiedSource + DiffResult
```

```python
# skills/edit.py

@dataclass
class LineEdit:
    """单行修改"""
    line: int              # 1-based
    old_text: str
    new_text: str
    reason: str            # 为什么改（规则 ID）
    
@dataclass
class EditResult:
    """修改结果"""
    file_path: str
    edits: List[LineEdit]
    applied: int
    failed: List[str]
    diff: str             # unified diff

class Editor:
    def preview(self, tree: SourceTree, edits: List[LineEdit]) -> EditResult:
        """预览修改（dry-run）"""
    
    def apply(self, tree: SourceTree, edits: List[LineEdit], 
              write_to_disk: bool = False) -> EditResult:
        """应用修改"""
```

### Skill 4: chip — 芯片契约

```
输入: 芯片名称 或 JSON 路径 + 查询
输出: 引脚信息 / DMA 映射 / 时钟约束 / 中断表
```

```python
# skills/chip.py

class ChipModel:
    """统一的芯片模型查询接口"""
    def __init__(self, contract_path: str):
        self.data = json.load(open(contract_path))
    
    def get_pin_functions(self, pin: str) -> Dict[str, int]:
        """查询引脚可用功能"""
    
    def get_af(self, pin: str, function: str) -> int:
        """查询功能对应的 AF 号"""
    
    def get_dma_channel(self, peripheral: str) -> Tuple[str, str]:
        """查询外设的 DMA 通道"""
    
    def get_clock_limits(self) -> dict:
        """查询时钟约束"""
    
    def get_interrupt_limits(self) -> dict:
        """查询中断约束"""
```

### Skill 5: report — 违规存储

```
输入: Violation 列表 + 输出格式
输出: terminal / JSON / JUnit 字符串
```

```python
# skills/report.py

class ReportStore:
    """违规数据存储，支持增量更新和多格式输出"""
    def __init__(self):
        self.violations: List[Violation] = []
        self.fixes: List[Fix] = []
    
    def add_scan(self, violations: List[Violation]):
        """追加扫描结果（自动去重）"""
    
    def add_fixes(self, fixes: List[Fix]):
        """追加修复记录"""
    
    def delta(self) -> dict:
        """与上一次的差异"""
    
    def format(self, fmt: str = 'terminal') -> str:
        """格式化输出"""
```

### Skill 6: fs — 文件系统

```
输入: 目录路径 + 排除规则
输出: 文件列表 + 源码缓存
```

```python
# skills/fs.py

class ProjectFS:
    """项目文件系统，统一目录遍历 + 缓存"""
    def __init__(self, root: str, exclude: List[str] = None):
        self.root = root
        self.exclude = exclude or DEFAULT_EXCLUDE
    
    def find_source_files(self, extensions=('.c', '.h')) -> List[str]:
        """查找所有源文件"""
    
    def read(self, path: str) -> str:
        """读取文件（内置缓存）"""
    
    def iter_files(self):
        """迭代所有源文件 (path, source)"""
```

---

## 4. Engine 层（engines/）

### Engine = 组合 skill，不写底层逻辑

以 constraint_engine 为例，对比重构前后：

```python
# === 重构前 (scanner.py) ===
class Scanner:
    def __init__(self, rules):
        self.rules = rules
        self._lang = Language(tsc.language())     # 写死
        self._parser = Parser(self._lang)          # 写死
    
    def scan_file(self, file_path):
        source = open(file_path).read()            # 自己读文件
        tree = self._parser.parse(source.encode()) # 自己解析
        lines = source.split('\n')                 # 自己分行
        # ... 100 行匹配逻辑

# === 重构后 (engines/constraint_engine.py) ===
class ConstraintEngine:
    def __init__(self, rules: List[Rule], parser: Parser):
        self.rules = rules
        self.parser = parser                  # 注入，可替换
    
    def analyze(self, tree: SourceTree, 
                report: ReportStore) -> QualitySignal:
        """分析一个已解析的源文件"""
        violations = []
        for rule in self.rules:
            nodes = walk.find_nodes(tree, rule.query)
            violations += self._match_rule(rule, nodes, tree)
        
        report.add_scan(violations)
        return QualitySignal(
            name='constraint',
            success=len([v for v in violations if v.severity==Severity.ERROR]) == 0,
            items_processed=1,
            issues_found=len(violations),
            score=1.0 - min(1.0, len(violations) * 0.02),
        )
```

**关键变化**：engine 不再 import tree_sitter、不再 open 文件、不再管理 cache——这些全是 skill 的事。

---

## 5. 组合示例

### 现在的 `guard scan` 变成什么

```python
# cli.py 中的 scan_command
def scan_command(path, rules_dir, fmt):
    # 1. 组装 skill
    parser = Parser()
    rules = load_rules(rules_dir)
    report = ReportStore()
    fs = ProjectFS(path)
    engine = ConstraintEngine(rules, parser)
    
    # 2. 遍历文件
    for filepath, source in fs.iter_files():
        tree = parser.parse_file(filepath)
        signal = engine.analyze(tree, report)
    
    # 3. 输出
    print(report.format(fmt))
    return 0 if report.error_count == 0 else 1
```

### `guard fix` 变成什么

```python
def fix_command(path, rules_dir, dry_run):
    parser = Parser()
    rules = load_rules(rules_dir)
    report = ReportStore()
    fs = ProjectFS(path)
    editor = Editor()
    
    # 先 scan
    engine = ConstraintEngine(rules, parser)
    for filepath, source in fs.iter_files():
        tree = parser.parse_file(filepath)
        engine.analyze(tree, report)
    
    # 再 fix
    fix_engine = FixEngine(rules, editor)
    for filepath in report.files_with_violations():
        tree = parser.parse_file(filepath)  # 复用缓存！
        fix_engine.fix(tree, report, dry_run)
```

**注意 parser.parse_file 第二次调用走缓存，不重新解析。**

### 未来的 `guard plan`

```python
def plan_command(path):
    fs = ProjectFS(path)
    
    # 分析项目
    files = fs.find_source_files()
    chip = detect_chip_from_code(fs)       # 从代码检测芯片型号
    lib = detect_library_from_code(fs)     # HAL / std_gpio / GPIO_Init
    
    plan = {
        'rules': recommend_rules(files),   # 根据代码模式推荐规则
        'contract': chip,                  # 检测到的芯片
        'fixable': estimate_fixable(files),# 预估可修复数
        'order': ['scan', 'fix', 'check'], # 执行顺序
    }
    return plan
```

---

## 6. 迁移路径（分三步，不重写）

### Step 1：抽 skill（本次，2-3 天）

```
新增：
  skills/parse.py     → 从 scanner.py + contract.py 提取
  skills/fs.py        → 从 cli.py 提取（三处目录遍历合并）
  skills/report.py    → 从 cli.py 提取（三套格式化合并）

修改：
  scanner.py   → import skills.parse, 删除内部 parse 逻辑
  contract.py  → import skills.parse, 删除内部 parse 逻辑
  cli.py       → import skills.fs + skills.report, 删除重复代码

不改：
  fixer.py     → 暂时保持原样（输入是 violation + source，接口已清晰）
  models.py    → 保留（Violation/Fix/PinAssignment 数据结构）
  13 条 YAML   → 不变
  pytest       → 保持不变（接口兼容，43 测试应全部通过）
```

### Step 2：拆 engine（下个功能点触发，不是现在就做）

当加下一个大功能时（Golden Trace / 第四芯片 / router），才拆 engine：

```
新增：
  engines/constraint_engine.py  → 从 scanner.py 拆出
  engines/fix_engine.py         → 从 fixer.py 拆出
  engines/contract_engine.py    → 从 contract.py 拆出
```

### Step 3：加 planner（时机成熟时）

```
  engines/planner.py → 组合 project analysis + skill selection
  guard plan → 自动分析项目推荐执行计划
```

---

## 7. 重构前后对比

| 维度 | 重构前 | 重构后 |
|------|--------|--------|
| tree-sitter 解析 | scanner + contract 各写一遍 | `skills/parse.py` 一处 |
| 目录遍历 | cli.py 三处近乎相同的代码 | `skills/fs.py` 一处 |
| 格式化输出 | cli.py 三个函数 | `skills/report.py` 统一 |
| 加新 engine | 从头写所有底层 | 组合已有 skill |
| 加新芯片支持 | 改 contract.py | 加 JSON + chip skill 自动识别 |
| router/planner | 不可能（引擎不互通） | 可行（统一接口 + QualitySignal） |

---

## 8. 不做什么

- **不引入消息队列或微服务** — 全部进程内调用，Python 函数组合
- **不抽象到 DSL 层** — rule YAML 已足够，不需要更深的元编程
- **不重写 13 条规则** — 规则保持不变，只改调度方式
- **不影响 CLI 命令** — `guard scan|fix|check` 用法不变

---

## 9. 开始点

```
Step 1 的具体顺序：

  1. skills/parse.py    — 提取 tree-sitter 解析 + SourceTree + 缓存
  2. skills/fs.py       — 提取目录遍历 + 排除规则
  3. skills/report.py   — 提取 Violation 格式化 (terminal/json/junit)
  4. scanner.py         — import skills, 删自己的 parse 逻辑
  5. contract.py        — import skills, 删自己的 parse 逻辑
  6. cli.py             — import skills, 删重复的遍历/格式化
  7. pytest             — 全跑一遍, 确保 43 测试通过
```
