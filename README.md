# FinTrace

FinTrace 是一个面向 A 股投研场景的证据驱动型 Agentic AI 问答系统骨架。

Last Update on Codex 2026/8/18

当前版本已经具备：

- 项目技术拆解和目录结构；
- Pydantic Schema；
- 四个工具的固定输入输出；
- 财务指标、主要股东快照、文档检索和事件时间线工具；
- SQLite / FTS5 / FAISS 的冻结索引与严格失败策略；
- 基于 LangGraph 的 Agent Harness 和组合工具路由；
- Qwen 兼容 OpenAI API 的 LLM 客户端封装；
- Pytest 测试骨架；
- `.env.example` 和本地运行说明。

在线工具只读取由比赛数据构建的冻结索引。索引缺失、过期或目标数据不可得时返回结构化错误，不使用 sample 数据冒充真实事实。

## 竞赛设计文档

请从 [文档导航](docs/README.md) 开始阅读。主文档明确区分竞赛目标、数据边界、在线工作流、评测与标注；专题数据设计仅作实现参考，历史材料已移至 `docs/archive/`。

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
→ harness.tracing.store.persist_run()  # 请求、工具、证据、节点和模型调用事务性落盘
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

### CLI 调用方案

CLI 支持默认轻量问答、可读 Trace、完整 Debug Trace，以及本地和 FastAPI 两种执行方式。普通界面和 Trace 均不会直接输出原始 JSON；只有显式使用 `--json` 时才输出供程序处理的完整 `AgentState`。

单轮轻量问答：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli "600519.SH 2024年营业收入是多少"
```

默认先显示回答，随后只显示一行状态：

```text
🤖 FinTrace
------------------------------------------------------------------------
贵州茅台（600519.SH）2024年度营业收入为……

✅ 已回答 | 🛠️ 1 次工具 | 📎 1 条证据 | ⏱️ 6.9 秒
```

需要观察请求解析、路由、工具输入、工具结果、证据和缺口时使用：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli `
  "结合公告分析600519.SH的存货风险" `
  --trace
```

`--trace` 使用中文字段逐项展示，不倾倒参数或结果 JSON。需要排查 LangGraph 节点时增加 `--debug-trace`。

不带问题或显式使用 `--interactive` 会进入共享同一 `session_id` 的多轮对话：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli --interactive
```

```text
========================================================================
🔎 FinTrace 多轮金融问答
========================================================================
[1] 🧑 你 > 贵州茅台2024年营业收入是多少？

🤖 FinTrace
……

