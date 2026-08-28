# FinTrace

FinTrace 是面向 A 股研究场景的证据驱动金融 Agent。系统将用户问题解析为受约束的工具动作，从冻结的数据索引中取得财务、股权、公告事件、研报观点和文档证据，再由 Qwen 在证据边界内生成回答。

项目的核心原则是：**数据计算交给确定性工具，语言组织交给大模型，所有结论保留来源，缺少证据时明确说明。** 财务异常仅作为风险信号，有限持股路径不等同于完整控制关系，研报观点也不会被表述为公司客观事实。

## 当前能力

- 基于 LangGraph 的多轮 Agent 控制流；
- 实体、时间、任务、筛选条件和可回答性解析；
- 简单问题确定性直连，复杂问题有界调查；
- 财务分析、股权分析、文档检索、事件脉络和研报观点五类工具；
- 工具参数预检、失败分类、有限修复和证据充分性审查；
- 近期消息窗口与滚动摘要组成的多轮记忆；
- CLI、FastAPI、SSE 和 Next.js 图形界面；
- SQLite 会话、工具、证据、节点和 LLM 调用轨迹；
- 评测批次执行、专项工具实验和白皮书结果导出；
- 面向竞赛展示的只读评测会话、持久化演示会话和服务器配置。

系统只使用项目现有的冻结数据，不提供实时行情、账户操作、投资交易或完整工商股权查询。

## 系统架构

```text
CLI / Next.js GUI / 评测 Runner
                |
                v
        FastAPI 或本地入口
                |
                v
      LangGraph Agent 工作流
  请求解析 -> 可回答性 -> 路由与规划
      -> 工具执行 -> 证据审查 -> 回答
                |
       +--------+---------+
       |                  |
       v                  v
  五类只读工具       Qwen LLM Skills
       |          解析/规划/审查/回答/摘要
       v
 data/indexes/ 冻结索引
                |
                v
 runtime/fintrace.sqlite3
 会话、轨迹、证据与评测运行状态
```

CLI、GUI 和评测 Runner 最终复用同一个 `run_agent()` / `stream_agent()`，不会形成三套不同的 Agent 逻辑。

## 在线工作流

```text
用户请求
  -> load_session                 恢复多轮会话与摘要
  -> resolve_request              解析主体、时间、任务和筛选条件
  -> check_pre_answerability      判断可路由、部分可回答或超出边界
  -> route_mode
       -> direct                  简单问题生成确定性单工具动作
       -> investigation           LLM Planner 或规则队列逐步规划
  -> validate_action              按工具 Schema 校验参数并防止前视、重复调用
  -> repair_action                对可修复的非法动作执行一次有界修复
  -> execute_one_tool             执行一个工具动作
  -> validate_tool_result         区分成功、可重试和不可重试失败
  -> merge_evidence               合并并去重本轮证据
  -> review_evidence              判断证据是否充分
       -> plan_next_action        仍有信息增益时继续调查
       -> generate_answer         只基于证据生成回答
  -> persist_session              保存多轮上下文
  -> persist_run                  保存节点、工具、证据和 LLM 轨迹
```

工作流对工具调用次数、重复动作和无新增证据轮数设置预算。界面展示的是可审计的节点与工具轨迹，不是模型的私有思维链。LLM 调用失败时系统返回明确错误，不使用确定性模板伪装成模型回答。

## 工具与数据

| 工具 | operation | 功能 | 在线索引 |
| --- | --- | --- | --- |
| `financial_analysis` | `metric_query` | 查询公司及报告期的财务指标 | `financial_metrics.sqlite` |
|  | `metric_compare` | 单公司跨期或多公司单期比较 | 同上 |
|  | `risk_scan` | 执行版本化财务风险规则 | 同上 |
| `ownership_analysis` | `holding_query` | 正向、反向或交叉查询主要股东快照 | `ownership_holdings.sqlite` |
|  | `holding_compare` | 比较两个时点的进入、退出和增减持 | 同上 |
|  | `penetration` | 在已确认实体关系内搜索有限持股路径 | 同上及 `entity_master.sqlite` |
| `document_search` | `search` | 对公告与研报 Chunk 进行 FTS5 + FAISS 混合检索 | `fintrace_kb.sqlite`、`bm25_index.sqlite`、`vector.faiss` |
| `event_timeline` | `event_query` | 按主体、日期和类型查询事件 | `events.sqlite` |
|  | `event_cluster` | 按明确关联依据聚合事件记录 | 同上 |
| `research_analysis` | `view_query` | 查询带机构、日期和来源的研报观点 | `research_views.sqlite` |

