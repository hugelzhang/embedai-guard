# EmbedAI Guard

> 嵌入式固件开发的 AI Native 质量基础设施

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![CI](https://github.com/hugelzhang/embedai-guard/actions/workflows/test.yml/badge.svg)](https://github.com/hugelzhang/embedai-guard/actions)
[![Chips](https://img.shields.io/badge/chips-3%20supported-cyan)](src/plugins/)

---

## 一句话

让嵌入式固件开发从"人工逐行审查"进化到"AI 自主闭环验证"。

## 做什么

```
传统嵌入式 debug：
  写代码 → 编译 → 烧录 → 看波形 → 改代码 → 烧录 → ...
  60% 时间花在 debug，大量问题是已知规则的违规

EmbedAI Guard：
  写代码 → guard scan → guard check → guard fix → guard trace
  └── AI 自动扫描 ──┘  └── 契约验证 ──┘  └── 波形回归 ──┘
  一次烧录，验证通过
```

## 三层守卫引擎

| 引擎 | 做什么 | 依赖 |
|------|--------|------|
| **Constraint Scanner** | 扫描 delay/malloc/乘除/ISR 复杂度，自动修复违规 | 纯软件 |
| **Contract Validator** | 检测引脚冲突、时钟配置错误、DMA 争用、中断优先级 | 芯片插件 |
| **Golden Trace** | 逻辑分析仪波形自动采集 + 黄金数据对比 + 差异分析 | Saleae |

## 快速开始

```bash
pip install -e .
guard plan .                          # 分析项目，推荐执行计划
guard scan .                          # 13 条规则约束扫描
guard fix . --dry-run                 # 预览可自动修复的违规
guard fix . --apply                   # 执行自动修复
guard check . --contract chip.json    # 芯片契约验证
```

### 效果预览

```
$ guard scan STM32L475_HARDWARE/

EmbedAI Guard — Scan Results
  ERROR: 8  WARNING: 204  INFO: 0

  adc.c (13 issues)
    ERROR  L 126:9   [EMBED-001]  发现阻塞延时调用 delay_ms(
            > delay_ms(5);
    ERROR  L  30:1   [EMBED-004]  ISR USART1_IRQHandler 共 31 行（建议 ≤10 行）
    WARN   L  27:1   [EMBED-007]  全局变量未声明 volatile

  pwm.c (9 issues)        stmflash.c (16 issues)     timer.c (5 issues)
  ...                     ...                        ...

  ✗ 8 errors — FIX REQUIRED
```

## 项目状态

**v0.1.0** — Phase 1+2 完成，三芯片支持。

```
✅ Phase 1: 13 规则约束扫描 + auto-fix + CLI
✅ Phase 2: 双芯片契约引擎
✅ Phase 2.5: Skill Library 架构 + Planner
🔜 Phase 3: Golden Trace 波形回归
```

## 目录结构

```
embedai-guard/
├── README.md              # 本文件
├── docs/
│   └── PROJECT_PLAN.md    # 项目计划书
├── src/                   # 源代码（Phase 1 开始）
│   ├── guard/             # CLI 核心
│   ├── rules/             # 规则库
│   └── plugins/           # 芯片插件
├── tests/                 # 测试
└── examples/              # 示例项目
```

## 技术栈

- **AST 解析**：tree-sitter C
- **规则引擎**：YAML DSL → Python
- **CLI**：Click / Typer
- **VS Code 扩展**：TypeScript + LSP
- **波形分析**：Saleae SDK + numpy
- **芯片插件**：JSON Schema + Python entry_points

## 相关资源

- [嵌入式编码规范](~/.claude/CLAUDE.md)
- [硬件知识库](D:/嵌入式知识库/.claude/CLAUDE.md)
- [MCU 调试参考](~/.claude/references/embedded/mcu-debug-probe.md)
- [逻辑分析仪参考](~/.claude/references/embedded/saleae-logic.md)
- [嵌入式固件开发参考](~/.claude/references/embedded/embedded-systems.md)