[2] 🧑 你 > 那它的存货呢？
```

交互命令：

- `/status`：查看会话 ID、最近轮次、当前主体、期间和 Trace 状态；
- `/trace on|off`：动态开关可读 Trace；
- `/debug on|off`：动态开关完整 LangGraph 节点；
- `/clear`：生成新会话 ID，后续问题不再继承旧上下文；
- `/help`：显示命令帮助；
- `exit` 或 `quit`：退出。

CLI 参数：

- `--trace`：显示实体、时间、任务解析，路由、工具输入与结果、证据和缺口；
- `--debug-trace`：在可读 Trace 后追加全部 LangGraph 节点名和职责；
- `--json`：仅用于单轮开发或程序集成；交互模式禁止使用；
- `--session-id`：指定会话 ID；
- `--api-url`：通过 FastAPI 服务调用 Agent；
- `--interactive`：进入连续问答模式。

### 逐轮日志与后续评测

日志写入位于 `run_agent()`，因此本地 CLI、FastAPI 和前端都会记录相同格式。SQLite 是唯一运行日志事实源：

```text
runtime/fintrace.sqlite3
```

数据库分表保存运行主记录、工具执行、文件与非文件证据、工作流节点事件和 LLM 调用。记录包含 `run_id`、`trace_id`、`session_id`、递增的 `turn_id`、问题、解析上下文、路由模式、回答、限制、错误、终止原因和耗时，但不保存 API Key 或模型私有思维链。

```dotenv
FINTRACE_EVAL_LOG_ENABLED=true
FINTRACE_RUNTIME_DB=./runtime/fintrace.sqlite3
```

该数据库同时保存多轮会话记忆、用户与会话归属、运行日志和评测批次。关闭运行日志时设置 `FINTRACE_EVAL_LOG_ENABLED=false`，会话记忆仍会正常写入。SQLite 使用 WAL、外键和事务保证并发写入一致性。需要评测交换文件时按需导出，不再持续维护两份 JSONL：

```powershell
F:\conda_envs\FinTrace\python.exe -m harness.tracing.export_jsonl evaluation\exports\agent_runs.jsonl
```

前端或调试工具通过 `GET /runs` 分页查询运行列表，通过 `GET /runs/{run_id}` 获取工具、证据、节点和模型调用详情，不直接读取数据库文件。

### Web 前端

左下角提供本地用户工作区切换、新建、重命名和删除。当前版本不要求登录，
但聊天请求会携带 `user_id`，后端校验 `session_id` 归属；既有历史会话会自动
归入默认的“本地用户”。浏览器仅保存当前选择和各用户的富界面缓存，用户及
会话归属以 SQLite 为准。
切换用户或刷新页面时会优先加载后端会话；`localStorage` 只补充尚未发送的新会话
和工具展开状态等临时界面信息，不再阻止历史会话显示。

前端位于 `fintrace-frontend/`，浏览器请求先到 Next.js Route Handler，再由服务端转发至 FastAPI，因此无需配置浏览器 CORS：

```text
Browser → POST /api/fintrace/chat → FastAPI POST /chat → run_agent()
```

先在项目根目录启动后端：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.api.main
```

再打开另一个终端启动前端：

```powershell
cd fintrace-frontend
npm install
npm run dev
```

访问 `http://localhost:3000`。默认后端为 `http://127.0.0.1:8000`；需要修改时，在 `fintrace-frontend/.env.local` 设置 `FINTRACE_API_BASE_URL`。Web 对话使用 `POST /chat/stream`：LangGraph 节点、工具状态和证据通过 SSE 实时推送，Qwen 最终回答使用真实 token 流；`turn.completed` 返回权威最终状态并写入统一 SQLite。原 `POST /chat` 继续服务于 CLI、测试和非流式调用方。

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

`request_parser`、`next_action_planner`、`evidence_reviewer` 和 `action_repair` 使用 `QWEN_PLANNER_*` 配置；`final_answer` 使用主模型的 `QWEN_*` 配置。Planner Key 未配置时会回退到主模型 Key，Planner 模型未配置时会回退到主模型名称；只有模型调用失败或输出未通过 Schema 校验时，调查节点才使用规则队列降级。两组配置可以暂时填写同一模型，也可以日后独立切换。

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

Planner 采用“按需调用、综合分析主动检查”的策略：普通指标、公告或股东事实查询只调用必要工具。`financial_analysis` 已支持确定性风险规则扫描；`ownership_analysis` 已支持主要股东快照范围内的有限持股路径搜索；`event_timeline` 使用公告构建的冻结事件索引；`research_analysis` 查询离线提炼且带归属的研报观点。风险信号不能直接认定造假，有限路径不能表述为完整控制关系，研报观点不能升级为公司客观事实。现有比赛文件不含历史或实时行情，相关问题返回数据不支持，不由 LLM 凭记忆补充。

## 当前工具进度

