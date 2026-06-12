# EmbedAI Guard — 10 分钟演示脚本

> 目标受众：MCU 原厂 SDK 负责人 / FAE 团队负责人
> 时长：~10 分钟
> 准备：已安装 `pip install embedai-guard`，已有 STM32L475 / CIU32F003 项目代码

---

## 环节 1：13 条规则一键扫描 (2 分钟)

```bash
# 切换到你的 STM32L475 项目
cd D:/MyProject/TM32L475VET6/0_Software

# 全项目扫描
guard scan .
```

**展示重点：**
- 55 个文件，秒级扫描完成
- 14 个 ERROR（阻塞延时 + ISR 过长）+ ~200 个 WARNING
- 每条违规标注了文件名 + 行号 + 列号 + 违规代码片段 + 修复建议

**旁白：**
> "这是 ST 的 STM32L475 项目，22 个外设驱动。13 条规则覆盖了你 CLAUDE.md 里的全部编码规范：禁止 delay、禁止 malloc、禁止乘除、ISR 精简、静态内存——而且不只是检查，每条都有具体的修复建议。"

---

## 环节 2：自动修复 — 一个字不改，违规减少 (2 分钟)

```bash
# 预览可自动修复的内容
guard fix . --dry-run
```

**展示重点：**
- 列出所有可自动修复的违规，红绿 diff 预览

```bash
# 执行修复
guard fix . --apply
```

**展示重点：**
- 10+ 处 `*4096` → `<<12`、`/8` → `>>3` 秒级完成
- 再跑 `guard scan .`，WARNING 数量下降

**旁白：**
> "它不是只告诉你哪里错了——能改的自动帮你改。乘除改移位、未初始化变量加 `= 0`，改完代码语义不变，编译照过。"

---

## 环节 3：换个芯片，同样的命令 (2 分钟)

```bash
# 切换到华大 CIU32F003 项目
cd D:/MyProject/CIU32F003_tomato

# 同样的扫描命令
guard scan Source/
```

**展示重点：**
- 14 个文件，1 ERROR（ISR 71 行），98 WARNING
- 同样的 13 条规则，同样的三格式输出

**旁白：**
> "这个项目跑在华大 CIU32F003 上——Cortex-M0，3KB RAM，TSSOP20 封装。和 STM32L475 完全不同量级。但 **guard 不用改任何配置**，一样的命令，一样的效果。"

---

## 环节 4：芯片契约 — 硬件层面的验证 (3 分钟)

```bash
# STM32L475 契约验证
guard check . --contract src/plugins/stm32l475vet6.json

# CIU32F003 契约验证
cd D:/MyProject/CIU32F003_tomato
guard check Source/ --contract .../ciu32f003f5u6.json
```

**展示重点：**
- 引脚分配表自动提取（HAL_GPIO_Init / std_gpio_init 都支持）
- 四维验证：引脚冲突 / 时钟配置 / DMA 通道 / 中断优先级
- STM32L475: 0 errors（引脚配置正确）
- CIU32F003: 0 violations

**旁白：**
> "芯片契约是 **硬件层面的类型检查**。你的客户用 SDK 配引脚的时候，配错了 AF 号、两个外设抢同一个 DMA 通道、ISR 优先级违反 FreeRTOS 约束——这些在编译阶段就能拦住，不用等烧录后拿示波器 debug。"

---

## 环节 5：第三个芯片，零开发成本 (1 分钟)

```bash
# 验证汇春 YS32F003 契约已就绪
python -c "
import json
c = json.load(open('src/plugins/ys32f003.json'))
print(f'YS32F003: {len(c[\"pins\"])} pins, {len(c[\"interrupts\"][\"vectors\"])} vectors')
"
```

**展示重点：**
- 三个芯片：ST → 华大 → 汇春，覆盖 Cortex-M4F 到 M0+
- 新增芯片支持：一份 JSON 文件（82 行 ~ 18 行），半天工作量

**旁白：**
> "支持一个新芯片只需要写一份 JSON 契约——82 引脚的大芯片半天，18 引脚的小芯片两小时。不是为某个芯片定制的工具，是通用平台。"

---

## 总结话术 (30 秒)

> 你们的 SDK 团队写驱动，你们的 FAE 团队帮客户 debug。
> 这两个环节里 60% 的问题是有确定答案的——编码规范违规、引脚配置错误、中断优先级打架。
> EmbedAI Guard 把这些变成**自动化门禁**：扫描 → 自动修复 → 契约验证，三条命令，秒级完成。
> 客户拿到的 SDK 质量更高，FAE 处理的问题更少。我们已经有三个芯片的验证数据，下一个可以是你们的。

---

## 演示环境准备清单

- [ ] `pip install embedai-guard`（或 `pip install -e .` 开发安装）
- [ ] STM32L475 项目代码就位
- [ ] CIU32F003 项目代码就位
- [ ] 三个芯片契约 JSON 文件就位
- [ ] 终端配色正常（Windows Terminal 推荐）
- [ ] 可选：录屏工具准备（OBS / ScreenToGif）
