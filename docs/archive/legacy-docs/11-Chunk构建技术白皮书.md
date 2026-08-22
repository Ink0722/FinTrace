# FinTrace Chunk 构建技术白皮书

## 1. 建设目标

统一文本 Document 解决了公告与研报摘要的字段差异，但整篇 Document 通常仍然过长，不能直接用于语义检索。Chunk 构建的任务是把 Document 转换为大小适中、语义尽量完整、可以稳定追溯的检索单元。

本阶段坚持四个原则：

1. 优先保留自然段，不为了凑固定长度随意截断句子；
2. 保留明确的章节语义，但不猜测原文没有提供的标题；
3. 每个 Chunk 都能精确回到原 Document；
4. 切分结果可复现、可验收，并能在人工标注和向量化前冻结版本。

本阶段只负责文本切分，不生成 Embedding，不写入向量数据库，也不修改源 Document。

---

## 2. 输入与输出

### 2.1 输入

唯一输入为：

```text
data/processed/documents/documents.jsonl
```

当前语料包含 62,400 篇 Document，其中公告 7,278 篇、研报摘要 55,122 篇。Chunk 构建器只读取 `document_id`、`document_type` 和 `text`；公司、日期、标题、发布方等元数据仍由 Document 保存。

### 2.2 输出

```text
data/processed/documents/chunks.jsonl
data/processed/documents/chunk_quality.json
data/processed/documents/chunk_manifest.json
```

上述三个文件保留为 V1 对照。改进后的 V2 独立保存，不覆盖 V1：

```text
data/processed/documents/chunks_v2.jsonl
data/processed/documents/chunk_quality_v2.json
data/processed/documents/chunk_manifest_v2.json
```

三类文件职责不同：

| 文件 | 用途 |
|---|---|
| `chunks.jsonl` | 提供给后续 Embedding、检索和人工标注的正式 Chunk 语料 |
| `chunk_quality.json` | 记录长度分布、异常数量和覆盖校验结果 |
| `chunk_manifest.json` | 冻结版本、参数、Schema、文件哈希和记录数量 |

---

## 3. Chunk 数据结构

每行是一个独立 JSON 对象，只保留七个字段：

```json
{
  "chunk_version": "chunks-v2",
  "chunk_id": "ANN-259496024-C0001",
  "document_id": "ANN-259496024",
  "chunk_index": 1,
  "section_title": "一、整改情况 / （一）收入确认",
  "char_start": 0,
  "text": "一、整改情况……"
}
```

字段含义如下：

| 字段 | 含义 |
|---|---|
| `chunk_version` | Chunk 所属语料版本，V2 固定为 `chunks-v2` |
| `chunk_id` | 当前版本内的稳定标识，格式为 `{document_id}-C{四位序号}` |
| `document_id` | 所属 Document ID，用于关联完整元数据 |
| `chunk_index` | 在本 Document 内从 1 开始的顺序 |
| `section_title` | 原文明确章节标题；多级标题使用 ` / ` 连接；无法可靠判断时为 `null` |
| `char_start` | Chunk 首字符在 Document `text` 中的零基偏移量 |
| `text` | 用于向量化和检索的原文片段 |

没有把公司代码、日期、发布方等字段重复写进 Chunk，是为了减少 16 万余条记录中的冗余。`chunk_version` 是唯一保留的语料级字段，因为不同版本可能出现相同 `chunk_id` 却对应不同文本。检索阶段先按 `document_id` 关联 Document 元数据，再做过滤、排序和证据展示。

`text` 始终是 Document 原文的连续切片。因此可以用下面的关系核验来源：

```python
document_text[char_start:char_start + len(chunk_text)] == chunk_text
```

---

## 4. 切分策略

### 4.1 长度参数

默认参数为：

| 参数 | 字符数 | 作用 |
|---|---:|---|
| `min_chars` | 200 | 希望短段合并后达到的参考下限 |
| `target_chars` | 600 | 组合段落和拆分超长段落时的目标长度 |
| `soft_max_chars` | 900 | 普通段落组合时尽量不超过的上限 |
| `hard_max_chars` | 1200 | 任何 Chunk 都不得超过的硬上限 |

