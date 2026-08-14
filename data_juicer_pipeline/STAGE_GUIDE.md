# Stage 00–05 处理与判定标准

本文说明推荐入口 `scripts/run_native_pipeline.py` 的每个阶段做什么、按什么标准判定、哪些内容会被隔离，以及去哪里查看结果。

每次执行使用独立的 `data/runs/<run_id>` 根目录，下面固定分为 `intermediate`、`output`、`reports`、`quarantine` 和 `logs`。Stage 04 重试也使用新的独立 `<retry_id>`。改造前已经存在的分散式历史目录不会迁移或删除，重试与计时脚本仍可读取。

## Stage 00：输入扫描与分流

### 做什么

- 自动选择最新的规范网页 JSONL，或读取 `--web-input`；
- 递归扫描附件目录中的 `content.md`，同时读取解析元数据；
- 统一 `doc_id`、`text`、`title`、来源和附件解析字段；
- 按网页/附件、普通/多行/表格分成五条通道；
- 标记空正文、解析失败和超长正文。

### 判定标准

- 附件正文只取 `content.md`，不把 `raw.md` 再送入清洗；
- 解析状态失败、正文为空或缺失的附件进入待重解析隔离；
- 正文长度超过 `--max-native-chars`，默认 3,000,000 字符，进入超长隔离；
- HTML/Markdown/解析元数据表明含表格时进入表格通道；这表示“文档含表格”，不要求全文只有表格。

### 输出

```text
data/runs/<run_id>/intermediate/00_prepared/*.jsonl
data/runs/<run_id>/quarantine/reparse_required.jsonl
data/runs/<run_id>/quarantine/oversized.jsonl
```

Stage 01 某通道的输入条数少于扫描总数，是因为 Stage 00 已经隔离无法进入清洗的记录，并且总记录被分流到五个通道；单个 `web_normal` 数量不是网页总数。

## Stage 01：字符、文本与表格内容规范化

### 做什么

- NFKC Unicode 规范化和圈号等兼容字符转换；
- 繁体中文转简体；
- 修复中文/数字间异常空格、受约束的年份/月日/小数/百分比拆分；
- 中文语境标点转中文全角格式，删除标点与标点之间及受约束的标点/符号与英文字母之间的异常空格；
- 合并标点后由解析异常产生的换行，压缩过多空行；
- 删除少量明确的打印、分享、栏目导航等固定独立行；
- 普通正文去连续重复句，多行网页可去跨文档高频模板行；
- 表格文档同样执行字符、繁简、空格、标点和断行修复，并提取 HTML 表格元数据。

### 判定标准

- 保留英文词间空格，如 `Public Health`；
- 修复 `COVID - 19`、`A / B`、`（ OpenAI ）` 和 `API： OpenAI`，但保留 `Note: text` 以及 Markdown 行首的 `# Title`、`- item`、`* emphasis`；
- 不无条件拼接所有相邻数字，避免把编号误写；
- 高频行去重只用于 `web_multiline`，默认文档频次阈值 100；
- 表格行列结构应保留，表格内容会清洗，但不使用 Stage 02 文档级过滤器。

### 输出

```text
data/runs/<run_id>/intermediate/01_normalized/<lane>.jsonl
data/runs/<run_id>/reports/01_normalized_<lane>.json
```

这是查看繁简、异常空格和标点修复效果的第一处。

## Stage 02：非表格质量过滤

### 做什么

按顺序运行 Data-Juicer 原生过滤器：

1. `text_length_filter`：50–3,000,000 字符；
2. `alphanumeric_filter`：中文/字母/数字比例至少 0.45；
3. `special_characters_filter`：特殊字符比例不高于 0.75；
4. `character_repetition_filter`：10 字符 n-gram 重复比例不高于 0.50；
5. `language_id_score_filter`：默认中文 `zh`，FastText 置信度至少 0.50。

### 判定标准

五项均通过才保留。表格通道整体绕过 Stage 02，防止符号密集或重复的合法表格被误删。

### 输出

```text
data/runs/<run_id>/intermediate/02_quality_filtered/<lane>.jsonl
data/runs/<run_id>/quarantine/02_quality_<lane>_removed.jsonl
data/runs/<run_id>/quarantine/02_quality_filter_reasons/<lane>/
data/runs/<run_id>/reports/02_quality_<lane>.json
```

