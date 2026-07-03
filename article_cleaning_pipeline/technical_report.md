# 技术报告

## 数据体检结果

# 数据体检报告

## 总体规模

JSONL 文件数：13；记录数：18611；raw_html 文件数：15501。

## 字段差异

顶层/嵌套字段共 157 个，非全量字段 116 个，类型冲突 1 个。详见 schema_profile.json 和 schema_diff_report.json。

## 主要问题

正文为空 289 条，正文过短 888 条；导航噪声命中：{'首页': 1464, '版权所有': 1326, '网站地图': 1314, '分享到': 1183, 'ICP备案': 1042, '无障碍': 332, '当前位置': 220, '长者版': 202, '主办单位': 190, 'English': 48, '下一篇': 14, '上一篇': 13, '打印本页': 6, '关闭窗口': 6, '责任编辑': 5}。模板/重复标题示例：[{'title': '政府信息公开详情', 'count': 305}, {'title': '工作动态', 'count': 54}, {'title': '政策文件', 'count': 54}, {'title': '关于我们', 'count': 54}, {'title': '404 Not Found', 'count': 27}, {'title': 'Water, sanitation, hygiene, waste and electricity services in health care facilities: progress on the fundamentals', 'count': 27}, {'title': '全国新型冠状病毒感染疫情情况', 'count': 23}, {'title': '李克强主持召开国务院常务会议_广东省医疗保障局', 'count': 21}, {'title': '伤害预防', 'count': 12}, {'title': '广东省疾病预防控制中心招聘科研助理的公告', 'count': 11}, {'title': '健康生活方式', 'count': 11}, {'title': '广东省卫生健康委公布2023年6月全省突发公共卫生事件信息', 'count': 7}, {'title': '国家卫生健康委员会医师资格考试委员会公告', 'count': 7}, {'title': '体育总局竞体司关于对授予国际级运动健将和运动健将称号运动员公示的通知', 'count': 7}, {'title': '国家卫生健康委员会公告', 'count': 6}, {'title': '国家医疗保障局 通知公告 首都医科大学国家医疗保障研究院人员招聘公告', 'count': 6}, {'title': '国家体育总局人事任免', 'count': 6}, {'title': '国家疾控局综合司关于印发学校等重点场所诺如病毒感染防控消毒技术指南的通知', 'count': 5}, {'title': '国家卫生健康委办公厅关于印发肺癌筛查与早诊早治方案（2024年版）和结直肠癌筛查与早诊早治方案（2024年版）的通知', 'count': 5}, {'title': '中华人民共和国传染病防治法', 'count': 5}]。

## 重复候选

duplicate_candidate_profile.json 仅统计非空 content.body_text 的 SHA1 重复，并附带每组 doc_id/title/url；空正文单独写入 empty_content_profile.json，避免 SHA1 空串被误认为正文重复。

## raw_html 关联

可在 raw_html_linkage_report.xlsx 查看每条记录 raw_html_path 是否能回溯到真实文件。

## 对同事提示

请保持 doc_id/title/url/source/organization/classification/dates/content/attachments/images/crawl/raw 的稳定结构；raw_html_path 建议保存相对 Crawler_Gov 根目录的 data/raw_html 路径或可直接定位的 page.html。

## 为什么不能直接喂给 DataTrove

原始 JSONL 存在导航噪声、正文过短、raw_html_path 不统一、字段语义差异、附件图片状态不一、WHO 等扩展字段差异；直接喂入会污染语料并影响去重。

## Pipeline 设计

00 体检、01 校验、02 标准化、03 正文定位、04 去噪、05 结构、06 表格、07 资产、08 敏感、09 去重、10 质量、11 DataTrove 导出。每层输出可作为下一层输入。

## 策略说明

字段标准化保留原始字段并补充 canonical 字段；正文定位优先 body_html 后回溯 raw_html；去噪只删模板噪声和修格式；去重先做精确 hash 同文异址合并；质量层统一人工复核原因。

## 当前限制

复杂表格、PDF/DOCX/Excel 深度解析、跨语种 WHO 内容抽取、近重复大规模 MinHash/OCR 作为后续扩展；当前版本优先端到端稳定运行。

## 运行摘要

{
  "started_at": "2026-07-03T09:35:00",
  "jsonl_dir": "D:\\LZH\\A-Project\\Crawler311\\corpus_crawler\\Crawler_Gov\\data\\output",
  "raw_html_dir": "D:\\LZH\\A-Project\\Crawler311\\corpus_crawler\\Crawler_Gov\\data\\raw_html",
  "output_dir": "D:\\LZH\\A-Project\\Crawler311\\corpus_crawler\\Clean_Gov\\data\\bodyClean",
  "rule_version": "0.1.0",
  "stages": {
    "profile": {
      "records": 18611,
      "manual_review": 0,
      "outputs": [
        "D:\\LZH\\A-Project\\Crawler311\\corpus_crawler\\Clean_Gov\\data\\bodyClean\\00_profile"
      ],
      "elapsed_seconds": 226.746
    }
  },
  "finished_at": "2026-07-03T09:38:47"
}

