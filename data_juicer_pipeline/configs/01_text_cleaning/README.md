# 配置说明

`site_rules.yaml` 只包含有明确样例依据的低风险、完整匹配规则。站点专属规则必须先在真实输入报告中找到证据，再按 `source` 或 `domain` 限定；高风险规则必须保持 `enabled: false`。

Data-Juicer 真实运行配置不会覆盖模板，而是写入 `data/runs/<run_id>/intermediate/configs/`。1.5.3 的 `frequency_threshold` 是绝对文档频次，只有频次严格大于阈值的行才会删除。
