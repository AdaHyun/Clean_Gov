# 原生 Data-Juicer 配置

这些模板只声明 Data-Juicer 1.5.3 原生算子，不含自定义 operator。运行入口会在 `data/runs/<run_id>/intermediate/configs` 生成带实际输入、输出和工作目录的配置。

- `web_normal.yaml`：普通网页的 NFKC、繁转简、异常空格、中文标点、断行、固定噪声和重复句清理。
- `web_multiline.yaml`：上述清理加 `document_line_deduplicator` 高频模板行处理。
- `web_table.yaml`：含表格网页的字符/单元格文本规范化与 HTML 表格提取。
- `attachment_text.yaml`：普通附件的字符、繁简、空格、标点、断行、内部图片 URL 和重复句清理。
- `attachment_table.yaml`：含表格附件的字符/单元格文本规范化与 HTML 表格提取。
- `quality_text.yaml`：Stage 02 非表格正文质量过滤。
- `exact_dedup.yaml`：Stage 03 全通道完整正文完全去重。
- `llm_topic_quality.yaml`：Stage 04 原生 `llm_quality_score_filter`，同时请求质量、主题、内容类型、排除标签和精确栏目噪声段。

项目不再包含或运行 MinHash/Jaccard 近似去重配置。Stage 03 只使用 `document_deduplicator`。

表格通道也会执行 NFKC、繁转简、异常空格、标点和断行规则，但绕过 Stage 02 文档级质量过滤。五条通道均修复标点/符号之间及受约束的符号与英文字母之间的异常空格，同时保留正常英文标点后的词间空格和 Markdown 行首结构。所有通道均参加 Stage 03，并在启用 LLM 时参加 Stage 04/05；Stage 05 的本地校验会保护 HTML/Markdown 表格结构行。

LLM 非密钥提供商策略位于 `configs/llm_providers.yaml`，本机地址、模型名和密钥位于被忽略的 `.env.local`。中英文标签对照位于 `configs/llm_tag_labels.zh-CN.json`。
