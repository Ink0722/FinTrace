# FinTrace

FinTrace 是一个面向 A 股投研场景的证据驱动型 Agentic AI 问答系统骨架。

Last Update on Codex 2026/8/18

当前版本已经具备：

- 项目技术拆解和目录结构；
- Pydantic Schema；
- 四个工具的固定输入输出；
- 财务风险、股权穿透、文档检索和事件时间线工具；
- CSV / SQLite / FAISS / sample 的多数据源回退策略；
- 基于 LangGraph 的 Agent Harness 和组合工具路由；
- Qwen 兼容 OpenAI API 的 LLM 客户端封装；
- Pytest 测试骨架；
- `.env.example` 和本地运行说明。

真实数据未接入时会回退 sample 数据；如果显式配置了 CSV/知识库但目标数据缺失，工具会返回结构化错误，避免用样例数据冒充真实数据。

## 竞赛设计文档

建议按编号阅读：

1. [竞赛要求对齐](docs/00-竞赛要求对齐.md)：三项攻关任务、量化指标、数据缺口和实施路线；
2. [数据与工具总览](docs/01-数据与工具总览.md)：五组数据、四个工具和证据类型的总体关系；
3. [多轮评测方案](docs/02-多轮评测方案.md)：35 个会话、1,410 个 Turn 和 0.5M 长历史压力评测；
4. [股东快照设计](docs/03-股东快照设计.md)：时序持股边、有限穿透和增强图谱要求；
5. [公告索引设计](docs/04-公告索引设计.md)：标题级时间线、正文入口和事件聚类边界；
6. [财务报表设计](docs/05-财务报表设计.md)：期间口径、跨表勾稽、风险规则和 F1 标签；
7. [研报摘要设计](docs/06-研报摘要设计.md)：结构化过滤、混合检索和机构观点证据。
8. [Agent 评测实施方案](docs/07-Agent评测实施方案.md)：准确率边界、人工标注、自纠错、工具基准和财务 F1。
9. [数据集构建技术白皮书](docs/08-数据集构建技术白皮书.md)：原始数据转换、公告正文获取、异常修复和数据资产验收。
10. [统一文本 Document 构建白皮书](docs/09-统一文本Document构建白皮书.md)：字段映射、保守清理、原子写入、质量验收和下游边界。
11. [多轮问题集人工标注指南](docs/10-多轮问题集人工标注指南.md)：Answerability、主体日期、工具和必要 Chunk 的人工标注规则。
12. [Chunk 构建技术白皮书](docs/11-Chunk构建技术白皮书.md)：段落保持、章节继承、超长文本切分、版本冻结和全量质量验收。

## Code Review 导览

在线 Agent 采用「确定性直连 + 有界 ReAct 调查」双模架构（见 `docs/13-Agent决策与证据驱动调查技术白皮书.md`）：

```text
app.cli.main()
→ harness.graph.workflow.run_agent()
→ LangGraph StateGraph
   → load_session           # 会话上下文恢复（指代继承）
   → resolve_request        # Gate A：实体/时间/任务族解析（不选工具）
   → check_pre_answerability# Gate B：unsupported / clarification_required / routeable
      ├─ build_clarification → persist_session → END   # 缺槽追问，不猜
      ├─ build_refusal      → persist_session → END    # 能力边界拒绝
      └─ route_mode         # Gate C：简单直连 / 复杂调查
         ├─ direct：build_direct_action（纯规则，无 LLM）
         └─ investigation：plan_next_action（每轮一个 AgentAction）
            → validate_action（→ repair_action 一次）
            → execute_one_tool → validate_tool_result → merge_evidence
            → review_evidence ─┬─ continue → plan_next_action（有界循环）
                               └─ sufficient/partial/insufficient → generate_answer
   → generate_answer_node()  # 基于证据的回答（answer_status 分级）
   → persist_session → END
→ harness.tracing.jsonl.write_trace()  # routing_mode/planner_actions/gaps/termination 全落盘
```

关键模块：

