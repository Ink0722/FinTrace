# FinTrace

FinTrace 是面向 A 股研究场景的证据驱动金融 Agent。系统将自然语言问题解析为受约束的工具动作，从冻结数据索引中取得证据，再由 Qwen 基于证据生成回答。财务数值、股权路径、事件和研报观点均由确定性工具产生；LLM 不负责补造缺失事实。

当前仓库包含：

- 基于 LangGraph 的在线 Agent 控制流；
- 财务分析、股权分析、文档检索、事件脉络和研报观点五个工具；
- 对应的离线数据预处理与索引构建代码；
- CLI、FastAPI 和 Next.js 三种交互入口；
- SQLite 会话、工具轨迹、证据和评测记录；
- 自动化测试、评测执行器及竞赛设计文档。

## 审查入口

建议按以下顺序阅读代码：

1. [schemas/agent_state.py](schemas/agent_state.py) 与 [schemas/request.py](schemas/request.py)：理解状态、请求解析、动作和 LLM 记录的数据契约。
2. [harness/graph/workflow.py](harness/graph/workflow.py)：查看 LangGraph 节点、条件边和统一执行入口。
3. [harness/graph/nodes.py](harness/graph/nodes.py)：查看每个节点如何修改 `AgentState`。
4. [harness/routing/](harness/routing/)：审查实体、时间、可回答性、直连路由、规划和动作校验。
5. [harness/evidence/](harness/evidence/) 与 [harness/guards/validation.py](harness/guards/validation.py)：审查证据合并、充分性判断和工具结果校验。
6. [tools/registry.py](tools/registry.py) 与五个工具的 `interface.py`：审查工具分发、参数校验和错误边界。
7. [harness/skills.py](harness/skills.py)、[harness/prompts.py](harness/prompts.py) 和 [prompts/](prompts/)：审查 LLM Skill、结构化输出与 Prompt 版本。
8. [harness/tracing/store.py](harness/tracing/store.py)、[harness/memory/session_store.py](harness/memory/session_store.py) 与 [harness/memory/manager.py](harness/memory/manager.py)：审查日志、会话持久化、滚动摘要和证据事实记忆。
9. [tests/](tests/)：从测试反查各模块的预期行为和边界条件。

更完整的设计说明见 [docs/README.md](docs/README.md)。源码与测试代表当前真实实现；`docs/` 描述设计目标和验收口径，二者不一致时应以源码、测试和实际索引为准。

## 在线工作流

CLI、FastAPI 和前端最终都进入同一个 `run_agent()` / `stream_agent()` 工作流：

```text
用户请求
  -> load_session                 恢复多轮会话上下文
  -> resolve_request              解析实体、时间、任务和筛选条件
  -> check_pre_answerability      判断完整可路由、带缺口可路由、需澄清或超出能力边界
  -> route_mode
       -> direct                  简单请求生成确定性单工具动作
       -> investigation           LLM Planner 或规则队列逐步规划
  -> validate_action              校验 operation、参数、截止日和重复调用
  -> repair_action                对非法动作进行一次有界修复
  -> execute_one_tool             每轮只执行一个工具动作
  -> validate_tool_result         区分成功、可重试失败和不可重试失败
  -> merge_evidence               将工具证据写入本轮 ledger
  -> review_evidence              判断充分、部分充分或不足
       -> plan_next_action        仍有信息增益时继续有界调查
       -> generate_answer         只基于证据生成结构化答案
  -> structured_error             LLM 或工作流失败时明确报错，不生成兜底结论
  -> persist_session              保存多轮上下文
  -> persist_run                  保存运行、工具、证据、节点和 LLM 轨迹
```

控制流具有工具调用总数、重复检索和无新增证据轮数等预算。这里的 trace 是可审计执行轨迹，不是模型私有思维链。

## 目录结构

