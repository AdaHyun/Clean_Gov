# 同事交接报告

## 字段差异

请查看 00_profile/schema_diff_report.json、field_value_profile.xlsx、missing_value_report.xlsx。WHO 文件存在 language/geo/api/who_metadata 等额外字段，医保/体育文件 raw_html_path 可能指向子目录 page.html。

## 必须字段

doc_id、title、url、source.site_name、source.site_domain、source.channel_name、dates.publish_date、content.body_text 或 content.body_html、crawl.raw_html_path。

## 可空字段

attachments/images 可为空数组；issue_date、topic_tags、joint_departments、summary 可为空。

## 语义约定

source_department 是 parser 原始来源/部门；issuing_department 是发文司局；content_source 是转载来源；issuing_authority 是正式发文主体。

## 枚举和资产

document_type/policy_category 以 controlled_vocabulary.json 为准；附件需包含 name/url/local_path/file_type，图片需包含 url/local_path/download_status。

## raw_html 与正文

raw_html_path 最好保存相对 Crawler_Gov 根目录的 `data/raw_html/...`；body_html 尽量保存正文容器，若只能保存完整 body，清洗层会用 site_rules/自动 selector 处理。

## 拼接标准

最终 clean 文件保留标准 schema；DataTrove 输出必须为 `id/text/metadata`，可直接按行拼接两个同事的 datatrove_documents.jsonl。

## 风险点

不要静默丢弃短正文、日期冲突、附件缺失、敏感信息和正文定位失败记录；它们应进入 manual_review_list.jsonl。

