# FinTrace 统一文本 Document 构建技术白皮书

## 1. 文档定位

本文说明 FinTrace 如何把公告正文和研报摘要整理为统一的文本 Document 语料。它重点回答四个问题：

1. 为什么公告 TXT 和研报 JSONL 不能直接混合检索；
2. 不同来源的数据如何映射为同一种 Document；
3. 如何清理文本，同时避免误删金融事实；
4. 如何证明最终产物完整、稳定并且可以复现。

本文描述的是**文本标准化层**，不包含 Chunk 切分、Embedding、FAISS 建库和在线检索。原始竞赛数据如何转换为七个 JSONL、公告正文如何下载和修复，见 [数据集构建技术白皮书](08-数据集构建技术白皮书.md)。

---

## 2. 为什么需要统一 Document

公告和研报在原始数据中差异很大：

| 对比项 | 公告 | 研报 |
|---|---|---|
| 元数据位置 | `announcements.jsonl` | `research_reports.jsonl` |
| 文本位置 | 独立 TXT 文件 | JSONL 的 `abstract` 字段 |
| 公司代码 | 已包含交易所后缀 | 证券代码与交易所字段分开 |
| 标签来源 | 公告分类 | 研报类型、评级及评级变化 |
| 文本性质 | 公告正文 | 研报摘要，不是全文 |

如果下游直接分别读取两种数据，每一个检索器都要重复处理字段差异、路径解析、日期格式和异常记录。统一 Document 层把这些差异收敛在离线流程中，使下游只需要理解一套稳定字段。

统一并不等于抹平差异。`document_type` 会始终保留文档类型，研报特有的发布机构也保留为 `publisher`。这样既降低下游复杂度，又不会丢失业务语义。

---

## 3. 建设目标与边界

### 3.1 建设目标

统一 Document 需要满足以下要求：

- 每条记录有稳定且唯一的 `document_id`；
- 每条记录明确关联一个 A 股公司代码；
- 标题、发布日期和正文不能为空；
- 文本采用统一的 UTF-8 编码和换行格式；
- 公告和研报可以通过 `document_type` 区分；
- 来源可以定位到本地公告文件或研报记录；
- 构建失败不能破坏上一版完整产物；
- 构建结果必须附带可审计的质量报告。

### 3.2 当前边界

本阶段只处理两类文本：

```text
announcement
research_report
```

财务报表和股东快照是结构化事实，不应为了“统一”而强行转换成长文本。它们后续分别由财务工具和股权工具读取。

本阶段也不执行以下工作：

- 不切分 Chunk；
- 不调用 Embedding 模型；
- 不构建 SQLite 或 FAISS；
- 不用 LLM 改写、补全或总结原文；
- 不把研报摘要伪装成完整研报。

---

## 4. 总体流程

```text
data/jsonl/announcements.jsonl
        +
data/documents/announcements/*.txt
        |
        |  字段校验、正文读取、保守清理
        v
data_pipeline.text.document_builder
        ^
        |  字段校验、代码标准化、摘要读取
        |
data/jsonl/research_reports.jsonl
        |
        v
data/text_corpus/documents.jsonl
data/text_corpus/document_quality.json
```

代码位于：

```text
data_pipeline/text/
├── cli.py
├── cleaner.py
└── document_builder.py
```

`cli.py` 提供命令行入口，`cleaner.py` 负责保守文本清理，`document_builder.py` 负责字段映射、校验、流式写入和质量统计。

---

## 5. Document 数据结构

### 5.1 公告 Document

```json
{
  "document_id": "ANN-259499590",
  "document_type": "announcement",
  "company_id": "603439.SH",
  "title": "关于公司最近五年监管措施情况的公告",
  "published_date": "2026-05-26",
  "tags": ["违纪违规", "个股其他公告"],
  "text": "证券代码：603439……",
  "source_ref": "data/documents/announcements/259499590.txt"
}
```

### 5.2 研报 Document

```json
{
  "document_id": "RR-5971493",
  "document_type": "research_report",
  "company_id": "601033.SH",
  "title": "2024年报点评",
  "published_date": "2025-04-01",
  "publisher": "东吴证券",
  "tags": ["业绩点评", "买入", "维持"],
  "text": "公司实现营业收入增长……",
  "source_ref": "data/jsonl/research_reports.jsonl#5971493"
}
```

### 5.3 字段含义

| 字段 | 含义 | 约束 |
|---|---|---|
| `document_id` | Document 全局标识 | 公告使用 `ANN-`，研报使用 `RR-` |
| `document_type` | 文档类型 | `announcement` 或 `research_report` |
| `company_id` | A 股证券代码 | `000001.SZ`、`600000.SH` 或 `920088.BJ` |
| `title` | 原始标题 | 必填，不由 LLM 生成 |
| `published_date` | 发布日期 | ISO `YYYY-MM-DD` |
| `publisher` | 研报发布机构 | 仅研报包含 |
| `tags` | 来源提供的分类信息 | 去空、去重，允许空数组 |
| `text` | 可供后续检索的文本 | 公告为正文，研报为摘要 |
| `source_ref` | 当前来源定位 | 本地 TXT 或 JSONL 记录 ID |

