# 中文政府/公卫网页 JSONL 清洗系统

## 项目目标

本项目把多个政府、公卫、医保、WHO 等来源的 parsed JSONL 和 raw_html 清洗为稳定的 clean article，并导出可直接喂给 DataTrove 的 `id / text / metadata` JSONL。系统按层输出，每一层都有主结果、日志、统计报告和复核依据。

## 输入数据

默认输入：

```text
JSONL_INPUT_DIR = D:\LZH\A-Project\Crawler311\corpus_crawler\Crawler_Gov\data\output
RAW_HTML_DIR    = D:\LZH\A-Project\Crawler311\corpus_crawler\Crawler_Gov\data\raw_html
OUTPUT_DIR      = D:\LZH\A-Project\Crawler311\corpus_crawler\Clean_Gov\data\bodyClean
```

JSONL 记录建议包含：

```text
doc_id, title, url, source, organization, classification, dates,
content, attachments, images, crawl, raw
```

## 安装依赖

```bash
pip install -r requirements.txt
```

## 一键运行

```bash
python run_pipeline.py ^
  --jsonl-dir "D:\LZH\A-Project\Crawler311\corpus_crawler\Crawler_Gov\data\output" ^
  --raw-html-dir "D:\LZH\A-Project\Crawler311\corpus_crawler\Crawler_Gov\data\raw_html" ^
  --output-dir "D:\LZH\A-Project\Crawler311\corpus_crawler\Clean_Gov\data\bodyClean" ^
  --run-all
```

## 单层运行

```bash
python run_pipeline.py --stage profile
python run_pipeline.py --stage validation
python run_pipeline.py --stage normalization
python run_pipeline.py --stage extraction
python run_pipeline.py --stage cleaning
python run_pipeline.py --stage structure
python run_pipeline.py --stage tables
python run_pipeline.py --stage assets
python run_pipeline.py --stage sensitive
python run_pipeline.py --stage dedup
python run_pipeline.py --stage quality
python run_pipeline.py --stage datatrove
```

## Pipeline 流程

```text
原始 JSONL + raw_html
  ↓
00_profile          数据体检
  ↓
01_validation       基础校验
  ↓
02_normalization    字段标准化
  ↓
03_extraction       正文区域定位
  ↓
04_text_cleaning    正文去噪与文本规范化
  ↓
05_structure        正文结构重建
  ↓
06_tables           表格解析
  ↓
07_assets           附件/图片引用处理
  ↓
08_sensitive        敏感信息识别与分级标记
  ↓
09_dedup            去重与同文异址合并
  ↓
10_quality          质量评估与人工复核
  ↓
11_datatrove        DataTrove 输入格式导出
```

## 最终交付文件

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `cleaned_articles.jsonl` | `10_quality/cleaned_articles.jsonl` 的根目录副本；标准化 clean article 全量结果。 | 每行是一个 JSON；包含 `doc_id/title/url/source/organization/classification/dates/content/attachments/images/privacy/dedup/quality` 等字段；记录数应等于 `10_quality/final_quality_report.json.records`。 | 用于内部留档、复核、和同事 clean 文件拼接前检查。 |
| `datatrove_documents.jsonl` | `11_datatrove/datatrove_documents.jsonl` 的根目录副本；DataTrove 输入文件。 | 每行必须是 `id/text/metadata`；`text` 非空；可与同事同格式文件直接按行拼接。 | 后续喂给 DataTrove 时优先使用这个文件。 |
| `manual_review_list.jsonl` | `10_quality/manual_review_list.jsonl` 的根目录副本；所有需人工复核记录。 | 每行包含 `doc_id/url/title/quality_label/review_reasons/suggested_action`。 | 复核时按 `review_reasons` 分类处理，优先看 `empty_clean_text/short_clean_text/high_sensitive_risk`。 |
| `pipeline_summary.json` | 本次 pipeline 运行摘要。 | 包含输入目录、输出目录、规则版本、每层记录数、耗时、复核数量。 | 快速确认本次运行是否完整。 |
| `run_manifest.json` | 与 `pipeline_summary.json` 类似，记录运行 manifest。 | 每次运行应更新；每个 stage 有 `records/manual_review/outputs/elapsed_seconds`。 | 用于审计和复现。 |
| `technical_report.md` | 技术报告，包括数据问题、设计依据、策略、限制。 | 应说明为什么不能直接喂 DataTrove、每层策略、当前限制和扩展路线。 | 给开发者、负责人看。 |
| `colleague_handoff_report.md` | 同事交接报告。 | 应说明字段差异、parser 规范、拼接标准、风险点。 | 给另一个写 parser 或清洗代码的同事看。 |
| `README.md` | 当前说明文件。 | 应包含运行方式、输出文件说明、验收标准、阅读说明。 | 项目入口文档。 |

