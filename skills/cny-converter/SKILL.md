---
name: cny-converter
description: Convert a US dollar amount into Chinese yuan (RMB) using the latest live exchange rate. Make sure to use this skill whenever the user says "算一下" followed by a number, or otherwise asks to convert dollars/USD into 人民币/RMB/CNY, even if they don't explicitly mention exchange rates. Always fetch a fresh rate from the web before calculating.
---

# 美元转人民币换算器 (USD → CNY)

当用户说「算一下 + 数字」，或要求把美元换算成人民币时，触发本 skill。

## 步骤

1. **解析金额**：从用户输入中提取数字（视为美元金额）。如果单位不明确，默认按美元处理；若用户明确说是人民币转美元，则反向计算。

2. **获取最新汇率**：使用 `web_search` 搜索当前美元兑人民币汇率，例如查询 `USD to CNY exchange rate` 或 `美元 人民币 汇率`。从搜索结果中取最新的 1 美元 = ? 人民币 数值。不要使用记忆中的旧汇率——必须每次实时查询。

3. **计算并输出**：
   - 人民币金额 = 美元金额 × 汇率
   - 用如下格式回复：
     ```
     当前汇率：1 USD = X.XX CNY（来源 / 时间）
     $<金额> ≈ ¥<结果>
     ```
   - 金额保留两位小数，使用千位分隔符。

## 示例

用户：`算一下 250`

回复：
```
当前汇率：1 USD = 7.18 CNY（来源：xe.com，实时）
$250.00 ≈ ¥1,795.00
```

## 注意事项

- 汇率波动频繁，务必每次重新搜索，并在回复中注明汇率与来源。
- 若搜索结果有多个汇率，取来自权威来源（xe.com、Google 财经、央行中间价等）的最新值。
- 若用户给出的是带千位分隔符或货币符号的金额（如 `$1,000`），先清洗成纯数字再计算。