```text
FinTrace/
├─ app/                         用户入口
│  ├─ cli.py                    单轮/交互式 CLI，本地或 FastAPI 双模式
│  ├─ cli_render.py             将 AgentState 渲染为可读 Trace
│  └─ api/main.py               FastAPI、SSE、用户和历史会话接口
│
├─ harness/                     在线 Agent 编排层
│  ├─ graph/
│  │  ├─ workflow.py            StateGraph 拓扑、run_agent、stream_agent
│  │  ├─ nodes.py               节点实现
│  │  └─ conditions.py          条件边判断
│  ├─ routing/
│  │  ├─ request_parser.py      规则优先、LLM 补充的请求解析
│  │  ├─ entities.py            查询中的实体与筛选条件抽取
│  │  ├─ time_resolver.py       自然语言日期解析
│  │  ├─ financial_period_resolver.py 财务报告期扩展与口径选择
│  │  ├─ answerability.py       可回答性与能力边界
│  │  ├─ capability_registry.py 已实现能力及 operation 白名单
│  │  ├─ direct_gate.py         简单请求的确定性直连动作
│  │  ├─ planner.py             调查模式的规则动作队列
│  │  └─ action_validator.py    动作校验、防前视和防重复
│  ├─ evidence/
│  │  ├─ ledger.py              证据去重与合并
│  │  └─ review.py              确定性优先的证据充分性判断
│  ├─ guards/validation.py      工具返回值校验与失败分类
│  ├─ memory/
│  │  ├─ session_store.py       SQLite 多轮上下文持久化
│  │  └─ manager.py             消息窗口、滚动摘要和证据事实筛选
│  ├─ tracing/
│  │  ├─ store.py               运行日志及工具/证据/节点/LLM 子记录
│  │  ├─ users.py               本地用户与会话归属
│  │  ├─ export_jsonl.py        按需导出评测 JSONL
│  │  └─ migrate_jsonl.py       旧 JSONL 一次性迁移工具
│  ├─ skills.py                 Prompt 组装、LLM 调用、Schema 校验和重试
│  ├─ prompts.py                Prompt 清单、版本头和依赖校验
│  ├─ runtime_context.py        各 LLM Skill 的最小输入上下文
│  ├─ runtime_db.py             统一 SQLite 路径和连接配置
│  ├─ streaming.py              SSE 事件与答案增量解析
│  ├─ llm.py                    Qwen OpenAI-compatible 客户端
│  └─ answering.py              结构化错误的用户可读渲染
│
├─ schemas/                     跨层 Pydantic 契约
│  ├─ agent_state.py            LangGraph 全局状态
│  ├─ request.py                ParsedRequest、AgentAction、EvidenceReview 等
│  ├─ tool_calls.py             统一工具输入
│  ├─ tool_results.py           统一工具输出和错误
│  ├─ evidence.py               证据结构
│  ├─ memory.py                 摘要输出与已验证事实结构
│  └─ financial/document/ownership/event.py 领域模型
│
├─ tools/                       在线只读工具
│  ├─ registry.py               ToolName 到工具实现的唯一分发入口
│  ├─ entity_resolver.py        公司代码与别名解析
│  ├─ financial_analysis/       财务指标、比较和规则风险扫描
│  ├─ ownership_analysis/       股东快照、比较和有限股权穿透
│  ├─ document_search/          FTS5 + FAISS 混合检索
│  ├─ event_timeline/           事件查询和事件聚合
│  └─ research_analysis/        可回溯研报观点查询
│
├─ data_pipeline/               离线数据与索引构建，不参与在线回答
│  ├─ competition/              比赛原始文件转换和公告修复
│  ├─ documents/                Document、Chunk、Embedding、FTS5、FAISS
│  ├─ entity_alias/             公司别名索引
│  ├─ entity_resolution/        法定主体统一、候选审计与公司档案
│  ├─ financial/                财务窄表 SQLite 构建
│  ├─ ownership/                股东快照与穿透关系索引
│  ├─ events/                   公告事件索引
│  └─ research_views/           研报观点索引
│
├─ prompts/                     版本化 LLM Skill
│  ├─ 00_prompt_manifest.md     Prompt 清单
│  ├─ 01_global_policy.md       所有 Skill 的证据与安全约束
│  ├─ 02_request_parser.md      请求解析
│  ├─ 03_next_action_planner.md 单动作规划
│  ├─ 04_evidence_reviewer.md   证据审查
│  ├─ 05_action_repair.md       动作修复
│  ├─ 06_final_answer.md        最终回答
│  └─ 07_memory_summarizer.md   滚动会话摘要
│
├─ data/                        本地数据、标准化产物和冻结索引（默认不入 Git）
├─ runtime/                     统一可变运行库 `fintrace.sqlite3`
├─ evaluation/                  评测问题集、批次执行器和导出目录
├─ fintrace-frontend/           Next.js 调试与演示前端
├─ tests/                       单元、集成、工作流及迁移测试
├─ docs/                        竞赛要求、数据、Agent、评测和部署文档
├─ deployment/                  部署边界与服务器维护说明
├─ backups/                     仅保留迁移恢复材料，不参与运行
├─ .env.example                 配置模板
├─ requirements.txt             Python 依赖
└─ pytest.ini                   测试配置
```

