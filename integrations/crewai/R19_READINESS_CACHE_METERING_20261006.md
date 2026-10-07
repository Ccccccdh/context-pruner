# r19 readiness — cache metering (zero API)

- 缓存计量字段：`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `cache_tokens_source` / `utc_started` / `utc_finished` / `off_peak_window`，逐尝试写入 `full_attempt_capture`。
- 缺失拆分时标记 `absent`，**不**按全价未命中记账。
- 价格（USD / 1M token）：命中 0.003、未命中 0.15、输出（占位假设）0.45；非高峰 ×0.5。
- mock 实测（**模拟 token，非供应商用量**）：21 次尝试 / 21017 输入 / 453 输出。
- 真实批预估（21 次调用 × 1900 输入 token）：all_miss_peak $0.0071、typical_miss_heavy_off_peak $0.0028、all_hit_off_peak $0.0006
- 区间：**$0.0006 – $0.0071**。