这些长度按 Python 字符数计算，不等同于模型 Token 数。`min_chars` 是合并偏好，不是删除或判废标准。例如“风险提示：原材料价格波动”虽然不足 200 字，却是完整且有检索价值的事实单元，应当保留。

### 4.2 自然段识别

构建器先按空行识别自然段。只要自然段不超过 1,200 字，就将其作为不可再拆的原子单元。相邻短段只有在同一章节内、合并后不超过长度边界时才会组合。

该策略避免把同一句解释、同一组财务判断或同一条风险提示分散到不同 Chunk。

### 4.3 章节标题识别

系统仅识别格式明确的标题，包括：

- `一、整改情况`、`（一）收入确认`；
- `第一章`、`第二节`；
- 有清晰编号分隔符的数字标题；
- 研报中带冒号的“事件”“投资要点”“投资建议”“风险提示”等固定栏目。

父子标题按层级组成路径，例如：

```text
一、整改情况 / （一）收入确认
```

普通自然段不会生成虚构标题；它继承最近的可靠标题，没有标题时写 `null`。纯数字、小数、日期以及以句号或分号结束的处罚清单项不会被当成章节标题。

V2 对研报栏目增加词边界约束。“事件”可以在段首、空白或标点后作为栏目名，但不会在“相关事件”“事件描述”等词组内部触发切分，避免产生只有几个字的伪章节。

连续标题可能出现“父标题后立刻跟子标题”的情况。父标题本身没有正文时，不单独生成只有标题的 Chunk，而是和下一段正文一起保存，并使用更完整的层级路径。首个正式章节之前不足 200 字的短前缀也会并入首个章节，避免生成“要点”“摘要”等无检索价值的孤立片段。

### 4.4 V2 结构碎片处理

V2 在章节分区后对每个候选区域执行一次结构碎片检查。判断流程如下：

```text
取出候选区域原文
-> 临时移除开头的栏目标题和冒号
-> 仅为判定而忽略空白、问号、序号、标点、括号、HTML 箭头和省略号
-> 检查是否还存在具有事实意义的文字或数字
```

这里的“移除”和“忽略”只发生在分类判断中，不会修改或删除 `chunk.text` 中的原文。

如果检查后只剩结构符号，例如：

```text
主要观点：?
投资建议：1）
风险提示：
```

则按以下规则处理：

1. 中间位置的结构碎片优先与后一个有效区域合并；
2. 文档末尾没有后续区域时，与前一个有效区域合并；
3. 合并只在同一 Document 内进行，不跨文档；
4. 合并后的 `text` 仍是 Document 原文中的连续切片，`char_start` 指向最早字符；
5. 合并结果仍受 1,200 字硬上限约束，超过上限时重新按标点切分。

金额、比例、人物、职务、评级和风险结论属于有效事实，即使不足 30 字也不判为结构碎片。例如：

```text
二、涉诉金额
本金25亿元及利息等。

3、整改负责人
副总经理：牛丹丹

风险提示：行业竞争加剧。
```

因此，V2 不设置统一的最小字数硬门槛。短块是否保留取决于其能否独立回答一个问题，而不是是否达到 `min_chars=200`。本轮规则调整后，不超过 10 字的 Chunk 已从90个降为0；剩余最短 Chunk 为12字，抽样内容均为简短但完整的风险或责任事实。

### 4.5 超长自然段处理

只有单个自然段超过 `hard_max_chars` 时才启动段内切分，优先级为：

1. 在目标长度附近寻找句号、问号、感叹号或分号；
2. 找不到时寻找逗号、顿号或冒号；
3. 仍找不到时在目标字符位置强制切分。

边界选择会在可接受范围内寻找最接近 600 字的位置。当前全量语料只有 13 次强制切分，其余超长段落均找到了自然标点边界。

### 4.6 不使用重叠窗口