### 三个必须区分的边界

- `data_pipeline/` 构建冻结数据产品；`tools/` 只读取这些产品并返回可验证结果。
- `schemas/` 定义跨层契约；业务计算不得放进 Schema。
- `harness/` 决定何时调用工具；工具本身不知道对话上下文，也不负责自然语言回答。

## 工具与数据

| 工具 | operation | 作用 | 主要在线索引 |
| --- | --- | --- | --- |
| `financial_analysis` | `metric_query` | 查询公司和报告期的财务指标原值 | `data/indexes/financial_analysis/financial_metrics.sqlite` |
|  | `metric_compare` | 单公司跨期或多公司单期比较 | 同上 |
|  | `risk_scan` | 执行版本化财务风险规则并返回公式、输入和状态 | 同上 |
| `ownership_analysis` | `holding_query` | 正向、反向或交叉查询主要股东快照 | `data/indexes/ownership_analysis/ownership_holdings.sqlite` |
|  | `holding_compare` | 比较两个观察时点的进入、退出和增减持 | 同上 |
|  | `penetration` | 在已确认同一主体关系内搜索有限持股路径 | 同上 + `entity_master.sqlite` |
| `document_search` | `search` | 对公告正文和研报 Chunk 做混合检索 | `fintrace_kb.sqlite`、`bm25_index.sqlite`、`vector.faiss` |
| `event_timeline` | `event_query` | 按主体、日期和事件类型查询事件 | `data/indexes/event_timeline/events.sqlite` |
|  | `event_cluster` | 将有明确关联依据的记录聚合为事件簇 | 同上 |
| `research_analysis` | `view_query` | 查询带机构、日期和来源归属的研报观点 | `data/indexes/research_analysis/research_views.sqlite` |

各工具的参数、返回字段、错误语义和边界分别见：

- [tools/financial_analysis/README.md](tools/financial_analysis/README.md)
- [tools/ownership_analysis/README.md](tools/ownership_analysis/README.md)
- [tools/document_search/README.md](tools/document_search/README.md)
- [tools/event_timeline/README.md](tools/event_timeline/README.md)
- [tools/research_analysis/README.md](tools/research_analysis/README.md)

系统没有历史/实时行情、用户账户和完整工商股权数据。风险信号不得表述为造假结论；主要股东路径不得表述为完整控制关系；研报观点不得升级为公司客观事实。

## 运行配置

推荐使用现有 `FinTrace` 环境。首次配置：

```powershell
F:\conda_envs\FinTrace\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` 不进入 Git。主要配置分组如下：