```text
harness/routing/request_parser.py      # query → ParsedRequest（规则 → 02 LLM 兜底）
harness/routing/entities.py            # 别名索引解析，禁止默认公司
harness/routing/capability_registry.py # 能力注册表（implemented 反映真实代码）
harness/routing/answerability.py       # Gate B 三态判定
harness/routing/direct_gate.py         # Gate C 确定性直连
harness/routing/planner.py             # 每轮一个动作的调查规划（P1 规则型）
harness/routing/action_validator.py    # 动作校验：参数/维度/防篡改 cutoff/防重复
harness/evidence/review.py             # 证据充分性（P1 确定性）
harness/memory/session_store.py        # SQLite 会话持久化
harness/prompts.py                     # Prompt 组装（全局政策 + Skill，带版本头）
harness/skills.py                      # run_skill：结构化输出校验 + trace 记录
```

Prompt 体系（`prompts/`，版本化、按 docs/11 规范）：`01_global_policy` 为所有 LLM 节点共享前缀；`02_request_parser`（解析兜底）、`03_next_action_planner`（ReAct 单动作）、`04_evidence_reviewer`（证据充分性）、`05_action_repair`（动作最小修复）、`06_final_answer`（结构化最终回答）已全部接入，LLM 不可用时逐级降级到规则队列与结构化摘要。旧的 `system.md`/`planner.md` 已退役删除。

FastAPI 模式只是在入口层替换为：

```text
app.api.main.chat()
→ run_agent()
→ AgentState.model_dump()
```

各目录职责：

```text
app/             CLI 和 FastAPI 入口
harness/         Agent 编排、路由、校验、回答、记忆、trace
tools/           四个可独立测试的金融工具
schemas/         Pydantic 数据契约（含 request.py 动作/审查契约）
data_pipeline/   文档、事件、股权、财务和实体别名离线预处理
data/            源数据、标准数据、处理结果和运行索引
prompts/         版本化 Prompt Skills
tests/           单元测试和工作流测试
```

## 快速开始

```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
python -m app.api.main
```

如果不想启动 API，可以直接运行最小 harness：

```bash
python -m harness.graph.workflow "分析一下示例公司的财务风险"
```

也可以使用交互式 CLI：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli "分析一下示例公司的存货和现金流风险"
F:\conda_envs\FinTrace\python.exe -m app.cli "监管问询函有没有关注存货跌价准备" --trace
F:\conda_envs\FinTrace\python.exe -m app.cli
```

不带问题时会默认进入 REPL 式连续问答：

```text
================================================================
🔎 FinTrace 交互式 CLI
================================================================
输入问题后按 Enter，系统会自动路由到财务、股权、文档或事件工具。
输入 exit 或 quit 退出。启动时加 --trace 可显示可审计推理路径、工具调用和证据。
运行模式：本地 run_agent
LLM 状态：⚠️ 未配置 QWEN_API_KEY / DASHSCOPE_API_KEY，将返回结构化错误
----------------------------------------------------------------
🧑 你
----------------------------------------------------------------
> 分析一下示例公司的存货风险
...
🧑 你
----------------------------------------------------------------
> exit
```

也可以显式使用：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli --interactive
```

CLI 参数：

- `--trace`：显示中文友好的可审计推理路径、工具调用和证据 ID；
- `--debug-trace`：在 `--trace` 输出中追加底层 LangGraph 节点名；
- `--json`：输出完整 `AgentState` JSON；
- `--session-id`：指定会话 ID；
- `--api-url`：通过 FastAPI 服务调用 Agent；
- `--interactive`：进入连续问答模式。

## CLI 双模式

CLI 支持两种模式。

### 模式一：本地模式

不传 `--api-url` 时，CLI 在当前进程内直接调用核心函数：

```text
CLI
→ harness.graph.workflow.run_agent
→ 工具路由
→ 四个工具
→ 输出回答
```

命令：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli "张某通过哪些主体控制这家公司" --trace
```

优点：

- 不需要启动服务；
- 适合本地开发、调试和快速演示；
- 报错更直接。

### 模式二：FastAPI HTTP 模式

传 `--api-url` 时，CLI 不直接跑 Agent，而是作为 HTTP 客户端调用 FastAPI：

```text
CLI
→ POST /chat
→ FastAPI
→ harness.graph.workflow.run_agent
→ 工具路由
→ 四个工具
→ FastAPI 返回 JSON
→ CLI 格式化输出
```

先启动 API：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.api.main
```