当前 Chunk 之间的重叠长度为 0。主要原因是：

- 自然段和章节已经提供语义边界；
- 重叠会重复召回同一证据，干扰后续人工标注和回答引用；
- `char_start` 与文本覆盖校验在无重叠情况下更清晰。

后续若检索实验显示边界信息丢失，应优先在查询侧加入相邻 Chunk 扩展，而不是立刻复制所有语料。

---

## 5. 构建工作流

代码位于 `data_pipeline/documents/`：

```text
cli.py
  -> chunk_builder.build_chunks()
     -> chunker.chunk_text()
        -> 标题识别与章节分区
        -> 栏目词边界检查
        -> 结构碎片识别与相邻合并
        -> 自然段识别
        -> 超长段落切分
        -> 同章节短段组合
     -> Schema、偏移和覆盖校验
     -> 原子替换 chunks_v2.jsonl
     -> 写入质量报告与 Manifest
```

构建过程采用流式读取和逐行写入，不需要把 62,400 篇 Document 或全部 Chunk 同时放入内存。正式输出先写入临时文件；只有全部文档通过校验后才替换旧文件。中途报错时会删除临时文件，保留上一次可用结果。

核心校验包括：

- `document_id` 和 `chunk_id` 不重复；
- Chunk 非空且字段集合固定；
- `char_start` 能精确定位原文；
- 同一 Document 内 Chunk 不重叠；
- 去除空白后，全部 Chunk 能覆盖完整 Document；
- 任何 Chunk 不超过 1,200 字。

---

## 6. 全量构建结果

使用 `chunks-v2` 和默认参数对当前语料执行后，结果如下：

| 指标 | 结果 |
|---|---:|
| 输入 Document | 62,400 |
| 输出 Chunk | 160,365 |
| 公告 Chunk | 31,330 |
| 研报摘要 Chunk | 129,035 |
| 平均长度 | 480.2 字 |
| 中位长度 | 547 字 |
| P95 长度 | 1,094 字 |
| 最大长度 | 1,200 字 |
| 最小长度 | 12 字 |
| 带章节标题 Chunk | 103,784 |
| 合并结构碎片 | 7,479 个区域 |
| 不超过 10 字 | 0 |
| 不超过 30 字 | 10,238 |
| 超过硬上限 | 0 |
| 空 Chunk | 0 |
| 重复 Chunk ID | 0 |
| 文本覆盖失败 | 0 |

共有 57,507 个 Chunk 少于 200 字。该数字不能直接解释为质量问题，因为研报中的“风险提示”、公告中的“涉诉金额”和“整改责任人”等内容本身就很短。V2 已消除所有不超过 10 字的块；现存最短块为12字，内容包括“风险提示：行业竞争加剧”等完整事实。当前策略选择保留这些证据，不跨章节强行拼接。

V1 与 V2 的主要差异如下：

| 指标 | V1 | V2 |
|---|---:|---:|
| 总 Chunk | 163,741 | 160,365 |
| 中位长度 | 494 | 547 |
| 少于 200 字 | 60,486 | 57,507 |
| 少于 20 字 | 521 | 422 |
| 不超过 10 字 | 90 | 0 |
| 最短长度 | 6 | 12 |

V2 的目标不是让所有 Chunk 都达到200字，而是消除没有独立语义的结构碎片，同时保留短而完整的金融事实。

质量报告和 Manifest 分别保存在：

```text
data/processed/documents/chunk_quality_v2.json
data/processed/documents/chunk_manifest_v2.json
```

---

## 7. 执行与复现

默认构建命令：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.cli build-chunks `
  --data-dir data `
  --version chunks-v2 `
  --output data\processed\documents\chunks_v2.jsonl `
  --report data\processed\documents\chunk_quality_v2.json `
  --manifest data\processed\documents\chunk_manifest_v2.json
```

需要进行实验时可以显式覆盖参数：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.cli build-chunks `
  --data-dir data `
  --version chunks-exp-01 `
  --target-chars 600 `
  --min-chars 200 `
  --soft-max-chars 900 `
  --hard-max-chars 1200
