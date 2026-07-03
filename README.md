# Clean_Gov 中文政府/公卫网页清洗项目

本仓库用于搭建和运行“中文政府/公卫网页 JSONL 清洗系统”。系统把 `Crawler_Gov` 产出的 parsed JSONL 和 raw HTML 清洗成稳定的 clean article，并导出可直接进入 DataTrove 的 `id / text / metadata` JSONL。

## 目录结构

```text
Clean_Gov/
├── README.md
├── .gitignore
├── article_cleaning_pipeline/
│   ├── README.md
│   ├── run_pipeline.py
│   ├── requirements.txt
│   ├── configs/
│   ├── src/
│   └── tests/
└── data/
    └── bodyClean/          # 本地生成输出，已被 .gitignore 忽略
```

## 核心子项目

清洗系统代码位于：

```text
article_cleaning_pipeline/
```

详细的分层流程、每个输出文件说明、验收标准和阅读说明见：

```text
article_cleaning_pipeline/README.md
```

## 输入与输出

默认输入来自同级爬虫项目：

```text
corpus_crawler/
├── Crawler_Gov/
└── Clean_Gov/
```

在这个目录结构下，pipeline 会自动推导默认路径：

```text
..\Crawler_Gov\data\output
..\Crawler_Gov\data\raw_html
```

默认输出写入：

```text
data\bodyClean
```

`data/bodyClean/` 是本地生成的大体量结果目录，默认不提交到 Git。

## 安装依赖

```bash
cd article_cleaning_pipeline
pip install -r requirements.txt
```

## 一键运行

推荐方式：保持 `Crawler_Gov` 和 `Clean_Gov` 是同级目录，然后直接使用默认相对路径。

```bash
cd article_cleaning_pipeline
python run_pipeline.py --run-all
```

也可以从 `Clean_Gov` 根目录显式传相对路径：

```bash
python article_cleaning_pipeline\run_pipeline.py ^
  --jsonl-dir "..\Crawler_Gov\data\output" ^
  --raw-html-dir "..\Crawler_Gov\data\raw_html" ^
  --output-dir "data\bodyClean" ^
  --run-all
```

## 单层运行

```bash
cd article_cleaning_pipeline

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

## 分层流程

```text
00_profile
01_validation
02_normalization
03_extraction
04_text_cleaning
05_structure
06_tables
07_assets
08_sensitive
09_dedup
10_quality
11_datatrove
```

每一层都会输出主结果文件、日志文件、统计报告和 `stage_summary.json`。

## 最重要的输出文件

运行完成后优先查看：

```text
data/bodyClean/README.md
data/bodyClean/pipeline_summary.json
data/bodyClean/technical_report.md
data/bodyClean/colleague_handoff_report.md
data/bodyClean/cleaned_articles.jsonl
data/bodyClean/manual_review_list.jsonl
data/bodyClean/datatrove_documents.jsonl
```

其中：

| 文件 | 用途 |
|---|---|
| `cleaned_articles.jsonl` | 最终 clean article 文件。 |
| `manual_review_list.jsonl` | 人工复核清单。 |
| `datatrove_documents.jsonl` | 可与同事同格式文件直接拼接并进入 DataTrove。 |
| `technical_report.md` | 技术说明、策略、限制和后续扩展。 |
| `colleague_handoff_report.md` | 给其他 parser/清洗同事看的字段规范和拼接注意事项。 |

## 验收方式

1. 查看 `data/bodyClean/run_manifest.json`，确认 00-11 层已完成。
2. 查看 `data/bodyClean/00_profile/data_health_check_report.md`，确认输入规模和主要问题。
3. 查看 `data/bodyClean/10_quality/final_quality_report.json`，确认质量标签分布和复核数量。
4. 抽查 `data/bodyClean/manual_review_list.jsonl`，确认不确定记录没有被静默丢弃。
5. 抽查 `data/bodyClean/11_datatrove/datatrove_documents.jsonl`，确认每行是 `id / text / metadata`。

## Git 提交建议

建议提交：

```text
README.md
.gitignore
article_cleaning_pipeline/
```

不建议提交：

```text
data/bodyClean/
```

该目录包含全量 JSONL、Excel 和报告输出，体量较大，已在 `.gitignore` 中忽略。