隔离记录的 `quarantine_reason` 是 `failed_native_quality_filters`；具体命中过滤器优先查看 tracer 目录和 `__dj__stats__`。

## Stage 03：全通道正文完全去重

### 做什么

先合并五条通道，再使用 `document_deduplicator` 比较清洗后的完整 `text`。

### 判定标准

只有完整正文逐字符相同才视为重复。大小写、标点、数字和正文差异均参与比较。当前明确关闭 MinHash/Jaccard、SimHash 和语义近似去重。

因此固定描述模板相同、但数字或其他正文内容不同的监测/统计记录不会在本阶段被删除。

### 输出

```text
data/runs/<run_id>/intermediate/03_global_exact_dedup/all_before_exact_dedup.jsonl
data/runs/<run_id>/intermediate/03_global_exact_dedup/all_after_exact_dedup.jsonl
data/runs/<run_id>/reports/03_global_exact_dedup.json
data/runs/<run_id>/quarantine/03_global_exact_duplicates.jsonl
data/runs/<run_id>/quarantine/03_global_exact_duplicate_pairs/
```

被删除记录的原因是 `exact_duplicate_after_cleaning`。

## Stage 04：LLM 主题、质量和噪声标注

Stage 04 默认关闭，传入 `--enable-llm-quality` 才运行。包括表格在内的全部 Stage 03 保留记录都会参加。

### 做什么

- 五维质量评分：解析准确性、语法、信息量、连贯性、严谨性；
- 识别公共卫生主题、内容类型、公共卫生相关度和 CPT/SFT 用途；
- 标出招聘、活动、宣传、导航等排除类型；
- 定位可能可删的栏目噪声，返回噪声类型、精确原文行、行号和置信度；
- 将所有英文标签翻译为中文字段，再按确定策略分流。

### 直接保留候选

同时满足：

- 质量综合分至少 0.60；
- `topic_decision=keep`；
- 模型明确判断存在实质公共卫生内容；
- 主题置信度至少 0.90；
- 公共卫生相关度至少 4/5；
- 不含任何硬排除标签。

### 高置信无关主题隔离

同时满足：

- `topic_decision=exclude`；
- 主题置信度至少 0.90；
- 不存在实质公共卫生内容；
- 命中招聘、人事、活动、会议、领导活动、党建、表彰、单位宣传、采购、机构介绍、纯导航、一般新闻、商业广告或无关主题中的至少一项。

活动/宣传文章如含具体、可独立复用的公共卫生知识，不应仅凭形式排除；不确定时进入人工复核。

### 其他分流

- 质量分低于 0.60：低质量隔离；
- 主题低置信、相关度不足、混合内容或标签冲突：人工复核隔离；
- API 超时、空响应、0 分占位或 JSON 解析失败：重试隔离；
- Data-Juicer 标注阶段不应删除任何记录，若发生意外删除，流水线停止生成正式结果。

### 输出

```text
data/runs/<run_id>/intermediate/04_llm_topic_quality/all_scored_tagged.jsonl
data/runs/<run_id>/intermediate/04_llm_topic_quality/all_annotated_zh.jsonl
data/runs/<run_id>/intermediate/04_llm_topic_quality/candidate_keep.jsonl
data/runs/<run_id>/reports/04_llm_scoring_validation.json
data/runs/<run_id>/reports/04_llm_topic_quality.json
data/runs/<run_id>/quarantine/04_topic_excluded.jsonl
data/runs/<run_id>/quarantine/04_low_quality.jsonl
data/runs/<run_id>/quarantine/04_llm_retry_required.jsonl
data/runs/<run_id>/quarantine/04_manual_review_required.jsonl
```

人工查看优先使用 `all_annotated_zh.jsonl`。每条记录包含 `llm_content_type_zh`、`llm_topic_tags_zh`、`llm_exclusion_tags_zh`、`llm_policy_status_zh` 等中文字段。完整中英文代码表在 `configs/llm_tag_labels.zh-CN.json`。

进度条中“已处理 2647/8407”表示 Data-Juicer 已经为这些记录完成算子调用并收到可落盘结果，不等同于这些记录最终全部通过；最终分流数量以 `04_llm_topic_quality.json` 为准。服务是否正常还应查看 `04_llm_retry_required.jsonl` 数量及 Stage 04 stderr 日志。

