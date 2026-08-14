# 公司文档异步批量解析与镜像输出

本项目在原单文件客户端基础上增量支持：目录批量提交、自动回调、批次统计、原始审计结果、可选可读/镜像目录。原有 `submit`、`query` 和 `serve-callback` 命令保持兼容。

原始输入文件只读。程序不会移动、改名或删除输入文件，也不会在输入目录内写解析结果。

## 1. 服务器准备

服务器项目目录：

```text
/home/linzihan/PublicHealthProject/Clean_Gov
```

首次安装：

```bash
cd /home/linzihan/PublicHealthProject/Clean_Gov
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.server.example .env.server
```

将真实凭据只填写到 `.env.server`。它已被 `.gitignore`排除，不要提交、截图或复制到日志。

每次登录后：

```bash
cd /home/linzihan/PublicHealthProject/Clean_Gov
source .venv/bin/activate
set -a
source .env.server
set +a
python -m src.cli doctor
```

程序也会自动读取项目根目录的 `.env.server`。显式 `source`便于其他诊断命令使用相同环境。

## 2. 回调地址与服务

如果 `.env.server`没有设置 `COMPANY_CALLBACK_URL`，`batch-submit`会执行与下面等价的路由检测：

```bash
ip route get 10.61.5.9
```

读取其中的 `src`地址，并结合8800端口和 `COMPANY_CALLBACK_TOKEN`自动构造回调地址。日志只显示掩码token。

启动回调服务：

```bash
python -m src.cli serve-callback
```

检查监听：

```bash
ss -ltnp | grep ':8800'
```

查看脱敏回调日志：

```bash
tail -f data/logs/callback.log
```

批量解析期间回调服务必须一直运行。当前可先保持SSH会话；正式长期运行建议后续配置systemd。

## 3. Windows上传整个测试目录

PowerShell：

```powershell
cd D:\LZH\A-Project\Crawler311\corpus_crawler\Clean_Gov\attachment_clean_Company

powershell -ExecutionPolicy Bypass -File scripts\upload_batch.ps1 `
  -LocalDir "D:\待上传项目\data\attachments\gov_files" `
  -Server "服务器地址" `
  -User "linzihan" `
  -RemoteDir "/home/linzihan/PublicHealthProject/Clean_Gov/data/attachments" `
  -Port 22
```

脚本先统计文件数和大小，再使用 `scp -r`上传完整目录，不保存密码。结果位置为：

```text
/home/linzihan/PublicHealthProject/Clean_Gov/data/attachments/gov_files
```

`gov_files` 是服务器上的固定物理附件根目录。其下第一层直接是机构目录，
不要再额外套一层 `attachments` 或本地项目名称。

## 4. 批量dry-run

默认候选格式：

```text
pdf doc docx ppt pptx xls xlsx txt rtf
```

递归检查目录，不调用接口：

```bash
python -m src.cli batch-submit \
  --recursive \
  --interval 2 \
  --batch-name type_test \
  --dry-run
```

未提供 `--input-dir` 时，默认读取项目根目录
`data/attachments/gov_files`；未提供 `--output-layout` 时，默认使用
`mirror`。

`dry-run` 也会执行真实内容预检，但不会生成 Base64、不会调用接口。预检不再只
看扩展名，当前会检查：

- HTML 错误页冒充 PDF、DOCX、XLSX 等文档；
- 0 字节文件以及只有空白、BOM、NUL 的 TXT；
- PDF 文件头、`%%EOF` 截断标记和明确的加密字典；
- DOCX、XLSX、PPTX 的 ZIP 结构、必要组件及 CRC；
- DOC、XLS、PPT 的 OLE 文件头；
- RTF 文件头；
- 配置的文件大小上限。

预检失败的任务状态为 `preflight_failed`，会写入失败清单，不会调用解析接口。
旧版 OLE Office 文件保持兼容提交，但任务会记录
`preflight_needs_manual_review=true` 和兼容性警告。

限制文件和格式：

```bash
python -m src.cli batch-submit \
  --input-dir ./data/attachments/gov_files \
  --recursive \
  --extensions pdf,doc,docx,pptx,xlsx \
  --max-files 5 \
  --dry-run
```

## 5. 正式批量提交

默认镜像模式：

```bash
python -m src.cli batch-submit \
  --recursive \
  --interval 5 \
  --max-in-flight 10 \
  --batch-name gov_files