| 工具 | 当前状态 | 数据来源 |
|---|---|---|
| `financial_analysis` | 已支持 `metric_query`、`metric_compare`、逐期间 `financial-risk-rules-v2` 和行级证据；阈值尚未金标校准，不输出综合分 | `data/normalized/*.jsonl` / `data/indexes/financial_analysis/financial_metrics.sqlite` |
| `ownership_analysis` | 已支持快照查询、比较、集中度及基于可桥接主体的有界 `penetration` | `data/normalized/shareholders.jsonl` / `data/indexes/ownership_analysis/ownership_holdings.sqlite` |
| `document_search` | 已支持 FTS5 词法索引 + FAISS 向量的混合检索；词法索引缺失/失配时返回建库命令；demo 模式回退样例 | `data/indexes/document_search/fintrace_kb.sqlite` + `bm25_index.sqlite` / `vector.faiss` |
| `event_timeline` | 已支持公告标题事件 SQLite、阶段/文号/机构抽取、时间/类型/关键词过滤、证据约束聚类和空结果诊断 | `data/normalized/announcements.jsonl` / `data/indexes/event_timeline/events.sqlite` |
| `research_analysis` | 已支持机构观点、评级、盈利预测、风险提示和研报引用事实的结构化查询及Chunk追溯 | `data/normalized/research_reports.jsonl` / `data/indexes/research_analysis/research_views.sqlite` |

## Operation 功能说明

Planner 通过 `operation` 指定工具本次需要完成的具体任务。每个 operation 只承担一种相对明确的职责，避免把查询、计算、比较和解释混在一次工具执行中。

| 工具 | Operation | 功能说明 | 典型问题 |
|---|---|---|---|
| `document_search` | `search` | 在公告正文和研报摘要中执行关键词或语义检索，并按公司、文档类型和发布日期过滤结果；工作流注入 `knowledge_cutoff` 防止召回截止日之后披露的材料，返回可追溯的 Chunk 及其来源信息。 | “贵州茅台的研报如何评价其盈利能力？”“公告中如何描述本次违规事项？” |
| `financial_analysis` | `metric_query` | 查询一个或多个公司在指定报告期的原始财务指标或确定性派生指标，保留数值、单位、报表口径和来源。 | “查询公司 2024 年营业收入和净利润。” |
| `financial_analysis` | `metric_compare` | 对同口径指标进行确定性计算：单公司加多个期间时返回有序序列、相邻期间变化和首尾累计变化，多公司加单一期间时返回各公司数值及差异；工具不生成趋势性语言结论。 | “分析公司近五年的经营现金流趋势。”“比较甲公司和乙公司 2024 年的资产负债率。” |
| `financial_analysis` | `risk_scan` | 对同口径期间执行v2确定性规则，逐期间返回触发、未触发、输入不足、不适用、公式、阈值和证据；不输出未经校准的综合评分，也不直接认定造假。 | “扫描公司近三年的财务异常风险。” |
| `ownership_analysis` | `holding_query` | 查询主要股东快照：提供 `company_ids` 时从公司查股东并返回集中度，提供 `holder_ids` 时从股东反查公司，同时提供则做交叉过滤。 | “公司 2024 年末的前十大股东有哪些？”“某基金出现在哪些公司的主要股东名单中？” |
| `ownership_analysis` | `holding_compare` | 比较同一公司两个快照日期的主要股东名单，确定性识别进入、退出、增持和减持及其变化幅度。 | “哪些主要股东在两个快照日期之间进行了减持？” |
| `ownership_analysis` | `penetration` | 在快照能够证明的持股关系中搜索指定主体到目标公司的有限多层路径，返回每一跳持股比例、路径比例乘积和完整性警告。 | “主体 A 通过哪些层级间接持有公司 B？” |
| `event_timeline` | `event_query` | 按主体、事件类型、关键词和日期范围筛选事件，完成去重和排序，并可包含财务或股东派生信号；返回可供 Agent 组织时间线的事件节点及证据。 | “查询公司 2022 年以来受到处罚的事件。”“整理公司近三年的违规和财务风险时间线。” |
| `event_timeline` | `event_cluster` | 根据事件类型、时间窗口、共享文号或标题主题相似度聚合相关事件并公开聚合依据；跨类型关系必须有共享文号，不自动认定因果关系。 | “把同一轮违规调查及后续处罚聚合为一个事件簇。” |
| `research_analysis` | `view_query` | 按公司、日期、机构、观点类型和主题查询带归属的研报观点；正文栏目观点绑定Chunk，标题和元数据观点保留文档级来源。 | “近两年机构如何评价公司的盈利前景？”“研报提示了哪些风险？” |