### Stage 04失败队列重试

`scripts/retry_stage04.py` 只处理指定成功运行的 `04_llm_retry_required.jsonl`。它会先删除旧的 `llm_quality_score`、`llm_quality_record` 和失败状态，避免Data-Juicer把零分记录误认为已经计算。默认执行最多两轮，每轮仍使用公司模型和原主题质量阈值。

默认20,000字符以内提交完整正文；更长正文提交首部、中部、尾部各最多6,000字符的确定性审阅视图。该视图不替换原始 `text`。长文抽样的噪声定位被强制延后，不会用不完整抽样自动删除完整附件中的行。

```bat
python data_juicer_pipeline\scripts\retry_stage04.py --run-id <run_id> --dry-run
python data_juicer_pipeline\scripts\retry_stage04.py --run-id <run_id> --llm-concurrency 16 --max-rounds 2
```

重试输出使用新的 `<retry_id>`，不会覆盖原运行：

```text
data/runs/<retry_id>/intermediate/retry_candidate_keep.jsonl
data/runs/<retry_id>/intermediate/retry_kept.jsonl
data/runs/<retry_id>/quarantine/retry_topic_excluded.jsonl
data/runs/<retry_id>/quarantine/retry_low_quality.jsonl
data/runs/<retry_id>/quarantine/retry_manual_review_required.jsonl
data/runs/<retry_id>/quarantine/retry_still_failed.jsonl
data/runs/<retry_id>/quarantine/retry_noise_review_required.jsonl
data/runs/<retry_id>/reports/retry_summary.json
data/runs/<retry_id>/output/corpus_native_cleaned_<source_run_id>_retry_<retry_id>.jsonl
```

只有重试成功、主题质量合格并通过Stage 05的记录才会追加到修订版正式结果；仍失败、低质量、无关主题或待复核记录不会自动加回。

## Stage 05：本地安全删除栏目噪声

### 做什么

读取 Stage 04 的候选保留记录及 `llm_noise_segments`。模型只负责指出位置，本地程序负责校验和修改，不允许模型重写全文。

### 自动删除必须同时满足

- 噪声类型在允许列表；
- 置信度至少 0.90；
- `exact_lines` 是连续完整行，能在原文中按指定行号或唯一匹配定位；
- 目标不是 HTML/Markdown 表格结构行；
- 总删除字符不超过原文的 30%；
- 删除后正文长度仍达到最低要求；
- 所有报告的噪声段都通过校验。

任一条件失败，不做部分删除，保留原文并把整条记录放入人工复核隔离。

### 输出

```text
data/runs/<run_id>/intermediate/05_local_noise_cleanup/all_kept.jsonl
data/runs/<run_id>/intermediate/05_local_noise_cleanup/noise_cleaned.jsonl
data/runs/<run_id>/reports/05_local_noise_cleanup.json
data/runs/<run_id>/quarantine/05_noise_removal_review_required.jsonl
```

`noise_cleaned.jsonl` 是确实发生修改的审计子集，`all_kept.jsonl` 包含修改后和无需修改的全部正式候选。

## 最终输出与计时

正式结果：

```text
data/runs/<run_id>/output/corpus_native_cleaned_<run_id>.jsonl
```

成功摘要：

```text
data/runs/<run_id>/reports/native_pipeline_summary.json
data/runs/<run_id>/logs/run_summary.json
data/runs/<run_id>/logs/timing_summary.json
```

`timing_summary.json` 记录总耗时、各 Stage/通道耗时、输入输出条数、条/秒和字符/秒。可用 `scripts/show_timing.py` 查看。

## 快速抽查顺序

1. 看 `native_pipeline_summary.json` 的每阶段输入、输出和隔离计数；
2. 对比 Stage 01 前后，检查异常空格、繁简、标点、换行和表格结构；
3. 查看 Stage 02 tracer，确认没有误删严谨正文；
4. 抽查 Stage 03 重复对，确认是完整正文相同；
5. 查看 `all_annotated_zh.jsonl` 及 Stage 04 四类隔离文件；
6. 对比 `noise_cleaned.jsonl` 中修改前可由 `llm_noise_segments` 还原的精确行，检查栏目噪声删除；
7. 最后把 `corpus_native_cleaned_<run_id>.jsonl` 作为 CPT/SFT 前置语料，而不是任意中间文件。