```dotenv
# 最终回答模型
QWEN_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=

# 请求解析、规划、审查、修复和记忆摘要模型；可与主模型相同
QWEN_PLANNER_API_KEY=
QWEN_PLANNER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_PLANNER_MODEL=

# 统一知识截止日，由工作流注入，Planner 无权修改
FINTRACE_KNOWLEDGE_CUTOFF=2026-05-28

# 统一会话、日志和评测数据库
FINTRACE_RUNTIME_DB=runtime/fintrace.sqlite3
FINTRACE_EVAL_LOG_ENABLED=true
```

完整索引路径和 Embedding 配置见 [.env.example](.env.example)。未配置 LLM 或调用失败时，系统返回结构化错误，不使用确定性模板伪装模型回答。

## 启动方式

### CLI 本地模式

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli "600519.SH 2024年营业收入是多少" --trace
```

交互式多轮对话：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli --interactive
```

常用命令：`/status`、`/trace on|off`、`/debug on|off`、`/clear`、`/help`、`exit`。

### FastAPI

```powershell
F:\conda_envs\FinTrace\python.exe -m app.api.main
```

- API 地址：`http://127.0.0.1:8000`
- 健康检查：`GET /health`
- Swagger：`http://127.0.0.1:8000/docs`

CLI 也可以通过 API 调用同一工作流：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli `
  --api-url http://127.0.0.1:8000 `
  "查询贵州茅台的重要监管事件" --trace
```

### Web 前端

先启动 FastAPI，再在另一个终端运行：

```powershell
Set-Location fintrace-frontend
npm install
npm run dev
```

访问 `http://127.0.0.1:3000`。前端通过 Next.js Route Handler 转发到 FastAPI，并用 SSE 展示节点、工具、证据和回答增量。前端配置及历史会话加载方式见 [fintrace-frontend/README.md](fintrace-frontend/README.md)。

## API 概览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/chat` | 非流式执行一轮 Agent |
| `POST` | `/chat/stream` | SSE 流式执行一轮 Agent |
| `GET` | `/runs` | 分页查询运行摘要 |
| `GET` | `/runs/{run_id}` | 查询工具、证据、节点和 LLM 详情 |
| `GET/POST` | `/users` | 查询或创建本地用户 |
| `PATCH/DELETE` | `/users/{user_id}` | 修改或删除本地用户 |
| `GET` | `/users/{user_id}/sessions` | 获取轻量会话摘要 |
| `GET` | `/users/{user_id}/sessions/{session_id}` | 恢复完整历史轨迹 |
| `PATCH/DELETE` | `/users/{user_id}/sessions/{session_id}` | 重命名或删除会话 |

API 的请求与响应模型以 [app/api/main.py](app/api/main.py) 为准。

## 运行时数据

所有可变运行状态统一写入：

```text
runtime/fintrace.sqlite3
```

该数据库同时保存：

- 会话记忆与轮次；
- 本地用户及会话归属；
- Agent 主运行记录；
- 工具调用及结构化结果；
- 文件类和非文件类证据；
- LangGraph 节点事件；
- LLM 调用元数据；
- 评测批次及用例状态。

前端通过 API 读取 SQLite，不直接打开数据库。JSONL 只在评测交换时按需导出：

```powershell
F:\conda_envs\FinTrace\python.exe -m harness.tracing.export_jsonl evaluation\exports\agent_runs.jsonl
```

数据库表和维护规则见 [runtime/README.md](runtime/README.md)。

## 离线构建

在线工具不解析原始比赛文件，也不临时重建索引。主要构建入口如下：

```powershell
# 财务指标
F:\conda_envs\FinTrace\python.exe -m data_pipeline.financial.build_index

# 实体统一与股权
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.build_index
F:\conda_envs\FinTrace\python.exe -m data_pipeline.ownership.build_index

# 公告事件
F:\conda_envs\FinTrace\python.exe -m data_pipeline.events.build_index

# 研报观点
F:\conda_envs\FinTrace\python.exe -m data_pipeline.research_views.build_index

# 文档 Embedding Batch 与检索索引
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index --estimate-only
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index prepare
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index submit
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index status
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index collect
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index finalize
```

