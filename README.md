# FinTrace

FinTrace 是一个面向 A 股投研场景的证据驱动型 Agentic AI 问答系统骨架。

当前版本已经具备：

- 项目技术拆解和目录结构；
- Pydantic Schema；
- 四个工具的固定输入输出；
- 财务风险、股权穿透、文档检索和事件时间线工具；
- CSV / SQLite / FAISS / sample 的多数据源回退策略；
- 基于 LangGraph 的 Agent Harness 和组合工具路由；
- Qwen 兼容 OpenAI API 的 LLM 客户端封装；
- Pytest 测试骨架；
- `.env.example` 和 `docker-compose.yml`。

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

## Code Review 导览

一次本地 CLI 问答的主调用链：

```text
app.cli.main()
→ print_answer()
→ harness.graph.workflow.run_agent()
→ LangGraph StateGraph
   → route_node()
      → harness.routing.router.route_query()
      → harness.routing.planner.build_plan()
      → 规则 planner 或 Qwen planner
   → validate_plan_node()
      → harness.guards.validation.validate_plan()
   → execute_tools_node()
      → tools.registry.execute_tool()
      → document_search / financial_risk_analysis / ownership_penetration / event_timeline
      → harness.evidence.ledger.merge_evidence()
   → validate_tool_results_node()
      → harness.guards.validation.validate_tool_result()
   → check_evidence_node()
   → generate_answer_node()
      → harness.answering.generate_answer_with_status()
      → harness.llm.QwenClient.chat_json()
   → structured_error_node() 或 END
→ harness.tracing.jsonl.write_trace()
→ CLI 格式化输出
```

FastAPI 模式只是在入口层替换为：

```text
app.api.main.chat()
→ run_agent()
→ AgentState.model_dump()
```

各目录职责：