```

测试命令：

```powershell
F:\conda_envs\FinTrace\python.exe -m pytest tests\test_text_chunk_builder.py -q
```

测试覆盖标题层级、普通段落、研报栏目、栏目词边界、结构碎片合并、完整短事实保留、数字误判、短前缀合并、短段组合、超长段落拆分、版本字段、Schema 约束和失败时保留旧输出。

---

## 8. 版本冻结与下游使用

`chunk_id` 由 Document ID 和顺序生成。只要源文本、标题规则或长度参数发生变化，后续 Chunk 顺序就可能变化。因此执行顺序必须是：

```text
冻结 documents.jsonl
-> 生成并验收 chunks.jsonl
-> 冻结 chunk_manifest.json
-> 人工标注 required_chunk_ids
-> 生成 Embedding 和向量索引
-> 开展检索与 Agent 评测
```

Manifest 中同时保存输入和输出 SHA-256。评测、标注和向量索引必须记录所使用的 `chunk_version` 与哈希；哈希不一致时，不得混用旧的 Chunk ID、Embedding 或人工金标。

### 8.1 Embedding 输入内容

Embedding 构建已经由 `data_pipeline.documents.build_index` 实现。本节同时作为输入规范，避免直接把 `chunk.text` 或全部元数据无选择地送入模型。

实际生成向量时，先用 `document_id` 关联 `chunks_v2.jsonl` 与 `documents.jsonl`，再构造一个仅用于向量化的 `embedding_text`。固定字段顺序如下：

```text
文档类型：公告
证券代码：603439.SH
标题：三力制药:关于公司最近五年被证券监管部门和交易所处罚或采取监管措施情况的公告
发布日期：2026-05-26
标签：违纪违规；个股其他公告
章节：一、公司最近五年被证券监管部门和交易所处罚的情况
正文：
一、公司最近五年被证券监管部门和交易所处罚的情况
公司最近五年内不存在被证券监管部门和交易所处罚的情况。
```

研报摘要存在 `publisher` 时，在发布日期后增加一行：

```text
发布机构：中信证券
```

各字段的处理规则如下：

| 内容 | 来源 | 是否送入 Embedding | 规则 |
|---|---|---|---|
| 文档类型 | `document.document_type` | 是 | 转换为“公告”或“研报摘要” |
| 证券代码 | `document.company_id` | 是 | 使用现有标准代码，不从正文猜测公司名称 |
| 标题 | `document.title` | 是 | 提供整个 Chunk 的主题信息 |
| 发布日期 | `document.published_date` | 是 | 保留 `YYYY-MM-DD`，用于区分不同时期的相似内容 |
| 发布机构 | `document.publisher` | 条件加入 | 仅研报且字段非空时加入 |
| 标签 | `document.tags` | 条件加入 | 删除空值、去重后使用中文分号连接；无标签时整行省略 |
| 章节 | `chunk.section_title` | 条件加入 | 非空时加入；不生成或猜测标题 |
| 正文 | `chunk.text` | 是 | 核心语义内容，保持原文不改写 |

除 `正文` 外，其他字段为空时应省略整行，不写“未知”“无”或 `null`。字段名称使用固定中文前缀，使相同信息在所有记录中具有一致结构。

当前 Document 没有独立的公司名称字段，因此第一版只加入 `company_id`。后续若建立经过校验的“证券代码—公司名称”主数据表，可以把证券代码行扩展为：

```text
公司：贵州三力制药股份有限公司（603439.SH）
```

在主数据表建立之前，不从标题或正文临时抽取公司名称，避免错误实体进入所有相关向量。

### 8.2 不送入 Embedding 的内容

以下字段用于管理和追溯，不参与语义向量计算：

```text
chunk_id
document_id
chunk_index
char_start
source_ref
chunk_version
文件哈希
```

把这些标识符写入文本不会增强语义，反而可能给向量增加无意义噪声。它们应作为向量记录的 metadata 或外部关联字段保存。

### 8.3 向量记录与原文证据

建议写入向量索引前形成下面的逻辑记录：

```json
{
  "chunk_id": "ANN-259499590-C0002",
  "document_id": "ANN-259499590",
  "embedding_text": "文档类型：公告\n证券代码：603439.SH\n标题：……\n发布日期：2026-05-26\n标签：……\n章节：……\n正文：\n……",
  "metadata": {
    "document_type": "announcement",
    "company_id": "603439.SH",
    "published_date": "2026-05-26",
    "publisher": null,
    "chunk_index": 2,
    "char_start": 492
  }
}
```

其中只有 `embedding_text` 发送给 Embedding 模型。`chunk_id` 用作向量索引的稳定外部 ID，其余 metadata 用于过滤、关联和追溯。FAISS 只保存向量与内部位置映射时，完整 metadata 应保存在配套 SQLite 表中。

检索命中后，交给 Agent 和最终用户的证据必须是原始 `chunk.text`，同时展示 Document 标题、日期和来源；不能把带有人工字段前缀的 `embedding_text` 当作原文引用。这样可以形成“回答 -> Chunk -> Document -> 原始数据”的完整证据追溯链。

### 8.4 查询侧 Embedding

用户查询不套用上述文档模板。查询侧只对经过上下文解析后的检索问题生成向量，例如：

```text
贵州三力近五年是否受到证券监管部门处罚
```

股票代码、日期范围和文档类型等明确约束应同时用于 metadata 过滤，不应只依赖向量相似度。文档向量负责语义召回，结构化条件负责缩小检索范围，两者共同组成最终检索条件。

### 8.5 构建命令与产物

成本估算不会调用模型：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index --estimate-only
```

