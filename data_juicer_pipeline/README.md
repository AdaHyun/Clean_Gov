# Data-Juicer 政府公共卫生语料清洗流水线

推荐入口是 `scripts/run_native_pipeline.py`。项目以以下目录为统一路径根目录：

```text
E:\A-Project\PublicHealth_Gov\Clean_Gov
```

因此命令中的相对路径均相对 `Clean_Gov` 解析，不依赖终端当前目录。输入只读，清洗结果、隔离数据和日志只写入 `data_juicer_pipeline/data`。

完整的运行、逐阶段规则、全部产物字典、隔离原因、验收顺序和字段适配说明见 [data/PIPELINE_MANUAL.md](data/PIPELINE_MANUAL.md)。简版阶段说明见 [STAGE_GUIDE.md](STAGE_GUIDE.md)。LLM 英文标签与中文名称的完整对照见 [configs/llm_tag_labels.zh-CN.json](configs/llm_tag_labels.zh-CN.json)。

## 输入

网页输入默认扫描：

```text
Clean_Gov\text_clean\data\output\gov-webStructure-clean
```

只自动选择名称符合 `gov_corpus_clean_YYYYMMDD_HHMMSS.jsonl` 且时间戳最新的文件，也可以通过 `--web-input` 显式指定。

附件默认读取：

```text
Clean_Gov\attachment_clean\attachment_clean_Company\data\documents
```

每份附件只读取 `content.md`、`metadata.json` 和 `quality.json`，不会重复读取 `raw.md`。可通过 `--attachment-root` 指向测试目录或其他已解析目录。

## 当前 00–05 流程

| Stage | 作用 | 主要实现 |
| --- | --- | --- |
| `00` | 扫描网页和附件、统一字段、稳定 ID、识别解析失败/空正文/超长正文、分成五条通道 | 本地薄适配，不改正文 |
| `01` | NFKC、繁转简、异常空格、中文标点、异常断行、固定控件和重复句清理；表格文档同样清理字符与单元格文本 | Data-Juicer 原生 mapper |
| `02` | 非表格正文的长度、有效字符、特殊字符、重复度和中文识别过滤；表格绕过 | Data-Juicer 原生 filter |
| `03` | 合并全部通道，仅按清洗后的完整 `text` 完全一致去重 | `document_deduplicator` |
| `04` | 可选：LLM 质量评分、公共卫生主题/内容类型/排除原因/栏目噪声标注，高置信无关内容直接隔离 | `llm_quality_score_filter` 加本地审计分流 |
| `05` | 可选：根据 LLM 给出的精确连续行，由本地程序安全删除栏目噪声 | 本地精确匹配与保护校验 |

当前不再运行 MinHash/Jaccard、SimHash、Embedding 或其他近似/语义去重。数字不同但模板相似的政策、监测和统计记录不会因相似度被删除；Stage 03 只删除正文逐字符完全相同的记录。

### 五条通道

| 通道 | Stage 01 | Stage 02 | Stage 04/05 启用时 |
| --- | --- | --- | --- |
| `web_normal` | 通用网页规范化 | 参加 | 参加 |
| `web_multiline` | 通用规范化及高频模板行清理 | 参加 | 参加 |
| `web_table` | 字符、繁简、空格、标点、断行和表格提取 | 绕过 | 参加，表格结构受保护 |
| `attachment_text` | 通用附件规范化 | 参加 | 参加 |
| `attachment_table` | 字符、繁简、空格、标点、断行和表格提取 | 绕过 | 参加，表格结构受保护 |

表格不是“完全不洗”。表格文档也会繁转简、修复 `2 026年`、`4 .3`、标点空格和异常换行，并提取 HTML 表格元数据；只是绕过容易误删表格的 Stage 02 文档级质量过滤。Stage 05 不允许删除 HTML/Markdown 表格结构行。

### 文本规范化规则

- 删除中文与中文、中文与数字、数字与中文之间的异常水平空格，保留英文词间正常空格；
- 受约束地修复 `2 026年`、`4 .3万人次`、`9 8.7 %` 等拆分，不无条件拼接所有数字；
- 删除中文/数字与标点之间、标点与标点之间的异常空格；
- 中文语境中的半角逗号、分号、冒号、问号、感叹号、句号和括号改为中文标点；
- 合并 `，、；：` 后由解析异常产生的换行，压缩连续过多空行；
- NFKC 归一圈号等兼容字符，随后繁体转简体；
- 保留 `Public Health`、`GB/T 19001` 等英文词间空格和常用编号格式。

## LLM 主题与质量策略

Stage 04/05 默认关闭，只有传入 `--enable-llm-quality` 才会调用模型。默认公司内网模型、16 并发、数据级别 `restricted`。

LLM 对所有通道（包括含表格文档）给出：

- 五维质量评分：解析准确性、语法、信息量、连贯性、严谨性；默认综合分至少 `0.60`；
- 公共卫生相关度 `1–5`，默认至少 `4`；
- 主题决策、置信度、实质内容判断、主题标签、内容类型、排除标签、CPT/SFT 用途；
- 可安全删除的栏目噪声类型、原文精确行、原始行号和置信度。