再运行 CLI：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli `
  --api-url http://127.0.0.1:8000 `
  "监管问询函有没有关注存货跌价准备" `
  --trace
```

FastAPI 模式下，CLI 会向下面的接口发送请求：

```http
POST /chat
Content-Type: application/json
```

```json
{
  "query": "监管问询函有没有关注存货跌价准备",
  "session_id": "SESSION-CLI"
}
```

优点：

- 更接近真实部署形态；
- 后续 Web GUI、外部系统和 CLI 可以共用同一个 API；
- 便于把 Agent 服务和用户入口分开。

如果服务没有启动，CLI 会提示先运行：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.api.main
```

建议在本机使用已准备好的 `FinTrace` 环境运行：

```powershell
F:\conda_envs\FinTrace\python.exe -m pytest -q
F:\conda_envs\FinTrace\python.exe -m harness.graph.workflow "监管问询函有没有关注存货跌价准备"
```

## Qwen 配置

默认按 OpenAI 兼容接口调用 Qwen：

```text
QWEN_API_KEY=your_api_key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

也兼容 DashScope 风格变量名：

```text
DASHSCOPE_API_KEY=your_api_key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_CHAT_MODEL=qwen3.7-max-2026-05-20
```

路由与计划生成可以使用单独的模型配置，便于使用更便宜或更快的 planner 模型：

```text
QWEN_PLANNER_API_KEY=your_planner_api_key
QWEN_PLANNER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_PLANNER_MODEL=qwen-plus
```

如果 `QWEN_PLANNER_API_KEY` / `DASHSCOPE_PLANNER_API_KEY` 未配置，系统会自动使用规则 planner；这不会影响最终回答模型的 `QWEN_API_KEY` / `QWEN_MODEL` 配置。

没有配置 API Key 时，系统不会生成确定性模板回答，而是明确返回 `LLM_NOT_CONFIGURED` 警告，避免把非模型输出伪装成正常研判。

CLI 启动时会显示当前 LLM 状态：

```text
LLM 状态：✅ Qwen 已启用（qwen-plus）
```

或：

```text
LLM 状态：⚠️ 未配置 QWEN_API_KEY / DASHSCOPE_API_KEY，将返回结构化错误
```

## 当前目录

```text
app/
harness/
tools/
schemas/
prompts/
data_pipeline/
data/
evaluation/
tests/
deployment/
```

## 开发原则

- LLM 只负责理解、规划和解释；
- 财务指标、股权比例和时间过滤必须由程序计算；
- 所有关键结论必须绑定 `evidence_id`；
- 工具必须能脱离 Agent 独立测试；
- MVP 优先稳定、可解释、可评测。

Planner 采用“按需调用、综合分析主动检查”的策略：普通指标、公告或股东事实查询只调用必要工具。当前 `financial_analysis` 负责三张 normalized 财务报表的精确指标查询和确定性比较，`ownership_analysis` 负责主要股东快照查询、股东反查和跨期持股变化。财务 `risk_scan` 和股权 `penetration` 尚未实现，Agent 不得把指标比较描述成完整风险扫描，也不得把主要股东名单表述为完整控制关系。现有比赛文件不含历史或实时行情，相关问题返回数据不支持，不由 LLM 凭记忆补充。

## 当前工具进度

| 工具 | 当前状态 | 数据来源 |
|---|---|---|
| `financial_analysis` | 已支持 normalized 三表索引、`metric_query`、`metric_compare` 和行级证据；`risk_scan` 暂未实现 | `data/normalized/*.jsonl` / `data/indexes/financial_analysis/financial_metrics.sqlite` |
| `ownership_analysis` | 已支持 normalized 股东快照索引、`holding_query`、`holding_compare`、有效快照选择、集中度和行级证据；`penetration` 暂未实现 | `data/normalized/shareholders.jsonl` / `data/indexes/ownership_analysis/ownership_holdings.sqlite` |
| `document_search` | 已支持 FTS5 词法索引 + FAISS 向量的混合检索；词法索引缺失/失配时返回建库命令；demo 模式回退样例 | `data/indexes/document_search/fintrace_kb.sqlite` + `bm25_index.sqlite` / `vector.faiss` |
| `event_timeline` | 已支持 CSV 事件数据、时间过滤、事件聚类和证据绑定 | `data/events/events.csv` / `tools/event_timeline/sample_data.py` |

