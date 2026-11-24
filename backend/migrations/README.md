# 数据表初始化

本版通过 `src.models.base.Base.metadata.create_all` 创建首次运行所需的六张表。模型在 `src/models/workflow.py`。`python -m scripts.seed_demo` 按观测内容键补全演示样本，保留现有分析及签核。数据库端口只发布到本机。

## 签认版本化（本版变更）

- 测量单仍以 `observations` 为只追加台账，但同一工单的每次签认各占一行，载荷新增可选字段 `version`（签认版本，整数）与 `received_at`（签认收到时间，ISO 时间）。工单级新旧一律按版本号判定，与录入先后无关。
- `analysis_runs` 新增 `anchor_at`（发起分析时刻）。训练只用留出期开始前已收到的签认，留出工单按该锚点前的最新签认计分。
- 全新环境直接 `create_all` 即包含上述列。升级既有库需手动补列：

```sql
ALTER TABLE analysis_runs ADD COLUMN anchor_at TIMESTAMP;
```

并为旧观测补齐 `version` / `received_at`；缺少版本的历史记录在补录同工单时会被入库校验拦截，需先整理历史数据。