选择原则：查原文使用 `search`；只查询财务数值使用 `metric_query`；确定性比较使用 `metric_compare`；跨科目风险排查使用 `risk_scan`。`metric_compare` 不支持“多个公司与多个期间”同时比较。工具负责数值与规则计算，Agent LLM 负责解释，不得补充数值。持股事实和集中度使用 `holding_query`，股东变化使用 `holding_compare`，指定主体到目标公司的有限路径使用 `penetration`。事件筛选排序使用 `event_query`，相关节点归并使用 `event_cluster`；机构观点使用 `view_query`。事件或观点的原因、金额和原文上下文再由 `document_search` 补充，事件簇不表示因果，研报陈述不表示事实已独立核验。

文档调查会继承请求解析阶段唯一确定的公司和文档类型过滤条件，避免 Planner 漏填参数后执行无关的全库检索。同一轮最多进行一次初始检索和一次 Query 改写；之后仍未覆盖的证据缺口进入 Limitations，不继续消耗调查预算。

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

当前开放 `holding_query`、`holding_compare` 和 `penetration`。穿透只扩展统一实体库中已经确认的上市公司主体，采用有界路径搜索并返回每跳快照证据；待审名称候选不会进入在线图，无路径不等于不存在关系。完整参数、实体 ID 规则和质量标志见 `tools/ownership_analysis/README.md`。

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

当前开放 `metric_query`、`metric_compare` 和 `risk_scan`。每条结果保留 normalized 来源、公告日期和映射版本；风险扫描额外返回规则版本、公式、阈值、计算输入、覆盖率及跳过原因。完整参数、指标目录和期间口径见 `tools/financial_analysis/README.md`。

风险问题将“用户目标期间”和“实际计算期间”分开：指定一个年度时自动使用截至该年的全部可比年度；未指定时优先使用知识截止日前全部可用年度，没有年度数据则选取数据最多的同口径中报或季报。只有一个可用期间时仍执行点时规则，跨期和连续性规则明确返回数据不足。期间选择由SQLite和确定性解析器完成，Planner无权猜测年份。CLI Trace会同时显示两组期间及选择方式。

## 事件时间线数据

`event_timeline` 使用 normalized 公告离线构建标题级事件索引：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.events.build_index
```

环境变量：

```dotenv
FINTRACE_EVENT_NORMALIZED_DIR=data/normalized
FINTRACE_EVENT_INDEX_PATH=data/indexes/event_timeline/events.sqlite
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

每条标题事件标记 `extraction_method=announcement_title_rule_v3`；无法获得实际发生日时使用 `date_precision=announcement_only`。否定历史陈述不建为事件。`event_query` 负责筛选排序，`event_cluster` 负责有依据的聚类；时间或主题相关不等于因果。索引缺失、过期或查询为空时显式返回结构化错误及诊断，不回退样例。

## 研报观点数据

`research_analysis` 从研报元数据、标题和摘要固定栏目离线构建观点索引：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.research_views.build_index
```

环境变量：

```dotenv
FINTRACE_RESEARCH_SOURCE_PATH=data/normalized/research_reports.jsonl
FINTRACE_RESEARCH_CHUNKS_PATH=data/processed/documents/chunks_v2.jsonl
FINTRACE_RESEARCH_INDEX_PATH=data/indexes/research_analysis/research_views.sqlite
```

简单观点问题直接调用 `research_analysis.view_query`；询问观点理由或原文依据时先查结构化观点，再限定研报类型调用 `document_search.search`。当前规则抽取不调用LLM，无法稳定结构化的复杂观点不会猜测补全。

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