## Operation 功能说明

Planner 通过 `operation` 指定工具本次需要完成的具体任务。每个 operation 只承担一种相对明确的职责，避免把查询、计算、比较和解释混在一次工具执行中。

| 工具 | Operation | 功能说明 | 典型问题 |
|---|---|---|---|
| `document_search` | `search` | 在公告正文和研报摘要中执行关键词或语义检索，并按公司、文档类型、日期和发布机构过滤结果；返回可追溯的 Chunk 及其来源信息。 | “贵州茅台的研报如何评价其盈利能力？”“公告中如何描述本次违规事项？” |
| `financial_analysis` | `metric_query` | 查询一个或多个公司在指定报告期的原始财务指标或确定性派生指标，保留数值、单位、报表口径和来源。 | “查询公司 2024 年营业收入和净利润。” |
| `financial_analysis` | `metric_compare` | 对同口径指标进行确定性计算：单公司加多个期间时返回有序序列、相邻期间变化和首尾累计变化，多公司加单一期间时返回各公司数值及差异；工具不生成趋势性语言结论。 | “分析公司近五年的经营现金流趋势。”“比较甲公司和乙公司 2024 年的资产负债率。” |
| `financial_analysis` | `risk_scan` | 暂未实现；当前调用返回 `UNSUPPORTED_QUERY`，不会运行旧规则或生成风险评分。 | “扫描公司近三年的财务异常风险。” |
| `ownership_analysis` | `holding_query` | 查询主要股东快照：提供 `company_ids` 时从公司查股东并返回集中度，提供 `holder_ids` 时从股东反查公司，同时提供则做交叉过滤。 | “公司 2024 年末的前十大股东有哪些？”“某基金出现在哪些公司的主要股东名单中？” |
| `ownership_analysis` | `holding_compare` | 比较同一公司两个快照日期的主要股东名单，确定性识别进入、退出、增持和减持及其变化幅度。 | “哪些主要股东在两个快照日期之间进行了减持？” |
| `ownership_analysis` | `penetration` | 在快照能够证明的持股关系中搜索指定主体到目标公司的有限多层路径，返回每一跳持股比例、路径比例乘积和完整性警告。 | “主体 A 通过哪些层级间接持有公司 B？” |
| `event_timeline` | `event_query` | 按主体、事件类型、关键词和日期范围筛选事件，完成去重和排序，并可包含财务或股东派生信号；返回可供 Agent 组织时间线的事件节点及证据。 | “查询公司 2022 年以来受到处罚的事件。”“整理公司近三年的违规和财务风险时间线。” |
| `event_timeline` | `event_cluster` | 根据事件类型、时间接近程度和实体重合度聚合相关事件，输出事件簇及聚合依据；只表达相关性和时序，不自动认定因果关系。 | “把同一轮违规调查及后续处罚聚合为一个事件簇。” |

选择原则：查原文使用 `search`；只查询财务数值使用 `metric_query`；需要计算跨期变化、连续多期序列或跨公司差异时使用 `metric_compare`。当前不得规划 `risk_scan`。`metric_compare` 不支持“多个公司与多个期间”同时比较，因为这种输入无法唯一确定比较维度。工具负责数值排序和计算，Agent LLM 负责根据工具结果说明上升、下降、增速变化或拐点，不得自行补充数值。持股事实、反向持股查询和集中度统一使用 `holding_query`，股东变化使用 `holding_compare`，多层路径使用 `penetration`。查找、过滤和排序事件统一使用 `event_query`，Agent 根据返回节点组织时间线；需要将相关节点归并为事件簇时使用 `event_cluster`。