```text
app/             CLI 和 FastAPI 入口
harness/         Agent 编排、路由、校验、回答、trace
tools/           四个可独立测试的金融工具
schemas/         Pydantic 数据契约
knowledge_base/  离线文档知识库构建
prompts/         system / planner prompt
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
knowledge_base/
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

## 当前工具进度

| 工具 | 当前状态 | 数据来源 |
|---|---|---|
| `financial_risk_analysis` | 已支持 CSV 结构化财务数据、指标计算、风险规则和证据绑定 | `data/financial/financial_records.csv` / `tools/financial_risk/sample_data.py` |
| `ownership_penetration` | 已支持 CSV 数据源、有界图搜索、穿透比例和关系证据 | `data/ownership/*.csv` / `tools/ownership_graph/sample_data.py` |
| `document_search` | 已支持 SQLite 知识库优先检索；无知识库时回退样例 BM25 | `data/knowledge_base/fintrace_kb.sqlite` / `tools/document_search/sample_data.py` |
| `event_timeline` | 已支持 CSV 事件数据、时间过滤、事件聚类和证据绑定 | `data/events/events.csv` / `tools/event_timeline/sample_data.py` |

## 文档知识库

`document_search` 采用离线建库、在线检索：

```text
PDF / DOCX / TXT / MD
→ knowledge_base.document_ingestion
→ SQLite chunks
→ document_search BM25 检索
→ Evidence
```

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
F:\conda_envs\FinTrace\python.exe -m knowledge_base.document_ingestion.build_kb `
  --raw-dir data/raw_documents `
  --kb-dir data/knowledge_base
```

如果要同时建立 FAISS 向量索引，追加 `--build-vector`：

```powershell
F:\conda_envs\FinTrace\python.exe -m knowledge_base.document_ingestion.build_kb `
  --raw-dir data/raw_documents `
  --kb-dir data/knowledge_base `
  --build-vector
```

数据量较大、只是追加或重跑未变化文件时，可以使用：

```powershell
F:\conda_envs\FinTrace\python.exe -m knowledge_base.document_ingestion.build_kb `
  --raw-dir data/raw_documents `
  --kb-dir data/knowledge_base `
  --skip-unchanged
```

`--skip-unchanged` 会根据 `source_file + file_hash` 跳过未变化文件；如果同时需要向量索引，当前建议重建整份 FAISS 索引。

生成文件：

```text
data/knowledge_base/
  fintrace_kb.sqlite   # 文档、chunk 正文、页码、来源路径
  vector.faiss         # FAISS 向量索引
  vector_ids.json      # FAISS 行号到 chunk_id 的映射
  embeddings.npy       # 可选调试用 embedding 矩阵
  manifest.json        # 建库时间、chunk 数、失败文件
```

Qwen/DashScope embedding 配置：

```text
EMBEDDING_PROVIDER=dashscope
DASHSCOPE_EMBEDDING_API_KEY=your_api_key
DASHSCOPE_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_BATCH_SIZE=16
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
data/knowledge_base/parse_report.json
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

## 股权穿透数据

`ownership_penetration` 支持数据源抽象，当前实现：

```text
sample  # 内置样例
csv     # data/ownership/entities.csv + relations.csv
```

环境变量：

```text
OWNERSHIP_DATA_SOURCE=csv
OWNERSHIP_ENTITIES_PATH=data/ownership/entities.csv
OWNERSHIP_RELATIONS_PATH=data/ownership/relations.csv
```

`entities.csv`：

```csv
entity_id,entity_name,entity_type,company_id
PERSON-001,张某,PERSON,
HOLDCO-001,示例控股有限公司,COMPANY,
000001.SZ,示例公司,LISTED_COMPANY,000001.SZ
```

`relations.csv`：

```csv
source_entity_id,target_entity_id,relation_type,ratio,start_date,end_date,evidence_id,source_doc_id,source_path,page
PERSON-001,HOLDCO-001,OWNS,80%,2020-01-01,,EVID-OWN-001,DOC-001,data/raw_documents/ownership.pdf,12
HOLDCO-001,000001.SZ,OWNS,0.35,2020-01-01,,EVID-OWN-002,DOC-002,data/raw_documents/annual_report.pdf,24
```

支持关系类型：

```text
OWNS
CONTROLS
ACTS_IN_CONCERT
VOTING_RIGHTS
```

回退策略：

- CSV 不存在且未强制 `OWNERSHIP_DATA_SOURCE=csv`：使用内置样例，并返回 warning；
- CSV 存在但目标公司没有关系：返回 `DATA_NOT_AVAILABLE`，不回退样例；
- CSV 校验失败：返回 `VALIDATION_FAILED`。

图搜索采用有界搜索：

```text
target 反向 BFS 子图
→ source 到 target 的有界 DFS
→ max_depth 默认 5，最高 8
→ max_paths 默认 50，最高 200
```

路径会按控制关系、穿透比例、路径长度和证据完整性排序，避免在大图上无限枚举路径。

## 财务结构化数据

`financial_risk_analysis` 支持结构化财务记录数据源：

```text
sample  # 内置样例
csv     # data/financial/financial_records.csv
```

环境变量：

```text
FINANCIAL_DATA_SOURCE=csv
FINANCIAL_RECORDS_PATH=data/financial/financial_records.csv
```

`financial_records.csv`：

```csv
company_id,company_name,report_period,statement_type,metric_code,metric_name,value,unit,currency,source_doc_id,source_path,page,evidence_id
000001.SZ,示例公司,2022A,balance_sheet,INVENTORY,存货,310,CNY,CNY,ANNUAL-2022,data/raw_documents/annual_report.pdf,86,EVID-FIN-001
```

常用 `metric_code`：

```text
REVENUE
NET_PROFIT
OPERATING_CASHFLOW
INVENTORY
ACCOUNTS_RECEIVABLE
GROSS_PROFIT
NON_RECURRING_PROFIT
```

回退策略：

- CSV 不存在且未强制 `FINANCIAL_DATA_SOURCE=csv`：使用内置样例，并返回 warning；
- CSV 存在但目标公司没有记录：返回 `DATA_NOT_AVAILABLE`，不回退样例；
- CSV 解析或校验失败：返回 `VALIDATION_FAILED`。

CSV 中的 `evidence_id`、`source_doc_id`、`source_path`、`page` 会进入财务 Evidence，供最终回答追溯来源。

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
→ 规则抽取 company_id、period、focus_topics、document_types、event_types
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
张某通过哪些主体控制这家公司
监管问询函有没有关注存货跌价准备
这家公司后来发生了哪些监管和控制权事件
```

## 下一步开发

1. 建立 evaluation harness，用标准问题评估工具调用、证据召回和错误率；
2. 增加答案硬校验，确保最终数字、路径、事件均绑定证据；
3. 根据真实比赛数据补充 CSV/知识库字段映射和数据质量报告；
4. 开发 Web Demo，展示对话、工具轨迹、证据、股权图和时间线。