以下类型在没有实质公共卫生知识时属于高置信排除候选：招聘、人事任免、活动/会议报道、领导调研、党建群团、表彰、单位宣传、招标采购、机构介绍、纯导航、一般新闻、商业广告和无关主题。健康科普即使具有“宣传”形式，只要包含实质、可复用的公共卫生知识，仍可保留。

直接隔离无关主题必须同时满足：

1. `topic_decision=exclude`；
2. 主题置信度至少 `0.90`；
3. 模型判断不存在实质公共卫生内容；
4. 至少命中一个硬排除标签。

低置信、混合内容、标签冲突或相关度不足的记录进入人工复核，不会静默删除，也不会进入正式语料。模型调用失败、空响应或 JSON 解析失败进入重试隔离，不再作为正常语料保留。

LLM 不直接改写整篇正文。Stage 05 只删除模型逐字返回、能在原文中唯一定位的连续完整行；置信度默认至少 `0.90`，删除量不超过正文字符的 `30%`，清理后不得过短，HTML/Markdown 表格行禁止删除。任一条件不满足，整条记录进入噪声复核隔离并保留原文。

所有标注输出同时含英文稳定代码和中文字段，例如：

```json
{
  "llm_content_type": "navigation_only",
  "llm_content_type_zh": "仅导航或栏目页",
  "llm_exclusion_tags": ["navigation_only"],
  "llm_exclusion_tags_zh": ["仅导航或栏目内容"],
  "llm_policy_status_zh": "高置信度无关主题，已隔离"
}
```

## CMD 运行

已经激活 `(dj-env)` 后，从 `Clean_Gov` 根目录执行：

```bat
cd /d E:\A-Project\PublicHealth_Gov\Clean_Gov
```

只扫描输入，不写正式结果：

```bat
python data_juicer_pipeline\scripts\run_native_pipeline.py --dry-run
```

只准备输入和隔离队列：

```bat
python data_juicer_pipeline\scripts\run_native_pipeline.py --prepare-only
```

不调用 LLM，运行 Stage 00–03：

```bat
python data_juicer_pipeline\scripts\run_native_pipeline.py
```

调用公司模型，运行完整 Stage 00–05：

```bat
python data_juicer_pipeline\scripts\run_native_pipeline.py --enable-llm-quality --data-classification restricted --llm-concurrency 16
```

只重试某次成功运行中 Stage 04 的模型/结构化失败队列：

```bat
python data_juicer_pipeline\scripts\retry_stage04.py --run-id 20260806_152852_937068 --dry-run
python data_juicer_pipeline\scripts\retry_stage04.py --run-id 20260806_152852_937068 --llm-concurrency 16 --max-rounds 2
```

重试入口不会重跑 Stage 00–03，也不会覆盖原正式结果。它会清除旧的零分统计，只提交 `04_llm_retry_required.jsonl`；默认20,000字符以内使用完整正文，超过阈值则使用原文首部、中部、尾部各最多6,000字符的确定性审阅视图。完整原文始终保留。长文抽样只用于主题和质量判断，本轮不会依据不完整抽样自动删除栏目噪声。

每轮成功响应仍按原标准进入保留、无关主题、低质量、人工复核或继续失败队列。保留候选继续经过 Stage 05，然后与原正式结果按 `doc_id` 校验并生成新的修订版：

```text
data_juicer_pipeline\data\runs\<retry_id>\output\corpus_native_cleaned_<source_run_id>_retry_<retry_id>.jsonl
```

重试过程不会修改原 `corpus_native_cleaned_<source_run_id>.jsonl`。重试也作为一次独立 run，全部文件位于 `data/runs/<retry_id>`；其中 `intermediate`、`output`、`reports`、`quarantine`、`logs` 分别保存中间结果、修订版正式结果、报告、隔离和日志。脚本仍兼容读取改造前的历史运行目录。

显式使用测试附件目录：

```bat
python data_juicer_pipeline\scripts\run_native_pipeline.py --attachment-root attachment_clean\grg_test\A-goal\data\documents --enable-llm-quality --data-classification restricted --llm-concurrency 16
```

未激活环境时可用绝对解释器：

```bat
"D:\LZH\Environment\Anaconda3\anaconda3\envs\dj-env\python.exe" data_juicer_pipeline\scripts\run_native_pipeline.py --dry-run
```

不要执行空的 `"%DJ_PYTHON%"`。只有先在同一个 CMD 中运行 `set DJ_PYTHON=...` 后该变量才存在。

## 公司模型配置和保密

本机配置放在项目的 `.env.local`：

```dotenv
COMPANY_LLM_BASE_URL=http://10.61.5.9:7005/v1
COMPANY_LLM_MODEL=1
COMPANY_LLM_API_KEY=
```

公司接口不要求 Key，因此 Key 可为空。地址应为 `http://10.61.5.9:7005/v1`，不是 `http:///...`。配置检查不会发请求：

```bat
python data_juicer_pipeline\scripts\doctor.py --llm-provider company
python data_juicer_pipeline\scripts\run_native_pipeline.py --dry-run --enable-llm-quality
```