## 比赛文本 Document

离线预处理代码统一放在`data_pipeline/`。当前公告与研报的统一Document构建流程为：

```text
announcements.jsonl + 公告 TXT
research_reports.jsonl + abstract
→ data_pipeline.documents
→ data/processed/documents/documents.jsonl
```

执行：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.cli build-documents `
  --data-dir data
```

同时生成质量报告：

```text
data/processed/documents/document_quality.json
```

公告Document字段：

```text
document_id, document_type, company_id, title, published_date,
tags, text, source_ref
```

研报Document额外包含`publisher`。公告`text`读取`document_path`指向的TXT正文，并清除正文开头与`title`严格等价的重复标题行；研报`text`只使用`abstract`，不代表研报全文。不支持的交易所、无文本层公告、空文本和无效字段只记录到质量报告，不进入输出。

JSONL采用无BOM的UTF-8编码。在Windows PowerShell中预览时应显式指定编码：

```powershell
Get-Content -Encoding utf8 data\processed\documents\documents.jsonl -TotalCount 1
```

本命令只构建统一Document，不生成Chunk、不调用Embedding、不构建FAISS。完整模块说明见[data_pipeline/README.md](data_pipeline/README.md)。

## 文档知识库

`document_search` 采用离线建库、在线检索：

```text
documents.jsonl + chunks_v2.jsonl
→ data_pipeline.documents（导入、Embedding、索引构建）
→ data/indexes/document_search
→ tools.document_search
→ Evidence
```

竞赛文本只使用冻结的 `data/processed/documents/chunks_v2.jsonl`，不会在建索引时重新切分。下面的原始文件入口仅用于未来接收额外 PDF、DOCX、TXT 或 Markdown 文件。

先进行不调用 API 的字符和 Token 估算：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index --estimate-only
```

确认成本后，先在本地生成 Batch 请求文件（不调用 API）：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index prepare
```

正式任务分阶段执行：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index submit
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index status
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index collect
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index finalize
```

也可以逐个提交已经准备好的分片：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index submit --shard-id shard-0000
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index submit --shard-id shard-0001
```

`--shard-id` 可以重复传入以一次选择多个分片；不传时提交全部尚未提交的分片。分片名称以 `prepare` 生成的 `state.json` 为准，本次语料为 `shard-0000` 至 `shard-0008`。已保存 `batch_id` 的分片会自动跳过，不会重复提交。

`submit` 使用阿里云百炼 OpenAI 兼容 Batch File API，每个请求包含最多 10 个 Chunk；默认每 20,000 个 Chunk 拆成一个可独立重试的任务。`status` 可重复查询，`collect` 只下载已完成结果，`finalize` 按 `custom_id + data.index` 恢复向量顺序并严格校验缺失、重复、维度和非有限值。存在请求级错误时，执行 `retry` 生成失败请求分片后，重新运行 `submit/status/collect/finalize`。`run` 可串联正常流程，但任务可能需要等待，分阶段命令更便于观察。

若经过人工确认接受少量、具有明确错误记录的缺失，可显式构建部分索引：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index finalize --allow-partial
```

该参数不会忽略未知缺失、重复向量、错误维度或非法浮点数；仅排除错误文件中能够完整映射到 Chunk 的失败请求。SQLite 仍保留全部 Chunk，失败项继续支持 BM25，FAISS 与 `vector_ids.json` 只包含成功向量。覆盖率和排除数量写入 `manifest.json`，具体失败项写入 `embedding_failures.jsonl`。默认 `finalize` 仍要求 100% 完整。

本地断点位于 `data/indexes/document_search/.batch_build/state.json`。输入哈希、模型或维度变化时拒绝混用旧任务；替换不兼容检查点或已有索引必须在 `prepare` 时显式增加 `--force`。在线检索的问题向量仍通过兼容接口同步生成，因为查询必须实时返回。

### 上传文件兼容入口

原始文件建议放在：

```text
data/raw_documents/
  000001.SZ/
    annual_report/
      000001.SZ_2023-04-30_annual_report.pdf
    inquiry_letter/
      000001.SZ_2023-05-12_inquiry_letter.docx