完整输入、输出、Manifest 和失败恢复机制见 [data_pipeline/README.md](data_pipeline/README.md) 与 [docs/02-数据资产与离线构建.md](docs/02-数据资产与离线构建.md)。

## 测试与评测

运行完整测试：

```powershell
F:\conda_envs\FinTrace\python.exe -m pytest -q --basetemp runtime\pytest-run
```

当前基线：`239 passed`。

测试文件按职责划分：

- `test_schemas.py`：数据契约；
- `test_routing.py`、`test_langgraph_conditions.py`：路由与条件边；
- `test_workflow.py`、`test_streaming_workflow.py`：完整 Agent 与 SSE；
- `test_*_analysis.py`、`test_document_search.py`、`test_event_timeline.py`：工具行为；
- `test_local_users.py`、`test_evaluation_log.py`：用户、会话和可观测性；
- `test_memory_manager.py`：近期消息、滚动摘要、证据事实和相关记忆筛选；
- `test_evaluation_runner.py`：评测批次执行；
- `test_*_migration.py`：历史数据迁移。

评测问题集位于 `evaluation/questions/questions_annotated_v1.jsonl`，批次执行方式见 [evaluation/runner/README.md](evaluation/runner/README.md)，指标和人工标注规则见 [docs/05-评测、金标与人工标注规范.md](docs/05-评测、金标与人工标注规范.md)。

## 文档导航

| 文档 | 作用 |
| --- | --- |
| [00-竞赛要求与验收口径](docs/00-竞赛要求与验收口径.md) | 任务目标、指标与交付边界 |
| [01-系统目标能力与数据边界](docs/01-系统目标能力与数据边界.md) | 能回答什么、不能回答什么 |
| [02-数据资产与离线构建](docs/02-数据资产与离线构建.md) | 数据产品、索引和血缘 |
| [03-文本语料、Document、Chunk与检索索引](docs/03-文本语料、Document、Chunk与检索索引.md) | 文档检索技术设计 |
| [04-在线Agent、记忆、路由与证据工作流](docs/04-在线Agent、记忆、路由与证据工作流.md) | 在线控制流与证据治理 |
| [05-评测、金标与人工标注规范](docs/05-评测、金标与人工标注规范.md) | 评测和标注实施 |
| [06-交付、部署、演示与答辩说明](docs/06-交付、部署、演示与答辩说明.md) | 部署和交付 |
| [07-风险扫描、事件脉络与股权穿透实现审查](docs/07-风险扫描、事件脉络与股权穿透开发计划.md) | 三个专项工具的实现、边界与测试入口 |
| [08-统一评测清单与实施记录](docs/08-统一评测清单与实施记录.md) | 后续评测任务及结果登记 |
| [09-Agent评测实施方案与结果模板](docs/09-Agent评测实施方案与结果模板.md) | 正式指标口径、人工表格与白皮书结果模板 |
| [10-金融智能体评测体系与实验设计](docs/10-金融智能体评测体系与实验设计.md) | 可直接纳入技术白皮书的评测正文 |

## 审查时重点关注

- `knowledge_cutoff` 是否只能由工作流注入，且所有查询都防止前视；
- Planner 生成的工具名、operation 和参数是否经过白名单及 Schema 校验；
- 工具失败、空结果和证据不足是否被正确区分；
- 财务计算、持股比例和日期筛选是否完全由程序执行；
- 最终回答中的关键结论是否能追溯到 `evidence_id`；
- 研报观点、风险信号和有限股权路径是否保持正确语义边界；
- LLM 失败时是否明确报错，而不是生成无证据兜底答案；
- CLI、FastAPI、前端和评测 Runner 是否复用同一工作流与 SQLite 记录格式。