`restricted` 和 `internal` 数据禁止使用外部提供商，公司接口失败时也不会自动回退到硅基流动。只有明确可公开外发的数据，才允许同时使用：

```bat
python data_juicer_pipeline\scripts\run_native_pipeline.py --enable-llm-quality --llm-quality-provider siliconflow --data-classification public --allow-external-llm
```

## 输出怎么看

每次主流程或 Stage 04 重试都会生成唯一 `<run_id>`，该次运行的所有产物集中在同一个目录：

```text
data_juicer_pipeline\data\runs\<run_id>\
├─ intermediate\
├─ output\
├─ reports\
├─ quarantine\
└─ logs\
```

此结构只应用于改造后的新运行。原先位于 `data/intermediate/runs`、`data/output`、`data/reports/runs`、`data/quarantine/runs` 和 `data/logs/runs` 的历史结果原样保留，不迁移、不覆盖；计时查看和 Stage 04 重试仍兼容这些旧路径。

主流程唯一正式结果是：

```text
data_juicer_pipeline\data\runs\<run_id>\output\corpus_native_cleaned_<run_id>.jsonl
```

逐阶段结果位于：

```text
data_juicer_pipeline\data\runs\<run_id>\intermediate\
├─ 00_prepared\
├─ 01_normalized\
├─ 02_quality_filtered\
├─ 03_global_exact_dedup\
│  ├─ all_before_exact_dedup.jsonl
│  └─ all_after_exact_dedup.jsonl
├─ 04_llm_topic_quality\
│  ├─ all_scored_tagged.jsonl
│  ├─ all_annotated_zh.jsonl
│  └─ candidate_keep.jsonl
├─ 05_local_noise_cleanup\
│  ├─ all_kept.jsonl
│  └─ noise_cleaned.jsonl
├─ configs\
└─ data_juicer_work\
```

`all_scored_tagged.jsonl` 是 Data-Juicer 原始评分结果；人工查阅优先看 `all_annotated_zh.jsonl`，它含中英文标签和最终分流状态。`noise_cleaned.jsonl` 只保存实际发生栏目噪声修改的审计副本；`all_kept.jsonl` 是 Stage 05 全部保留结果。

被移出的记录均保存在：

```text
data_juicer_pipeline\data\runs\<run_id>\quarantine\
```

主要文件包括：

- `03_global_exact_duplicates.jsonl`：完整正文重复；
- `04_topic_excluded.jsonl`：高置信无关主题；
- `04_low_quality.jsonl`：质量分低于阈值；
- `04_llm_retry_required.jsonl`：模型/API/结构化结果失败；
- `04_manual_review_required.jsonl`：主题不确定或标签冲突；
- `05_noise_removal_review_required.jsonl`：栏目噪声删除未通过安全校验。

报告和日志分别位于 `data/runs/<run_id>/reports`、`data/runs/<run_id>/logs`。运行时会显示当前阶段、算子、处理条数、剩余条数和进度；耗时汇总写入 `logs/timing_summary.json`。`--output` 可显式指定其他正式结果路径；未指定时始终使用本次 run 的 `output`。查看最新一次计时：

```bat
python data_juicer_pipeline\scripts\show_timing.py
```

最终语料需要只保留任意指定字段时，使用字段适配脚本。以下命令把 `doc_id` 重命名为 `id`，并只保留 `id`、`title`、`text`：

```bat
python data_juicer_pipeline\scripts\export_fields.py --run-id <run_id> --fields id=doc_id,title,text
```

默认导出到该 run 的 `output/exports`，不修改正式结果；字段数量不限，缺失字段与覆盖策略详见完整手册。

## 默认阈值

非表格 Stage 02：

| 参数 | 默认值 |
| --- | --- |
| 正文长度 | 50–3,000,000 字符 |
| 中文/字母/数字比例 | 至少 0.45 |
| 特殊字符比例 | 不高于 0.75 |
| 10 字符 n-gram 重复比例 | 不高于 0.50 |
| 语言 | `zh`，置信度至少 0.50 |

LLM 和本地处置：

| 参数 | 默认值 | 命令行参数 |
| --- | --- | --- |
| 质量综合分 | 0.60 | `--llm-quality-min-score` |
| 主题置信度 | 0.90 | `--llm-topic-min-confidence` |
| 公共卫生相关度 | 4/5 | `--llm-min-public-health-relevance` |
| 噪声段置信度 | 0.90 | `--llm-noise-min-confidence` |
| 最大删除字符比例 | 0.30 | `--llm-noise-max-removed-ratio` |
| LLM 并发数 | 16 | `--llm-concurrency` |

## 实现边界

清洗、过滤、LLM 评分和完全去重优先使用 Data-Juicer 1.5.3 现成算子。项目代码只负责输入适配、通道分流、配置注入、隐私阻断、阶段校验、中文标签映射、保守的精确行删除、元数据恢复和隔离审计。

`scripts/run_stage_01.py` 是历史网页高频短行保护入口，为兼容旧报告而保留；新一轮网页与附件联合语料应使用 `run_native_pipeline.py`。