详细参数、返回值和边界见各工具说明：[财务分析](tools/financial_analysis/README.md)、[股权分析](tools/ownership_analysis/README.md)、[文档检索](tools/document_search/README.md)、[事件脉络](tools/event_timeline/README.md)、[研报观点](tools/research_analysis/README.md)。

## 目录结构

```text
FinTrace/
├─ app/                         CLI 与 FastAPI 用户入口
│  ├─ cli.py                    单轮及交互式命令行
│  ├─ cli_render.py             可读执行轨迹渲染
│  └─ api/main.py               HTTP、SSE、运行记录及展示会话接口
├─ harness/                     在线 Agent 编排
│  ├─ graph/                    LangGraph 节点、条件边和工作流
│  ├─ routing/                  请求解析、能力路由、规划和动作校验
│  ├─ evidence/                 证据账本与充分性审查
│  ├─ guards/                   工具结果校验和失败分类
│  ├─ memory/                   多轮会话、消息窗口与滚动摘要
│  └─ tracing/                  用户、会话和可观测性记录
├─ schemas/                     跨层 Pydantic 数据契约
├─ tools/                       五类在线只读工具及统一注册入口
├─ prompts/                     版本化解析、规划、审查、回答和摘要提示词
├─ data_pipeline/               离线清洗、切分、实体统一和索引构建
├─ data/
│  ├─ source/                   原始或补充数据
│  ├─ normalized/               标准化数据
│  ├─ processed/                Document、Chunk 等中间产物
│  └─ indexes/                  在线工具直接读取的冻结索引
├─ runtime/                     本地统一可变数据库
├─ evaluation/                  问题集、批次 Runner、分析代码和最终结果
├─ fintrace-frontend/           Next.js 展示前端
├─ deployment/                  展示种子库、Nginx 与 systemd 配置
├─ tests/                       单元、集成、工作流与部署测试
├─ docs/                        竞赛、架构、数据、评测和交付文档
├─ backups/                     迁移恢复材料，不参与正常运行
├─ .env.example                 环境变量模板
├─ requirements.txt             Python 依赖
└─ pytest.ini                   测试配置
```

`data_pipeline/` 主要服务于离线构建，但在线文档查询仍复用 `documents/embedding_client.py` 生成查询向量，实体解析也复用 `entity_alias/build_index.py` 中的名称标准化函数。因此最小部署不能直接删除这两个依赖。

各部署目录中的 `README.txt` 提供简短的文件用途说明；`docs/` 保存详细设计，不参与在线执行。

## 代码审查顺序

1. [schemas/agent_state.py](schemas/agent_state.py) 与 [schemas/request.py](schemas/request.py)：状态和请求契约。
2. [harness/graph/workflow.py](harness/graph/workflow.py)：统一入口和图拓扑。
3. [harness/graph/nodes.py](harness/graph/nodes.py)：节点如何修改状态。
4. [harness/routing/](harness/routing/)：实体、时间、可回答性、路由和规划。
5. [harness/guards/validation.py](harness/guards/validation.py) 与 [harness/evidence/](harness/evidence/)：失败分类和证据治理。
6. [tools/registry.py](tools/registry.py) 与五个工具的 `interface.py`：工具边界和分发。
7. [harness/skills.py](harness/skills.py)、[harness/prompts.py](harness/prompts.py) 与 [prompts/](prompts/)：LLM 输入、输出 Schema 和提示词版本。
8. [harness/memory/](harness/memory/) 与 [harness/tracing/](harness/tracing/)：记忆、会话和运行记录。
9. [tests/](tests/)：通过测试确认边界条件和预期行为。

源码、自动化测试和冻结索引代表当前实现；设计文档用于解释目标和验收口径。

## 环境配置

推荐使用现有 `FinTrace` Python 环境：

```powershell
F:\conda_envs\FinTrace\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` 不应提交到版本库。最终回答模型和规划模型分别配置，二者可以使用同一模型与密钥。

