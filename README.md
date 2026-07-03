# Clean_Gov 中文政府/公卫网页清洗项目

本仓库用于搭建和运行“中文政府/公卫网页 JSONL 清洗系统”。系统把 `Crawler_Gov` 产出的 parsed JSONL 和 raw HTML 清洗成稳定的 clean article，并导出可直接进入 DataTrove 的 `id / text / metadata` JSONL。

## 目录结构

```text
Clean_Gov/
├── README.md
├── .gitignore
├── article_cleaning_pipeline/
│   ├── README.md
│   ├── pipeline_00_raw_repair.py
│   ├── pipeline_main.py
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

## 环境与依赖配置

为保证本项目可以在不同机器上顺利复用，建议使用独立虚拟环境运行本项目，不建议直接使用系统 Python 环境。

### 1. Python 版本建议

推荐使用：

```bash
Python 3.10+
```

建议优先使用 Python 3.10 或 3.11，避免因 Python 版本过高或过低导致部分第三方库兼容问题。

### 2. 安装依赖

激活虚拟环境后，在项目根目录执行：

```bash
pip install -r requirements.txt
```

如果后续新增依赖，请同步更新 `requirements.txt`：

```bash
pip freeze > requirements.txt
```

### 4. 路径配置

本项目的输入输出路径、原爬虫项目路径等信息统一写在：

```text
configs/path_config.yaml
```

用户复用本项目时，通常只需要修改该配置文件中的路径，不需要直接改代码。

示例：

```yaml
crawler:
  crawler_root: "../Crawler_Gov"
  jsonl_dir: "../Crawler_Gov/data/output"
  raw_html_dir: "../Crawler_Gov/data/raw_html"
  image_root_dir: "../Crawler_Gov/data/images"
  attachment_root_dir: "../Crawler_Gov/data/attachments"

raw_repair:
  output_dir: "data/bodyClean/00_raw_repair"
  manifest: "data/bodyClean/00_raw_repair/repair_manifest.jsonl"
  output_raw_dir: "data/bodyClean/data/raw"

main_pipeline:
  input_raw_dir: "data/bodyClean/data/raw"
  output_dir: "data/bodyClean"
```

### 5. 说明

本项目尽量将路径、规则和执行流程配置化，避免将本地路径写死在代码中。复用者在新的机器或新的数据目录下运行时，应优先修改 `configs/path_config.yaml`，并确保原始 JSONL、raw_html、图片目录和附件目录路径正确。

## 推荐执行顺序

第一步，进入 pipeline 目录：

```bash
cd article_cleaning_pipeline
```

第二步，先执行 clean 前独立修复阶段。`00_raw_repair` 不会自动进入正式 clean pipeline：

```bash
python pipeline_00_raw_repair.py --mode scan
python pipeline_00_raw_repair.py --mode repair
```

第三步，人工核验 repair report、failed_after_repair、manual_review_list 和 `data/bodyClean/data/raw/*.jsonl`。如果人工补了失败文件，再执行：

```bash
python pipeline_00_raw_repair.py --mode verify
```

第四步，人工确认 verify 结果后，执行正式 clean pipeline。默认执行 01-12，不包含 00：

```bash
python pipeline_main.py
```

## 单层运行

```bash
cd article_cleaning_pipeline

python pipeline_main.py --stage 01_profile
python pipeline_main.py --stage 02_validation
python pipeline_main.py --stage 03_normalization
python pipeline_main.py --stage 04_extraction
python pipeline_main.py --stage 05_text_cleaning
python pipeline_main.py --stage 06_structure
python pipeline_main.py --stage 07_tables
python pipeline_main.py --stage 08_assets
python pipeline_main.py --stage 09_sensitive
python pipeline_main.py --stage 10_dedup
python pipeline_main.py --stage 11_quality
python pipeline_main.py --stage 12_datatrove_export
```

## 分层流程

```text
00_raw_repair  # 独立执行，不自动进入 01
01_profile
02_validation
03_normalization
04_extraction
05_text_cleaning
06_structure
07_tables
08_assets
09_sensitive
10_dedup
11_quality
12_datatrove_export
```

每一层都会输出主结果文件、日志文件、统计报告和 `stage_summary.json`。正式 clean pipeline 的输入是 `data/bodyClean/data/raw/*.jsonl`。

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

1. 查看 `data/bodyClean/00_raw_repair/scan_summary.json` 和 `repair_manifest.jsonl`，确认旧数据缺失项可解释。
2. repair 后查看 `repair_summary.json`、`failed_after_repair.jsonl` 和 `manual_review_list.jsonl`。
3. 人工补文件后查看 `verify_summary.json`，确认仍失败项可接受。
4. 查看 `data/bodyClean/run_manifest.json`，确认 01-12 层已完成。
5. 查看 `data/bodyClean/01_profile/data_health_check_report.md`，确认输入规模和主要问题。
6. 查看 `data/bodyClean/11_quality/final_quality_report.json`，确认质量标签分布和复核数量。
7. 抽查 `data/bodyClean/manual_review_list.jsonl`，确认不确定记录没有被静默丢弃。
8. 抽查 `data/bodyClean/12_datatrove_export/datatrove_documents.jsonl`，确认每行是 `id / text / metadata`。