```

批量提交当前是串行的，但不会等待前一个文件解析完成：接口接收一个任务后，程序按 `--interval`等待，再提交下一个任务。各任务的最终结果由回调服务独立接收。单文件失败不会终止整个批次。

批量器默认对每个 batch 独立启用 10 个在途槽位。在途状态包括
`submitting`、`waiting_callback` 和 `submit_unknown`。槽位通过 SQLite
短事务原子获取，同一 batch 不会超卖；不同 batch 的业务额度互不占用。
`service_max_in_flight` 是按 `parser_pool_id` 汇总的可选服务级上限，默认
为 `null`，不参与限流。

最保守的“完成一个再提交一个”模式：

```bash
python -m src.cli batch-submit \
  --recursive \
  --interval 5 \
  --max-in-flight 1 \
  --slot-poll-interval 5 \
  --batch-name stable_serial \
  --output-layout mirror
```

可选参数：

- `--callback-url`：优先使用指定回调地址。
- `--input-dir`：物理附件根目录；默认 `项目根/data/attachments/gov_files`。
- `--recursive`：递归扫描子目录。
- `--interval`：提交间隔，默认2秒。
- `--max-in-flight`：当前 batch 的最大在途任务数，默认10；设为1最保守。
- `--slot-poll-interval`：队列满时检查回调名额的间隔，默认5秒。
- `--max-files`：限制本次最多扫描提交的文件数。
- `--extensions`：逗号或空格分隔的扩展名。
- `--batch-name`：可读批次名称。
- `--force`：即使相同SHA256已有成功记录也重新提交。
- `--output-layout request|readable|mirror`：输出布局，默认 `mirror`。
- `--dry-run`：只生成扫描和批次记录。

## 6. 查看批次状态

提交命令会输出 `batch_id`。查看整个批次：

```bash
python -m src.cli batch-status --batch-id <batch_id>
```

筛选状态：

```bash
python -m src.cli batch-status --batch-id <batch_id> --status callback_failed
```

筛选格式：

```bash
python -m src.cli batch-status --batch-id <batch_id> --extension pptx
```

超过配置的 `callback_timeout_minutes`仍未回调时，`batch-status`会将任务标记为 `callback_timeout`；迟到的有效回调仍可更新最终状态。

`submit_unknown` 同样占用当前 batch 的在途名额。超过保守配置
`submit_unknown_timeout_minutes` 后会转成 `submit_unknown_timeout` 并释放槽位，
但绝不会自动重新提交；任务保留 RequestId、源文件指纹及人工核对标志。
崩溃遗留的 `submitting` 超过 `stale_submitting_minutes` 后先转成
`submit_unknown`，同样禁止自动重提。当前 Query 响应没有足够明确的状态
映射契约，仍需按 RequestId 人工核对。
默认回调超时为 120 分钟；如果超大文档实际会运行更久，应继续调高
`callback_timeout_minutes`，不要用缩短超时的方式提高吞吐。

SQLite 与 task JSON 的只读一致性检查：

```bash
python -m src.cli check-state --batch-id <batch_id>
```

仅修复“SQLite 仍为 ACTIVE、task JSON 已明确终态”的安全不一致：

```bash
python -m src.cli check-state --batch-id <batch_id> --repair-active-index
```

DOCX 文件名风险只读审计及显式刷新派生报表：

```bash
python -m src.cli audit-docx --batch-id <batch_id> --filename-limit 64
python -m src.cli rebuild-reports --batch-id <batch_id> --manifest
```

回调热路径只更新原始回调、正文、task JSON、SQLite 与 dirty 标记，不会逐次
扫描整个 batch；需要即时查看派生 summary 时执行 `batch-status` 或
`rebuild-reports`。

## 7. 重建 SQLite 索引与中断恢复

SQLite 是可删除重建的运行索引，task JSON 始终是持久化事实。上传新代码并
停止旧提交进程后，可执行：

```bash
python -m src.cli rebuild-index
```

根据一个已停止 batch 构建真正从未处理过的 remaining 硬链接目录：

```bash
python scripts/build_remaining_from_batch.py \
  --input-dir ./data/attachments/gov_files \
  --batch-dir ./data/batches/<旧batch_id> \
  --output-dir ./data/remaining/<旧batch_id> \
  --link-mode hardlink