## 00_profile 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `00_profile/input_inventory.json` | 输入 JSONL 文件清单、每个文件记录数、文件大小、raw_html 文件数。 | `files[].records` 加总应等于 profile stage 的 `records`。 | 先看这个确认本次扫描覆盖了哪些文件。 |
| `00_profile/schema_profile.json` | 字段树频次和字段类型统计。 | 包含 `total_records/field_frequency/field_types`。 | 用来判断字段是否稳定、哪些字段只在部分来源出现。 |
| `00_profile/schema_diff_report.json` | 非全量字段和类型冲突字段。 | 包含 `fields_not_in_all_records/type_conflicts`。 | 与同事对齐 schema 时重点看。 |
| `00_profile/field_value_profile.xlsx` | 常见枚举值画像，例如站点、栏目、文种、政策分类、日期值。 | Excel 可打开；包含 `field/value/count`。 | 适合人工快速看字段值是否混乱。 |
| `00_profile/missing_value_report.xlsx` | 字段缺失统计。 | 包含 `field/missing_count/missing_rate/files`。 | 高缺失率字段需要判断是正常可空还是 parser 问题。 |
| `00_profile/channel_distribution.xlsx` | 机构/栏目分布。 | 包含来源文件、站点、栏目、count。 | 用于判断采样覆盖和栏目规模。 |
| `00_profile/source_department_profile.xlsx` | 来源部门、文种、政策分类分布。 | 包含 site/domain/department/document_type/policy_category/count。 | 检查 `source_department` 是否被误塞入正文或长文本。 |
| `00_profile/attachment_image_profile.xlsx` | 每条记录附件数、图片数、附件类型概况。 | 每行对应一条记录；应有 `attachment_count/image_count`。 | 用于发现附件集中缺失或图片装饰噪声。 |
| `00_profile/raw_html_linkage_report.xlsx` | `crawl.raw_html_path` 是否能找到真实文件。 | 包含 `doc_id/raw_html_path/exists/resolved_path`。 | `exists=false` 的记录后续正文回溯能力较弱。 |
| `00_profile/content_noise_profile.json` | 空正文、短正文、模板噪声命中统计。 | 包含 `empty/too_short/noise_hits`。 | 判断正文抽取质量的第一入口。 |
| `00_profile/site_selector_candidates.json` | 自动发现的正文 selector 候选。 | 按域名列出 selector 及命中次数。 | 更新 `configs/site_rules.yaml` 时参考。 |
| `00_profile/table_profile.xlsx` | 每条记录 table 数量。 | 包含 `doc_id/table_count/site`。 | `table_count>0` 的记录后续需看 `06_tables`。 |
| `00_profile/duplicate_candidate_profile.json` | 非空 `content.body_text` 的重复候选组。 | 不应包含空串 SHA1 `da39a3ee...`；每组包含 `hash/count/basis/records`，`records` 中有 `doc_id/title/url/input_file/line_no`。 | 用它定位疑似重复正文；注意只是体检候选，不等于最终去重结果。 |
| `00_profile/empty_content_profile.json` | 空正文记录明细。 | `count` 应等于 `content_noise_profile.json.empty`；包含空串 hash 说明和 records 明细。 | 用来排查 PDF 页、图片解读页、parser 未抽正文等情况。 |
| `00_profile/data_health_check_report.md` | 数据体检文字报告。 | 应概括规模、字段差异、主要问题、raw_html 关联、重复候选说明。 | 给人快速读，不代替 JSON/XLSX 明细。 |
| `00_profile/stage_summary.json` | profile 层运行摘要。 | 包含 `records/manual_review/outputs/elapsed_seconds`。 | 用于确认本层是否完成。 |

