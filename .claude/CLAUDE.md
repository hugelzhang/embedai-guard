# EmbedAI Guard — 项目开发指南

## 项目定位

嵌入式固件代码质量守卫工具。CLI + VS Code 扩展 + 硬件桥接器。

## 目录结构

```
embedai-guard/
├── src/
│   ├── guard/
│   │   ├── scanner.py     # 扫描引擎（AST + 文本 + 函数级分析）
│   │   ├── fixer.py        # 自动修复引擎
│   │   ├── cli.py          # CLI 入口
│   │   ├── loader.py       # YAML 规则加载
│   │   └── models.py       # 数据模型
│   └── rules/              # 规则定义 (YAML)
├── tests/
│   └── test_core_rules.py  # 核心规则单元测试
└── docs/
    ├── PROJECT_PLAN.md     # 项目计划书
    └── EmbedAI_Guard_Pitch.pptx  # 路演 PPT
```

## 开发工作流

```bash
# 安装（开发模式）
pip install -e .

# 日常
guard scan .                          # 扫描
guard fix . --dry-run                 # 预览修复
guard rules                           # 查看规则

# 测试
pytest tests/ -v

# 加新规则
1. 创建 src/rules/EMBED-0XX.yml
2. 在 scanner.py 中注册（文本/AST/函数级）
3. 在 test_core_rules.py 中添加测试
4. guard scan . 验证
```

## 规则分类

| 分析方式 | 规则 | 复杂度 |
|---------|------|--------|
| 文本匹配 | EMBED-001/002/005/006 | 低 |
| AST 节点 | EMBED-003/004/007 | 中 |
| 函数级 | EMBED-008/009/010/011/012 | 高 |

## 编码规范

- Python 3.10+, 类型注解可选
- 规则用 YAML DSL 定义，不硬编码
- 每条规则至少 2 个 pytest 用例
- 加规则前先判断：可自动验证？收益 > 噪声？对齐实际规范？