```

文件名推荐格式：

```text
{company_id}_{published_date}_{document_type}.{pdf|docx|txt|md}
```

建库命令：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_file_index `
  --raw-dir data/raw_documents `
  --kb-dir data/indexes/document_search
```

数据量较大、只是追加或重跑未变化文件时，可以使用：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_file_index `
  --raw-dir data/raw_documents `
  --kb-dir data/indexes/document_search `
  --skip-unchanged
```

`--skip-unchanged` 会根据 `source_file + file_hash` 跳过未变化文件。该兼容入口只负责解析、切分和 SQLite 写入，不再提供同步向量构建；正式向量索引统一由上述 Batch 工作流生成。

生成文件：

```text
data/indexes/document_search/
  fintrace_kb.sqlite   # 文档、chunk 正文、页码、来源路径
  vector.faiss         # FAISS 向量索引
  vector_ids.json      # FAISS 行号到 chunk_id 的映射
  embeddings.npy       # 归一化后的原始向量矩阵
  bm25_index.sqlite    # BM25 FTS5 词法索引（bm25/hybrid 模式必需）
  bm25_manifest.json   # 词法索引的 KB 指纹与分词器版本
  build_progress.json  # 完成行数和 API 实际 Token
  batch_jobs.json      # Batch 任务 ID、文件 ID、状态和请求计数
  embedding_failures.jsonl # 被显式排除的 Chunk 和 API 错误，完整索引时为空
  manifest.json        # 输入哈希、模型、维度和构建结果
```

### BM25 FTS5 词法索引

关键词检索不在线重建词法索引。构建知识库后需要再执行一次离线索引构建：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_bm25_index
```

该命令从 `fintrace_kb.sqlite` 读取全部 Chunk，使用与在线完全相同的 bigram 分词器写入 contentless FTS5 表，并生成记录 KB 指纹和分词器版本的 `bm25_manifest.json`。真实语料约 16 万 Chunk，构建约 80 秒，索引约 209MB。环境变量：

```text
FINTRACE_BM25_INDEX_PATH=data/indexes/document_search/bm25_index.sqlite
```

在线的 `bm25/hybrid` 模式要求该索引存在且 manifest 与当前 KB 一致；缺失或失配时返回 `DATA_NOT_AVAILABLE` 并附建库命令。知识库重建后必须重新执行本命令。FTS5 排序参数为默认 `k1=1.2, b=0.75`；与旧内存 BM25 相比，全库查询 top-8 重合率约 91%，公司过滤查询约 76-90%（FTS5 使用全库 IDF，子集 IDF 噪声更大，差异集中在尾部排名）。全库关键词查询耗时从约 55 秒降至约 0.1 秒。

Qwen/DashScope embedding 配置：

```text
DASHSCOPE_EMBEDDING_API_KEY=your_api_key
DASHSCOPE_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v4
DASHSCOPE_EMBEDDING_DIMENSION=1024
EMBEDDING_BATCH_CHUNKS_PER_SHARD=20000
EMBEDDING_BATCH_COMPLETION_WINDOW=24h
EMBEDDING_BATCH_POLL_SECONDS=30
EMBEDDING_TIMEOUT_SECONDS=120
EMBEDDING_MAX_RETRIES=5
```

SQLite 保存 chunk 正文、页码和来源元数据；FAISS 只保存向量索引。在线检索时，FAISS 先返回向量行号，系统再通过 `vector_ids.json` 找到 `chunk_id`，最后回 SQLite 读取原文证据。

`document_search` 支持检索模式：

```text
mode=bm25    # 关键词检索
mode=vector  # FAISS 语义检索
mode=hybrid  # 默认，BM25 + FAISS 合并
```

如果没有 `vector.faiss` 或 embedding 配置不可用，`vector/hybrid` 会给出 warning，并回退到 BM25。

每条检索命中会包含 `retrieval` 诊断信息：

```json
{
  "source": "hybrid",
  "matched_by": ["bm25", "vector"],
  "bm25_score": 0.91,
  "vector_score": 0.78,
  "final_score": 0.85
}
```

`document_search` 的返回数据还包含 `retrieval_debug`，记录召回池大小、BM25/向量命中数量、合并命中数量和最终返回数量。

建库时会额外生成：

```text
data/indexes/document_search/parse_report.json
```

其中包含每个文件的解析状态、页数、文本字符数、chunk 数、识别到的 section 数和潜在 OCR 警告。chunker 会尝试识别常见章节标题，例如“问题一：关于存货跌价准备”“关键审计事项”“管理层讨论与分析”，并写入 `section_title`。

轻量表格抽取：

- DOCX 表格会被转换为可检索文本，例如“列：项目 | 期末余额 | 跌价准备”“行：库存商品 | 210 | 15”；
- PDF 如果当前 PyMuPDF 版本支持 `find_tables()`，会尝试抽取表格并文本化；
- `parse_report.json` 会记录 `table_count`、`skipped_count` 和每个文件的 `duration_ms`。

文档解析与向量索引相关依赖：

```text
pymupdf      # PDF 解析，import 名为 fitz
python-docx  # DOCX 解析，import 名为 docx
faiss-cpu    # 本地向量索引
numpy        # 向量矩阵处理
```

## 股东持股数据

`ownership_analysis` 直接以 normalized 十大股东快照为事实来源，离线构建 SQLite 索引，在线查询不回退样例数据。

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.ownership.build_index
```