---

## 6. 原始字段映射

### 6.1 公告映射

| Document 字段 | 公告原始字段或来源 |
|---|---|
| `document_id` | `ANN-` + `id` |
| `document_type` | 固定为 `announcement` |
| `company_id` | `s_info_windcode` |
| `title` | `n_info_title` |
| `published_date` | `ann_dt` |
| `tags` | `category_names` |
| `text` | 读取 `document_path` 指向的 TXT |
| `source_ref` | `document_path` |

只有 `download_status == "success"` 的公告可以进入统一语料。已经确认无文本层的公告保留在上游清单中，但不会制造空 Document。

### 6.2 研报映射

| Document 字段 | 研报原始字段或来源 |
|---|---|
| `document_id` | `RR-` + `report_id` |
| `document_type` | 固定为 `research_report` |
| `company_id` | `sec_code` + `exchange_code` 映射后缀 |
| `title` | `title` |
| `published_date` | `publish_date` |
| `publisher` | `org_name` |
| `tags` | `report_sub_type`、`rating_org`、`rating_change` 和首次覆盖标记 |
| `text` | `abstract` |
| `source_ref` | JSONL 路径 + `report_id` |

交易所映射采用确定性规则：

```text
XSHG -> .SH
XSHE -> .SZ
XBEI -> .BJ
```

不支持的交易所和不是六位数字的证券代码不会被猜测修复，而是记录到质量报告后跳过。

---

## 7. 文本清理策略

### 7.1 基础清理

`clean_text()` 只做不会改变金融含义的操作：

1. 删除 UTF-8 BOM；
2. 把 Windows 和旧式换行统一为 `\n`；
3. 删除不可见控制字符；
4. 合并行内连续空格；
5. 把三个及以上连续空行压缩为两个换行；
6. 清理行首、行尾和全文首尾空白。

流程不会改写数字、公司名称、财务术语和原始结论，也不会调用 LLM“润色”文本。

### 7.2 标签清理

标签只执行两项操作：

- 删除空标签；
- 在保持原顺序的前提下去重。

空标签是否需要自动推断仍待商榷，因此当前允许 `tags: []`，不会用模型生成看似合理但来源不明的标签。

### 7.3 公告前导标题清理

原始公告 TXT 普遍包含两遍相同标题：一遍来自网页头部，一遍来自附件正文。标题同时还独立保存在 Document 的 `title` 字段中。如果原样进入向量模型，标题词会被重复强调。

清理规则如下：

```text
读取并基础清理正文
-> 仅检查正文最前面的完整行
-> 与 Document title 比较
-> 连续相同则删除
-> 遇到第一行不相同立即停止
```

比较时使用 Unicode NFKC 规范化，因此全角冒号与半角冒号可以视为等价；同时忽略空白和标题末尾的句号、问号、感叹号。该规范化只用于比较，不会改写最终文本。

为防止误删，规则设置了三重保护：

- 不在正文中间搜索和删除标题；
- “标题摘要”等近似文本不视为标题；
- 如果删除后正文为空，则放弃本次删除并保留原文。

全量结果显示，7,066 份公告共删除 14,132 行前导标题，即这些公告各删除两行。清理后仍以标题开头的公告数量为 0。

---

## 8. 校验、写入与失败保护

### 8.1 入库前校验

公告必须满足：

- 下载状态成功；
- ID、公司代码、标题和日期有效；
- `document_path` 存在且可以读取；
- 清理后的正文不为空。

研报必须满足：

- `report_id` 存在；
- 交易所在当前 A 股映射范围内；
- 证券代码为六位数字；
- 标题、日期、发布机构和摘要不为空。

### 8.2 流式处理

构建器逐行读取源 JSONL，并逐条写出 Document，不把 6.24 万条记录和约 200 MB 文本一次性加载进内存。该方式适合后续数据规模继续增长。

### 8.3 ID 唯一性

写出前维护已经出现的 `document_id` 集合。一旦发现重复 ID，构建立即失败，而不是静默覆盖或产生两个无法区分的证据。

这里检查的是 Document ID，不是正文内容。正文完全重复如何处理尚未确定，因此当前主语料保留所有合法来源记录。

### 8.4 原子替换

新语料先写入：

```text
documents.jsonl.tmp
```

只有全部处理成功后才通过 `os.replace()` 替换正式文件。任何解析错误、重复 ID 或磁盘写入异常都会删除临时文件，上一版完整语料仍然可用。

质量报告同样采用临时文件加原子替换，避免生成一半 JSON。

---

## 9. 质量报告与当前结果

质量报告位于：

```text
data/text_corpus/document_quality.json
```

当前全量构建结果如下：