```

脚本不会复制大文件。硬链接失败会写入 `link_failed.jsonl`；同一路径但大小或
mtime 已改变的文件写入 `changed_source.jsonl`，不会静默归入 remaining。

## 8. 输出目录

原始审计层始终保留：

```text
data/requests/<完整RequestId>/
├── submission.json
├── callback_response.json
├── callback_state.json
├── events/
├── raw_content.md
├── content.md
└── normalization.json
```

`raw_content.md`是接口原始Markdown；`content.md`只做换行、BOM、NUL、行尾空格和连续空行等保守规范化。

默认镜像模式按物理附件相对路径生成：

```text
data/documents/机构/栏目/子栏目（如有）/文章/附件文件.pdf/
├── request_id.txt
├── metadata.json
├── raw.md
├── content.md
├── callback_response.json
└── quality.json
```

例如：

```text
data/attachments/gov_files/国家卫健委/政策法规/通知/文章标题/文件A.pdf
```

对应：

```text
data/documents/国家卫健委/政策法规/通知/文章标题/文件A.pdf/
```

镜像目录保留完整附件名和扩展名，但不追加 RequestId。重新成功解析同一个源附件时，
该镜像目录更新为最新成功结果；每一次提交和回调的完整历史仍保留在
`data/requests/<RequestId>`。失败或空回调不会覆盖镜像目录中已有的成功正文。

如果同一文章目录内同时存在 `文件A.pdf` 和 `文件A.docx`，它们会分别写入
`文件A.pdf/` 和 `文件A.docx/`，不会发生镜像覆盖。

批次文件：

```text
data/batches/<batch_id>/batch.json
data/batches/<batch_id>/tasks/<RequestId>.json
data/batches/<batch_id>/scan_issues.json
data/batch_logs/batch_<batch_id>.log
data/batch_logs/batch_<batch_id>_success.txt
data/batch_logs/batch_<batch_id>_waiting.txt
data/batch_logs/batch_<batch_id>_failed.txt
data/batch_logs/batch_<batch_id>_unsupported.txt
data/batch_logs/batch_<batch_id>_duplicates.txt
data/batch_logs/batch_<batch_id>_summary.json
data/manifest.jsonl
```

其中 `success.txt` 只包含已经收到成功回调的文件；已提交但尚未回调的文件单独
写入 `waiting.txt`。扫描遇到符号链接或访问错误时不会跟随或静默忽略，而会将
逐项证据写入 `scan_issues.json`。

相同 SHA-256 已有成功结果时，当前源路径仍会生成
`duplicate_reference.json` 和正文副本；在 readable/mirror 布局中还会生成
对应的 `content.md`、`metadata.json` 与质量报告，避免重复文件看起来像解析
遗漏。

## 9. 原单文件命令

继续支持：

```bash
python -m src.cli submit --file ./input/test.pdf
python -m src.cli query --request-id '<RequestId>'
python -m src.cli serve-callback
```

## 10. 下载结果

在Windows PowerShell执行。

单个RequestId：

```powershell
scp -r linzihan@服务器地址:/home/linzihan/PublicHealthProject/Clean_Gov/data/requests/<RequestId> D:\解析结果\requests\
```

整个原始审计层：

```powershell
scp -r linzihan@服务器地址:/home/linzihan/PublicHealthProject/Clean_Gov/data/requests D:\解析结果\
```

整个镜像层：

```powershell
scp -r linzihan@服务器地址:/home/linzihan/PublicHealthProject/Clean_Gov/data/documents D:\解析结果\
```

指定批次的任务和报告可下载：

```powershell
scp -r linzihan@服务器地址:/home/linzihan/PublicHealthProject/Clean_Gov/data/batches/<batch_id> D:\解析结果\batches\
scp linzihan@服务器地址:/home/linzihan/PublicHealthProject/Clean_Gov/data/batch_logs/batch_<batch_id>_* D:\解析结果\batch_logs\
```

## 11. 安全和当前边界

- 日志和JSON不得保存AccessKeySecret、完整回调token、签名头或文件Base64。
- 提交发生ReadTimeout时状态记为 `submit_unknown`，不会自动重复POST。
- 提交阶段的 ConnectTimeout、连接中断、ReadTimeout 和不确定 HTTP 5xx 不盲目
  重试，避免服务端已收件时生成重复任务；明确未送达的本地错误记为提交失败。
- 相同SHA256已有 `callback_success`时默认跳过；`--force`可重新提交并保留新RequestId历史。
- 回调采用单向状态策略：成功正文可以被后续成功回调更新，但不会被迟到的失败
  或空回调覆盖；所有回调仍保存在 `events/callback_NNNN.json`。
- 非安全 RequestId 使用“可读前缀 + 原值哈希”作为目录键，避免不同原值清洗
  后落到同一目录。
- task JSON 是持久化事实，SQLite 是可由 JSON 幂等重建的运行索引；当前未加入
  自动 `retry-failed` 和 systemd 安装脚本。
- Base64 改为分块读取，避免同时在内存中持有完整原文件和编码副本；最终请求
  JSON 仍需持有约为原文件 4/3 大小的 Base64 字符串，因此大文件仍应保守提交。

停止回调服务：在运行窗口按 `Ctrl+C`。