环境变量：

```dotenv
FINTRACE_OWNERSHIP_NORMALIZED_DIR=data/normalized
FINTRACE_OWNERSHIP_INDEX_PATH=data/indexes/ownership_analysis/ownership_holdings.sqlite
```

离线导入会为每条持股记录生成稳定 `EVID-OWN-` 证据、按 `s_info_compcode` 生成 resolved 主体 ID（缺失时生成不跨公司合并的 unresolved 主体 ID），并折叠完全重复的记录。`manifest.json` 记录源文件指纹，源数据变化后在线查询会要求重建。

在线查询的核心是有效快照选择：对每个观察时点 `as_of_date`，只使用 `announcement_date <= as_of_date`（防止前视）且 `holder_end_date <= as_of_date` 的最晚快照；`as_of_date` 省略时使用最新已披露快照并在结果中回显实际日期。股东排名按同一快照内持股比例降序计算，不使用缺失严重的 `s_holder_sequence`。

当前开放 `holding_query`（正向快照 + 集中度、按股东名反查、交叉过滤）和 `holding_compare`（进入、退出、增持、减持）。`penetration` 暂未实现，调用返回 `UNSUPPORTED_QUERY`。完整参数、实体 ID 规则和质量标志见 `tools/ownership_analysis/README.md`。

结果固定携带能力边界：仅基于主要股东披露数据，不构成完整股权或实际控制人认定；退出主要股东名单不等于清仓。

## 财务结构化数据