| 指标 | 结果 |
|---|---:|
| 公告输入 | 7,311 |
| 公告输出 | 7,278 |
| 无文本层公告排除 | 33 |
| 研报输入 | 55,214 |
| 研报输出 | 55,122 |
| 无效证券代码排除 | 8 |
| 不支持交易所排除 | 84 |
| 最终 Document | 62,400 |
| 重复 Document ID | 0 |
| 空正文 | 0 |
| 清理前导标题的公告 | 7,066 |
| 删除的前导标题行 | 14,132 |

整体记录保留率为：

```text
62,400 / 62,525 = 99.80%
```

文本长度统计：

| 指标 | 字符数 |
|---|---:|
| 最短 | 101 |
| 中位数 | 1,125 |
| P95 | 2,042 |
| 最长 | 28,990 |
| 平均值 | 1,234.6 |

这些结果说明结构完整性已经满足后续处理要求，但不代表每条文本都具有相同的信息密度。短文本、空标签和正文重复问题将在 Chunk 设计阶段继续讨论。

---

## 10. 运行与复现

在项目根目录执行：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.text.cli build-documents `
  --data-dir data
```

也可以指定输出和报告路径：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.text.cli build-documents `
  --data-dir data `
  --output data\text_corpus\documents.jsonl `
  --report data\text_corpus\document_quality.json
```

运行测试：

```powershell
F:\conda_envs\FinTrace\python.exe -m pytest -q
```

本轮实现完成后，全量测试结果为：

```text
69 passed
```

### 10.1 Windows PowerShell 编码说明

`documents.jsonl` 使用无 BOM UTF-8。旧版 Windows PowerShell 可能按系统 ANSI 编码显示，从而出现“文件看起来乱码、实际字节正确”的情况。预览时需要显式指定编码：

```powershell
Get-Content -Encoding utf8 data\text_corpus\documents.jsonl -TotalCount 1
```

Python 应明确使用：

```python
path.open("r", encoding="utf-8")
```

不应为了适配终端显示而批量转码已经正确的语料。

---

## 11. 下游使用约束

### 11.1 Chunk 必须继承的字段

后续每个 Chunk 至少需要继承：

```text
document_id
document_type
company_id
title
published_date
publisher（如有）
tags
source_ref
```

这样向量召回后才能判断证据属于哪家公司、什么时间、哪种来源。

### 11.2 分类召回

研报占当前语料的 88% 以上。如果混在一起只做统一 Top-K，公告可能被大量研报结果挤出。因此检索层应采用：

```text
公告问题 -> 只检索 announcement
研报观点问题 -> 只检索 research_report
综合问题 -> 两类分别召回后统一重排
```

综合查询的初始预算建议为：

```text
公告 Top 5 + 研报 Top 5 -> 重排后保留 Top 6
```

这是一项后续检索层约束，不属于 Document 构建器职责。

### 11.3 研报摘要的表达边界

研报 Document 的 `text` 只来自 `abstract`。Agent 可以表述：

```text
根据研报摘要，机构认为……
```

但不能表述：

```text
根据完整研报第 8 页……
```

除非后续取得并解析原始研报文件，否则无法恢复完整分析过程、图表和页码。

---

## 12. 当前保留的问题

以下问题经过评估后暂不在本阶段处理：

| 问题 | 当前决定 | 原因 |
|---|---|---|
| 完全重复正文 | 保留全部 Document | 不同 ID 可能代表不同来源，需结合 Chunk 索引策略讨论 |
| 空标签 | 允许空数组 | 自动推断可能引入无来源标签 |
| 逐条质量标记 | 暂不加入 Schema | 避免在规则未确定前膨胀字段 |
| 短文本过滤 | 暂不执行 | 短不等于无效，应结合检索效果评估 |
| 来源字段扩展 | 暂不扩展 | 当前 `source_ref` 已满足 Document 级定位 |
| 研报全文 | 无法从现有数据恢复 | 必须获得新的原始文件或正文数据源 |

这些保留项不影响统一 Document 作为中间层使用。它们应在 Chunk、索引和评测方案确定后，以检索效果为依据处理，而不是仅凭主观规则删除数据。

---

## 13. 结论

FinTrace 统一 Document 层完成了从异构文本来源到稳定语料接口的转换：公告正文和研报摘要拥有统一标识、公司代码、日期、类型、正文和来源引用，同时保留各自必要的业务差异。

当前产物具有以下工程特征：

- 结构确定，不依赖 LLM 猜测字段；
- 文本清理保守，不改写金融事实；
- 失败可见，不制造空证据；
- 流式构建，可适应更大数据量；
- 原子替换，不会因中断损坏正式语料；
- 质量结果可统计、可测试、可复现。

因此，`documents.jsonl` 已经可以作为下一阶段 Chunk 设计和检索实验的唯一文本输入。下一阶段的重点不再是继续扩充 Document 字段，而是确定不同文档类型的切分策略、重复内容处理方式和可量化的检索评测方法。
