# EmbedAI Guard — 效果数据对比

> 三芯片 · 13 规则 · 实际项目验证

---

## 1. 约束扫描效果

### STM32L475VET6（潘多拉 IoT 开发板，22 个外设驱动）

| 规则 | 级别 | 检出 | 说明 |
|------|------|------|------|
| EMBED-001 阻塞延时 | ERROR | 14 | 9 个文件存在 `delay_ms()` 调用 |
| EMBED-002 动态内存 | ERROR | 0 | 项目无 malloc，合规 |
| EMBED-003 乘除运算 | WARNING | 44 | 主要为 Flash/SPI/QSPI 地址计算 |
| EMBED-004 ISR 精简 | ERROR | 2 | `USART1_IRQHandler` 31 行, `SysTick_Handler` 11 行 |
| EMBED-005 浮点 | WARNING | 4 | AHT10/DAC/ICM20608 传感器浮点计算 |
| EMBED-006 返回值 | WARNING | ~80 | HAL 函数多处未检查返回值 |
| EMBED-007 volatile | WARNING | ~15 | 全局变量缺少 volatile 声明 |
| EMBED-008 空指针 | WARNING | 1 | timer.c TIM_HandleTypeDef 指针 |
| EMBED-009 数组越界 | WARNING | ~35 | w25qxx/stmflash/sd_card 数组下标 |
| EMBED-010 未初始化 | WARNING | 21 | HAL 初始化结构体未 `={0}` |
| EMBED-011 整数溢出 | WARNING | ~22 | uint8_t/uint16_t 循环计数器 |
| EMBED-012 冗余比较 | WARNING | 0 | 项目无此问题 |
| EMBED-015 switch | WARNING | 0 | 全部 switch 已有 default |

| 汇总 | 数值 |
|------|------|
| 扫描文件 | 55 |
| ERROR | 14 |
| WARNING | ~200 |
| 扫描耗时 | < 1 秒 |

### CIU32F003F5U6（番茄工作钟，6 个模块）

| 规则 | 级别 | 检出 | 说明 |
|------|------|------|------|
| EMBED-001 阻塞延时 | ERROR | 0 | 项目使用状态机，无 delay |
| EMBED-003 乘除运算 | WARNING | ~60 | BMS 库仑计/ADC/数码管大量数学运算 |
| EMBED-004 ISR 精简 | ERROR | 1 | `UART1_IRQHandler` **71 行**（严重违规） |
| EMBED-007 volatile | WARNING | ~30 | 全部 bank0 全局变量无 volatile |
| EMBED-008 空指针 | WARNING | ~20 | 多数驱动函数指针参数未做 NULL 检查 |
| EMBED-011 整数溢出 | WARNING | ~4 | uint8_t 循环计数器 |
| EMBED-015 switch | WARNING | 3 | bms_main/seg_display/key_bsp 缺 default |

| 汇总 | 数值 |
|------|------|
| 扫描文件 | 14 (Source) / 66 (全项目)|
| ERROR | 1 |
| WARNING | 98 (Source) / 453 (全项目)|

---

## 2. 自动修复效果

### fix --apply 实测

| 项目 | 可修复 | 成功 | 修复类型 |
|------|--------|------|---------|
| STM32L475 HARDWARE | 54 | 16 | `/2→>>1`, `/4→>>2`, `/8→>>3`, `*4→<<2`, `*4096→<<12` |
| CIU32F003 Source | 40 | 13 | `*2→<<1`, `/2→>>1` |

### fix 前 vs fix 后

```
CIU32F003:
  Before:  WARNING 109  →  After:  WARNING 98  (↓11)

STM32L475:
  Before:  EMBED-003 44 warnings
  After:   16 warnings resolved
```

---

## 3. 芯片契约验证

| 芯片 | 引脚 | 提取 | 冲突 | DMA | 中断 |
|------|------|------|------|-----|------|
| STM32L475VET6 | 82 | 25 pins ✅ | 0 errors | 6 warnings | 7 warnings |
| CIU32F003F5U6 | 18 | 14 pins ✅ | 0 violations | N/A (无DMA) | N/A |
| YS32F003 | 18 | 契约就绪 | — | N/A (无DMA) | — |

---

## 4. 库兼容性

| 芯片 | GPIO 初始化 API | 契约提取 |
|------|----------------|---------|
| STM32L475 | `HAL_GPIO_Init(GPIOA, &GPIO_Initure)` | ✅ |
| CIU32F003 | `std_gpio_init(GPIOA, &gpio_config)` | ✅ |
| YS32F003 | `GPIO_Init(GPIOA, &GPIO_InitStruct)` | ✅ |

---

*数据采集时间: 2026-06-13 · EmbedAI Guard v0.1.0*