## 01_validation 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `01_validation/validated_records.jsonl` | 校验后可继续进入下一层的记录。 | 每行保留原始字段，并附 `pipeline.validation_status`。 | 下一层 normalization 的输入。 |
| `01_validation/invalid_records.jsonl` | 严重不合格或待修复记录。 | 不应静默丢弃；每条应包含错误原因。 | 需要人工判断是否修 parser 或补字段。 |
| `01_validation/validation_errors.jsonl` | 校验错误和 warning 明细。 | 包含 `doc_id/status/field/error/repair_hint`。 | 排查字段结构问题时优先看。 |
| `01_validation/validation_summary.json` | 校验状态统计。 | 应统计 `valid/valid_with_warning/invalid_repairable/invalid_drop_candidate`。 | 快速看输入基础质量。 |
| `01_validation/validation_report.md` | 校验文字报告。 | 应解释哪些状态会继续流转。 | 给人工阅读。 |
| `01_validation/stage_summary.json` | validation 层运行摘要。 | `records` 应等于输入总记录数。 | 与 manifest 对账。 |

## 02_normalization 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `02_normalization/normalized_records.jsonl` | 字段标准化后的记录。 | 每条应有 `canonical_url`、标准日期、标准栏目、标准机构 code、附件/图片标准字段。 | 下一层 extraction 的输入。 |
| `02_normalization/field_correction_log.jsonl` | 字段修正日志。 | 自动补齐或修正字段时必须写日志；每行应有 `doc_id/field/old/new/rule`。 | 当前为空或较少不代表错误，说明这批可自动修正项少。 |
| `02_normalization/unmapped_values_report.json` | 未映射枚举值报告。 | 应存在；后续新增枚举未映射时写入。 | 用于完善配置映射表。 |
| `02_normalization/controlled_vocabulary.json` | 当前数据实际枚举词表。 | 包含 `document_type/policy_category/channel_name/site_name` 等分布。 | 同事对齐标准枚举时看。 |
| `02_normalization/standard_schema.json` | 标准 schema 配置快照。 | 应来自 `configs/standard_schema.yaml`。 | 用于确认 clean 文件字段契约。 |
| `02_normalization/normalization_summary.json` | 标准化统计摘要。 | 包含记录数、修正数。 | 快速看本层结果。 |
| `02_normalization/normalization_report.md` | 标准化文字报告。 | 应说明机构、栏目、日期、URL、附件图片标准化策略。 | 给人工阅读。 |
| `02_normalization/stage_summary.json` | normalization 层运行摘要。 | `records` 应等于 `normalized_records.jsonl` 行数。 | 与 manifest 对账。 |

## 03_extraction 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `03_extraction/article_html_records.jsonl` | 正文 HTML 定位后的记录，含 `content.clean_html`、`selector_used`、`extraction_method`。 | 可继续进入 text cleaning；失败记录不能丢，应带 `manual_review_reasons`。 | 下一层 cleaning 的输入。 |
| `03_extraction/extraction_log.jsonl` | 每条记录正文抽取日志。 | 包含 `doc_id/method/selector_used/confidence/fallback_used` 或失败原因。 | 排查正文定位失败时看。 |
| `03_extraction/site_selector_rules_discovered.json` | 按域名统计自动发现 selector。 | selector 候选应按频次排序。 | 用于完善 `configs/site_rules.yaml`。 |
| `03_extraction/extraction_failure_records.jsonl` | 正文定位失败或不可信记录。 | 每条应有失败原因或复核标记。 | 人工复核正文抽取时使用。 |
| `03_extraction/extraction_report.md` | 正文区域定位报告。 | 应说明成功数、失败数、fallback 策略。 | 给人工快速看。 |
| `03_extraction/stage_summary.json` | extraction 层运行摘要。 | `manual_review` 应等于失败需复核数。 | 与 manifest 对账。 |