`financial_analysis` 直接以三张 normalized JSONL 为事实来源。在线查询使用由这些文件构建的窄表 SQLite，不读取旧 CSV，也不使用内置样例。

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.financial.build_index
```

环境变量：

```dotenv
FINTRACE_FINANCIAL_NORMALIZED_DIR=data/normalized
FINTRACE_FINANCIAL_INDEX_PATH=data/indexes/financial_analysis/financial_metrics.sqlite
```

当前开放 `metric_query` 和 `metric_compare`。每条结果保留 normalized 来源文件、原始字段、`object_id`、公告日期和映射版本，并生成稳定 Evidence。完整参数、指标目录和期间口径见 `tools/financial_analysis/README.md`。

## 事件时间线数据

`event_timeline` 支持结构化事件数据源：

```text
sample  # 内置样例
csv     # data/events/events.csv
```

环境变量：

```text
EVENT_DATA_SOURCE=csv
EVENTS_PATH=data/events/events.csv
```

`events.csv`：

```csv
event_id,company_id,event_date,event_type,title,description,entities,source_doc_id,source_path,page,evidence_id
EVT-001,000001.SZ,2023-05-12,regulatory_inquiry,年报问询函,交易所要求公司说明存货跌价准备是否充分,000001.SZ;交易所,DOC-INQUIRY-2023,data/raw_documents/inquiry.pdf,2,EVID-EVT-001
```

支持事件类型：

```text
regulatory_inquiry
audit_opinion
controller_change
share_pledge
financial_restated
major_litigation
risk_warning
```

兼容别名：

```text
control_change      → controller_change
regulatory_penalty  → risk_warning
pledge              → share_pledge
litigation          → major_litigation
public_opinion      → risk_warning
```

回退策略：

- CSV 不存在且未强制 `EVENT_DATA_SOURCE=csv`：使用内置样例，并返回 warning；
- CSV 存在但目标公司没有事件：返回 `DATA_NOT_AVAILABLE`，不回退样例；
- CSV 解析或校验失败：返回 `VALIDATION_FAILED`。

CSV 中的 `evidence_id`、`source_doc_id`、`source_path`、`page` 会进入事件 Evidence。事件工具只处理结构化事件记录，不负责从原文临时抽取事件；原文检索和离线抽取应由文档知识库或外部 ETL 完成。

## 混合式路由

路由入口仍然是：

```python
route_query(query)
```

内部已经拆成三层：

```text
harness/routing/entities.py   # 规则实体与参数抽取
harness/routing/planner.py    # 规则 planner + 可选 LLM planner
harness/routing/router.py     # 对外薄入口
```

当前流程：

```text
用户问题
→ 规则抽取 company_ids、report_periods、focus_topics、document_types、event_types
→ 如果配置了 planner 模型，则调用 Qwen planner 生成候选 ExecutionPlan
→ Pydantic 校验并补齐工具参数
→ 失败或未配置时回退到规则 planner
→ LangGraph 执行计划
```

LLM planner 只负责生成结构化工具计划，不直接生成答案；所有计划仍会经过 schema 和 guardrail 校验。

## LangGraph 控制流

CLI 和 FastAPI 仍然调用统一入口：

```python
run_agent(query, session_id)
```

但 `run_agent` 内部已经切换为 LangGraph `StateGraph` 编排，节点定义在：

```text
harness/graph/nodes.py
```

当前图结构：

```text
START
→ route
→ validate_plan
   ├─ invalid → plan_error → structured_error → END
   └─ valid → execute_tools
             → validate_tool_results
                ├─ retryable_failed → retry_tools → validate_tool_results
                ├─ failed → tool_error → structured_error → END
                └─ success → check_evidence
                             ├─ insufficient → evidence_warning → generate_answer
                             └─ sufficient → generate_answer
                                            ├─ llm_failed → structured_error → END
                                            └─ success → END
```

这样外部接口保持稳定，同时把失败分支显式放进图里：

- `plan invalid → plan_error → structured_error`
- `tool failed retryable → retry_tools`
- `tool failed non-retryable → tool_error → structured_error`
- `evidence insufficient → evidence_warning → generate_answer`
- `LLM failed → structured_error`

CLI 使用 `--trace` 时会把 LangGraph 实际经过的 `executed_nodes` 转换为中文友好的可审计推理路径，并用 ✅ / ⚠️ / ❌ 标记节点状态；追加 `--debug-trace` 时会同时显示原始节点名，便于开发调试。

这里的 trace 指可审计执行路径，包括控制流、工具调用、错误分支和证据 ID，不展示也不依赖模型不可验证的私有思维链。

## 示例问题

```text
分析一下示例公司的存货和现金流风险
300838.SZ 2024 年一季度末的前十大股东有哪些
张三持有 300838.SZ 多少股份，2024 年以来有没有减持
监管问询函有没有关注存货跌价准备
这家公司后来发生了哪些监管和控制权事件
```

## 下一步开发

1. 建立 evaluation harness，用标准问题评估工具调用、证据召回和错误率；
2. 增加答案硬校验，确保最终数字、路径、事件均绑定证据；
3. 根据真实比赛数据补充 CSV/知识库字段映射和数据质量报告；
4. 开发 Web Demo，展示对话、工具轨迹、证据、股权图和时间线。