```dotenv
QWEN_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.7-max-2026-05-20
QWEN_MAX_OUTPUT_TOKENS=4096

QWEN_PLANNER_API_KEY=
QWEN_PLANNER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_PLANNER_MODEL=qwen3.7-max-2026-05-20
QWEN_PLANNER_MAX_OUTPUT_TOKENS=2048

FINTRACE_KNOWLEDGE_CUTOFF=2026-05-28
FINTRACE_RUNTIME_DB=runtime/fintrace.sqlite3
FINTRACE_EVAL_LOG_ENABLED=true
```

文档检索还需要同步查询 Embedding 密钥。完整的模型、Embedding、索引、展示模式和服务地址配置见 [.env.example](.env.example)。

## 本地运行

### CLI

```powershell
# 单轮查询
F:\conda_envs\FinTrace\python.exe -m app.cli "600519.SH 2024年营业收入是多少" --trace

# 交互式多轮对话
F:\conda_envs\FinTrace\python.exe -m app.cli --interactive --trace
```

交互命令包括 `/status`、`/trace on|off`、`/debug on|off`、`/clear`、`/help`、`exit` 和 `quit`。

### FastAPI

```powershell
F:\conda_envs\FinTrace\python.exe -m app.api.main
```

- 服务地址：`http://127.0.0.1:8000`
- 健康检查：`GET /health`
- Swagger：`http://127.0.0.1:8000/docs`

CLI 也可通过 FastAPI 调用同一工作流：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli `
  --api-url http://127.0.0.1:8000 `
  "查询贵州茅台的重要监管事件" --trace
```

### Web 前端

后端启动后，在另一个终端运行：

```powershell
Set-Location fintrace-frontend
npm install
npm run dev
```

访问 `http://127.0.0.1:3000`。前端通过 Next.js 服务端 Route Handler 调用 FastAPI，并以 SSE 展示请求解析、执行节点、工具结果、证据和回答增量。

## API 概览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 服务健康检查 |
| `POST` | `/chat` | 非流式执行一轮 Agent |
| `POST` | `/chat/stream` | 通过 SSE 流式执行一轮 Agent |
| `GET` | `/runs` | 分页查询运行摘要 |
| `GET` | `/runs/{run_id}` | 查询工具、证据、节点和 LLM 记录 |
| `GET` | `/showcase/sessions` | 获取展示工作区会话列表 |
| `GET` | `/showcase/sessions/{session_id}` | 分页恢复展示会话详情 |
| `PATCH` | `/showcase/sessions/{session_id}` | 重命名可写演示会话 |
| `DELETE` | `/showcase/sessions/{session_id}` | 删除可写演示会话 |

最终评测会话在展示种子库中标记为只读，不能重命名、删除或追加消息；现场新建的演示会话可以持续保存，不会自动清理。

## 运行时数据

本地默认数据库为 `runtime/fintrace.sqlite3`。它统一保存会话记忆、Agent 运行、工具调用、证据、节点事件、LLM 元数据和评测批次状态。前端只通过 API 访问数据，不直接读取 SQLite；JSONL 仅在评测交换时按需导出。

```powershell
F:\conda_envs\FinTrace\python.exe -m harness.tracing.export_jsonl evaluation\exports\agent_runs.jsonl
```

数据库结构和维护规则见 [runtime/README.md](runtime/README.md)。

## 线上展示部署

线上版本采用单一展示工作区，不实现注册登录和多租户：

```text
浏览器
  -> Nginx HTTPS + Basic Auth
  -> Next.js 127.0.0.1:3000
  -> 服务端代理附加 X-FinTrace-Internal-Key
  -> FastAPI 127.0.0.1:8000
  -> Qwen 与本地冻结索引
```

FastAPI 不直接暴露公网；展示模式下除 `/health` 外的请求都必须携带内部密钥。Nginx 只反向代理前端，并通过 Basic Auth 提供简单访问密码。

部署内容分为两部分：

- 代码和配置通过 Git 上传：`app/`、`harness/`、`schemas/`、`tools/`、`prompts/`、必要的 `data_pipeline/`、`fintrace-frontend/`、`deployment/` 和依赖文件；
- 大型冻结索引通过本地传输上传：`data/indexes/`。

服务器约定路径：

```text
/opt/fintrace/current/                     当前代码
/opt/fintrace/venv/                        Python 环境
/opt/fintrace/shared/fintrace-showcase-seed.sqlite3
/var/lib/fintrace/fintrace.sqlite3          持久化运行库
/etc/fintrace/fintrace.env                  服务环境变量
```