## 04_text_cleaning 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `04_text_cleaning/content_cleaned_records.jsonl` | 生成 `content.clean_text` 后的记录。 | 每条应保留 `clean_html`，并新增 `clean_text/raw_text_length/clean_text_length`。 | 下一层 structure 的输入。 |
| `04_text_cleaning/noise_removal_log.jsonl` | 被删除的模板噪声行日志。 | 包含 `doc_id/removed_lines/removed_count`。 | 检查是否误删正文时看。 |
| `04_text_cleaning/text_normalization_log.jsonl` | 文本规范化日志。 | 包含原始长度、清洗后长度、残留噪声。 | 判断清洗幅度是否异常。 |
| `04_text_cleaning/noise_residue_report.json` | 清洗后仍残留的噪声模式统计。 | 命中越少越好；高频残留应补 `noise_patterns.yaml`。 | 下一轮规则优化依据。 |
| `04_text_cleaning/text_cleaning_report.md` | 文本清洗报告。 | 应说明清洗条数、噪声删除日志条数、原则。 | 给人工阅读。 |
| `04_text_cleaning/stage_summary.json` | cleaning 层运行摘要。 | `records` 应等于 `content_cleaned_records.jsonl` 行数。 | 与 manifest 对账。 |

## 05_structure 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `05_structure/structured_records.jsonl` | 带 `content.elements` 的结构化记录。 | 每条记录应可继续进入 table/assets/sensitive。 | 下一层 tables 的输入。 |
| `05_structure/content_elements.jsonl` | 拆分后的正文元素明细。 | 每行应有 `doc_id/element_id/type/text/order/level/quality_flags`。 | 做段落、标题、条款、附件引用分析时用。 |
| `05_structure/structure_rebuild_log.jsonl` | 每条记录结构重建日志。 | 包含元素数量和类型分布。 | 排查结构化效果。 |
| `05_structure/signature_extraction_report.json` | 元素类型统计，包括签署日期等。 | 应展示各 element type 数量。 | 判断规则是否过宽或过窄。 |
| `05_structure/attachment_ref_report.json` | 正文附件引用统计。 | 包含附件引用元素数量。 | 与 `07_assets` 联合看。 |
| `05_structure/structure_report.md` | 结构重建报告。 | 应说明当前规则范围和限制。 | 给人工阅读。 |
| `05_structure/stage_summary.json` | structure 层运行摘要。 | `records` 应等于 `structured_records.jsonl` 行数。 | 与 manifest 对账。 |

## 06_tables 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `06_tables/table_parsed_records.jsonl` | 附加表格解析结果后的记录。 | 记录中 `content.tables` 应保存成功或失败表格信息。 | 下一层 assets 的输入。 |
| `06_tables/tables.jsonl` | 成功解析的表格明细。 | 每行应有 `doc_id/table_id/headers/rows/raw_table_html/parse_status`。 | 抽取结构化表格时用。 |
| `06_tables/table_parse_log.jsonl` | 每条记录表格解析日志。 | 包含 `doc_id/table_count/failure_count`。 | 快速定位含表页面和失败页面。 |
| `06_tables/table_failure_records.jsonl` | 解析失败表格。 | 每条应保留 `raw_table_html/failure_reason`。 | 复杂表格人工复核入口。 |
| `06_tables/table_report.md` | 表格解析报告。 | 应说明解析成功数、失败数、策略。 | 给人工阅读。 |
| `06_tables/stage_summary.json` | tables 层运行摘要。 | `manual_review` 应等于失败表格数量或需复核数量。 | 与 manifest 对账。 |