本地生成 Batch 请求文件（不调用 API）：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index prepare
```

提交与完成构建：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index submit
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index status
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index collect
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index finalize
```

若希望先验证单个分片，可指定提交：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index submit --shard-id shard-0000
```

`--shard-id` 可重复使用；不传时提交全部尚未获得 Batch 任务 ID 的分片。已经提交的分片会依据本地状态自动跳过。

构建器固定校验 `documents.jsonl`、`chunks_v2.jsonl` 和 `chunk_manifest_v2.json` 的 SHA-256，使用 Qwen `text-embedding-v4` 的 OpenAI 兼容 Batch File API 生成 1024 维稠密向量。每个 Batch 请求最多包含 10 条 Embedding 文本，默认每 20,000 个 Chunk 形成一个独立任务。在线查询使用同一模型、维度和兼容接口同步生成问题向量，避免文档侧和查询侧使用不同接口语义。

本地状态保存在 `data/indexes/document_search/.batch_build/state.json`。下载结果不依赖返回行顺序，而是使用 `custom_id + data.index` 对应到 `vector_row`；缺失、重复、错误维度、非有限值或尚未重试成功的请求都会阻止 `finalize`。完整向量归一化后写入 `embeddings.npy`，FAISS 使用精确的 `IndexFlatIP`，因此内积等价于余弦相似度。正式完成后生成：

对于数量极少且已经人工审查的请求级失败，可以显式执行 `finalize --allow-partial`。该模式只接受错误文件能够完整解释的缺失行，将成功向量按原始行序压缩为连续索引；SQLite 继续保留全部 Chunk，因此失败 Chunk 仍可通过 BM25 召回。`manifest.json` 记录 `vector_count`、`excluded_vector_count`、`vector_coverage` 和 `partial_index`，`embedding_failures.jsonl` 记录每个被排除 Chunk 的文档、公司、原始行号及错误原因。未知缺失或数据异常仍然失败，不能使用零向量或伪造向量补位。

```text
data/indexes/document_search/
  fintrace_kb.sqlite
  embeddings.npy
  vector.faiss
  vector_ids.json
  build_progress.json
  batch_jobs.json
  embedding_failures.jsonl
  manifest.json
```

`manifest.json` 同时保存估算 Token 和 API 返回的实际 Token。实际计费必须以后者为准。