`deployment/bootstrap_showcase.py` 只在运行库不存在时复制种子库，重启和代码更新不会覆盖已保存的演示会话。当前种子库包含最终评测批次的 35 个会话和 1410 轮结果，并附带 Manifest 与 SHA-256 校验文件。

服务模板位于 `deployment/nginx/` 和 `deployment/systemd/`。生产发布前需要执行 `npm ci`、`npm run build`，并确认域名、TLS 证书、Basic Auth 密码、内部 API 密钥、Qwen 密钥和索引路径均已替换为服务器配置。

## 离线索引构建

在线工具不会临时解析原始比赛文件或重建索引。主要构建入口为：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.financial.build_index
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.build_index
F:\conda_envs\FinTrace\python.exe -m data_pipeline.ownership.build_index
F:\conda_envs\FinTrace\python.exe -m data_pipeline.events.build_index
F:\conda_envs\FinTrace\python.exe -m data_pipeline.research_views.build_index
```

文档向量使用 Qwen Embedding Batch 预先生成，再构建 SQLite、FTS5 和 FAISS 索引。完整构建方式见 [数据资产与离线构建](docs/02-数据资产与离线构建.md) 与 [文本检索设计](docs/03-文本语料、Document、Chunk与检索索引.md)。

## 测试与评测

```powershell
F:\conda_envs\FinTrace\python.exe -m pytest -q
```

当前基线为 **316 passed**（2026-08-29）。测试覆盖数据契约、路由、工具、记忆、工作流、SSE、可观测性、评测 Runner、展示部署和离线构建。

正式评测批次：

```text
batch_id       EVAL-20260825T204302Z-FA59F317-2CB3
问题数         1410
会话数         35
知识截止日     2026-05-28
批次状态       completed
```

最终结果位于 `evaluation/results/EVAL-20260825T204302Z-FA59F317-2CB3/`。其中 `run_summary.json` 保存批次概况，`table_metrics.json` 保存汇总指标，`whitepaper_tables.md` 保存白皮书表格来源。

评测方法和论文式表述分别见 [Agent 评测实施方案](docs/09-Agent评测实施方案与结果模板.md) 与 [金融智能体评测体系与实验设计](docs/10-金融智能体评测体系与实验设计.md)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [00-竞赛要求与验收口径](docs/00-竞赛要求与验收口径.md) | 任务目标、指标与交付边界 |
| [01-系统目标能力与数据边界](docs/01-系统目标能力与数据边界.md) | 可回答范围和数据限制 |
| [02-数据资产与离线构建](docs/02-数据资产与离线构建.md) | 数据产品、索引与血缘 |
| [03-文本语料、Document、Chunk与检索索引](docs/03-文本语料、Document、Chunk与检索索引.md) | 文档检索技术设计 |
| [04-在线Agent、记忆、路由与证据工作流](docs/04-在线Agent、记忆、路由与证据工作流.md) | 在线控制流与证据治理 |
| [05-评测、金标与人工标注规范](docs/05-评测、金标与人工标注规范.md) | 数据集标注说明 |
| [06-交付、部署、演示与答辩说明](docs/06-交付、部署、演示与答辩说明.md) | 交付和部署原则 |
| [07-风险扫描、事件脉络与股权穿透开发计划](docs/07-风险扫描、事件脉络与股权穿透开发计划.md) | 三个专项工具的实现边界 |
| [08-统一评测清单与实施记录](docs/08-统一评测清单与实施记录.md) | 评测任务和实施记录 |
| [09-Agent评测实施方案与结果模板](docs/09-Agent评测实施方案与结果模板.md) | 指标口径和操作手册 |
| [10-金融智能体评测体系与实验设计](docs/10-金融智能体评测体系与实验设计.md) | 技术白皮书实验章节 |

## 审查重点

- `knowledge_cutoff` 是否由工作流统一注入并阻止前视；
- 当前问题中的明确主体是否优先于历史会话主体；
- 工具名、operation 和参数是否经过能力白名单与 Schema 校验；
- 工具失败、空结果和证据不足是否被正确区分；
- 财务计算、持股比例和日期筛选是否由程序执行；
- 最终回答是否只引用本轮可用证据；
- 风险信号、研报观点和有限持股路径是否保持正确语义边界；
- LLM 失败时是否明确报错且不生成无证据兜底答案；
- CLI、FastAPI、GUI 和评测 Runner 是否复用同一工作流和 SQLite 记录格式。