## 07_assets 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `07_assets/asset_linked_records.jsonl` | 附件/图片状态和正文引用关系处理后的记录。 | 附件应有 `referenced_in_text`，图片应有 `image_role`。 | 下一层 sensitive 的输入。 |
| `07_assets/attachment_status_report.jsonl` | 附件状态明细。 | 每行包含 `doc_id/name/exists/referenced_in_text/file_ext`。 | 检查附件缺失、类型异常、正文未引用。 |
| `07_assets/image_status_report.jsonl` | 图片状态明细。 | 每行包含 `doc_id/url/exists/image_role`。 | 区分正文图片与 logo/icon/二维码等装饰图。 |
| `07_assets/asset_linking_log.jsonl` | 每条记录资产处理日志。 | 包含附件数、匹配引用数、图片数。 | 快速看资产处理概况。 |
| `07_assets/asset_report.md` | 附件图片处理报告。 | 应说明当前不阻塞主流程的附件解析策略。 | 给人工阅读。 |
| `07_assets/stage_summary.json` | assets 层运行摘要。 | `records` 应等于 `asset_linked_records.jsonl` 行数。 | 与 manifest 对账。 |

## 08_sensitive 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `08_sensitive/sensitive_marked_records.jsonl` | 已标记敏感信息的记录。 | 每条应有 `privacy.sensitive_hits/risk_level/action`。 | 下一层 dedup 的输入。 |
| `08_sensitive/sensitive_info_report.jsonl` | 敏感信息统计明细。 | 包含 `doc_id/risk_level/action/hit_count/types`。 | 复核敏感风险时看。 |
| `08_sensitive/sensitive_manual_review_list.jsonl` | 高风险敏感信息复核清单。 | 高风险应进入此文件；公开办公电话邮箱一般不应强制删除。 | 优先人工处理身份证、患者个案、轨迹信息。 |
| `08_sensitive/sensitive_report.md` | 敏感信息识别报告。 | 应说明识别类型、分级和动作原则。 | 给人工阅读。 |
| `08_sensitive/stage_summary.json` | sensitive 层运行摘要。 | `manual_review` 应等于高风险复核数。 | 与 manifest 对账。 |

## 09_dedup 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `09_dedup/deduplicated_records.jsonl` | 去重后 canonical 记录。 | 每个重复组只保留 canonical；保留 `dedup.canonical_doc_id`。 | 下一层 quality 的输入。 |
| `09_dedup/duplicate_groups.jsonl` | 重复组摘要。 | 每行包含 `duplicate_group_id/canonical_doc_id/size/urls`。 | 看同文异址合并结果。 |
| `09_dedup/duplicate_candidates.jsonl` | 重复候选明细。 | 包含每条重复记录与 canonical 的关系。 | 排查是否错误合并。 |
| `09_dedup/dedup_log.jsonl` | 去重日志。 | 包含重复组 id、方法、组大小。 | 审计去重行为。 |
| `09_dedup/dedup_report.md` | 去重报告。 | 应说明输入数、输出数、重复组数、与 DataTrove 去重关系。 | 给人工阅读。 |
| `09_dedup/stage_summary.json` | dedup 层运行摘要。 | `records` 应等于 `deduplicated_records.jsonl` 行数。 | 与 manifest 对账。 |

## 10_quality 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `10_quality/cleaned_articles.jsonl` | 最终 clean article 主文件。 | 每行一个 JSON；必须保留可追溯字段、清洗字段、质量标签。 | 后续内部拼接、抽检、导出 DataTrove 的来源。 |
| `10_quality/manual_review_list.jsonl` | 最终人工复核清单。 | 每行包含复核原因和建议动作；不能静默丢弃不确定记录。 | 人工验收最重要文件之一。 |
| `10_quality/quality_scores.jsonl` | 每条记录质量评分和原因。 | 包含 `doc_id/quality_label/quality_score/reasons/site`。 | 可按站点或原因排序抽检。 |
| `10_quality/site_quality_summary.xlsx` | 站点级质量标签统计。 | Excel 可打开；按 `site/label/count` 聚合。 | 看哪个站点质量问题最多。 |
| `10_quality/final_quality_report.json` | 最终质量统计。 | 包含总记录数、复核数、质量标签分布。 | 快速验收整体质量。 |
| `10_quality/quality_report.md` | 质量评估报告。 | 应说明质量标签、复核原因和复核策略。 | 给人工阅读。 |
| `10_quality/stage_summary.json` | quality 层运行摘要。 | `records` 应等于 `cleaned_articles.jsonl` 行数。 | 与 manifest 对账。 |

## 11_datatrove 输出文件说明

| 文件 | 输出内容介绍 | 验收标准 | 阅读说明 |
|---|---|---|---|
| `11_datatrove/datatrove_documents.jsonl` | DataTrove 输入文件。 | 每行必须包含 `id/text/metadata`；`text` 非空；metadata 字段稳定。 | 可与同事同格式文件直接拼接后进入 DataTrove。 |
| `11_datatrove/datatrove_export_summary.json` | DataTrove 导出统计。 | 包含输入记录数、成功导出数、schema 名称。 | 检查是否有空文本未导出。 |
| `11_datatrove/datatrove_export_report.md` | DataTrove 导出报告。 | 应说明导出条数和拼接方式。 | 给人工阅读。 |
| `11_datatrove/stage_summary.json` | datatrove 层运行摘要。 | `records` 应等于导出 JSONL 行数。 | 与 manifest 对账。 |

## 配置文件说明

| 配置 | 作用 |
|---|---|
| `configs/standard_schema.yaml` | 标准输入/输出字段契约。 |
| `configs/site_rules.yaml` | 按域名配置正文 selector。 |
| `configs/noise_patterns.yaml` | 模板噪声词表。 |
| `configs/channel_map.yaml` | 栏目标准化映射。 |
| `configs/document_type_rules.yaml` | 文种标准化映射。 |
| `configs/policy_category_map.yaml` | 政策分类标准化映射。 |
| `configs/sensitive_patterns.yaml` | 敏感信息分级说明。 |
| `configs/quality_rules.yaml` | 质量评估阈值。 |
| `configs/datatrove_export_schema.yaml` | DataTrove metadata 字段契约。 |

## 如何新增机构或栏目规则

1. 先看 `00_profile/site_selector_candidates.json` 和 `raw_html_linkage_report.xlsx`。
2. 在 `configs/site_rules.yaml` 为新域名补正文 selector。
3. 在 `channel_map.yaml`、`document_type_rules.yaml`、`policy_category_map.yaml` 补标准映射。
4. 重新运行对应层，例如：

```bash
python run_pipeline.py --stage extraction
python run_pipeline.py --stage cleaning
python run_pipeline.py --stage quality
python run_pipeline.py --stage datatrove
```

## 如何验收一次完整运行

1. 看 `pipeline_summary.json` 或 `run_manifest.json`，确认 00-11 层均完成。
2. 看 `00_profile/data_health_check_report.md`，确认输入规模和主要问题。
3. 看 `01_validation/validation_summary.json`，确认 invalid 是否可解释。
4. 看 `03_extraction/extraction_failure_records.jsonl`，抽查正文定位失败原因。
5. 看 `10_quality/final_quality_report.json` 和 `manual_review_list.jsonl`，确认复核规模。
6. 抽查 `11_datatrove/datatrove_documents.jsonl`，确认每行是 `id/text/metadata` 且 `text` 非空。

## 常见问题

`duplicate_candidate_profile.json` 里为什么看不到空字符串 hash？

空正文已经单独写入 `00_profile/empty_content_profile.json`。重复候选只统计非空正文，避免把 `SHA1("")` 误读为正文重复。

raw_html_path 找不到怎么办？

查看 `00_profile/raw_html_linkage_report.xlsx` 的 `exists=false` 记录。优先在 parser 阶段修正 `crawl.raw_html_path`，保存相对 `Crawler_Gov` 根目录或 `data/raw_html` 的稳定路径。

为什么 DataTrove 条数少于 clean article？

`11_datatrove` 会跳过 `content.clean_text` 为空的记录。查看 `11_datatrove/datatrove_export_summary.json` 和 `10_quality/manual_review_list.jsonl`。

