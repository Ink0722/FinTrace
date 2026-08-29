# FinTrace Showcase

本分支是 FinTrace 的竞赛提交与线上展示版本，只保留图形界面运行所需的代码、提示词、展示数据和服务器配置。完整的数据预处理、测试、评测和技术文档保存在 `main` 分支的 `bde526b` 提交中。

FinTrace 是面向 A 股研究场景的证据驱动金融 Agent。系统先解析主体、时间和任务，再调用冻结数据上的确定性工具，最后由 Qwen 在工具证据范围内生成回答。证据不足或模型调用失败时会明确提示，不使用无证据模板补造结论。

## 展示能力

- 财务指标查询、跨期比较与风险信号扫描；
- 股东快照、持股变化和有限股权路径穿透；
- 公告与研报文档的 FTS5 + FAISS 混合检索；
- 公司事件查询与事件簇聚合；
- 带来源归属的研报观点查询；
- 多轮会话、滚动摘要、SSE 流式回答和完整工具轨迹；
- 35 个只读评测会话、1410 轮最终评测结果；
- 可持续保存且不自动清理的新建演示会话。

系统不提供实时行情、账户操作、投资交易或完整工商股权查询。

## 运行架构

```text
浏览器
  -> Nginx HTTPS + Basic Auth
  -> Next.js 127.0.0.1:3000
  -> 服务端代理附加 X-FinTrace-Internal-Key
  -> FastAPI 127.0.0.1:8000
  -> LangGraph Agent
       -> 五类只读工具 -> data/indexes/
       -> Qwen Chat / Qwen Embedding
  -> /workspace/fintrace/runtime/fintrace.sqlite3
```

FastAPI 不直接暴露公网。展示模式下除 `/health` 外，所有请求都必须携带前端服务端代理注入的内部密钥。

## 目录结构

```text
app/                         FastAPI 与 SSE 入口
harness/                     LangGraph、路由、证据、记忆和运行轨迹
schemas/                     Pydantic 数据契约
tools/                       五类在线工具
prompts/                     版本化 LLM 提示词
data_pipeline/               在线复用的查询向量与名称标准化函数
fintrace-frontend/           Next.js 图形界面
deployment/                  种子库、Nginx 和 systemd 配置
data/indexes/                冻结索引，需在服务器上单独上传
.env.example                 服务器环境变量模板
requirements.txt             Python 依赖
```

部署代码目录中的 `README.txt` 简要说明各目录及主要文件用途。

## 必需数据

`data/indexes/` 不进入 Git，必须从本地完整上传到服务器项目目录。线上运行需要：

```text
data/indexes/document_search/
  fintrace_kb.sqlite
  bm25_index.sqlite
  vector.faiss
  embeddings.npy
  vector_ids.json
  manifest.json
data/indexes/entity_alias/company_aliases.sqlite
data/indexes/entity_resolution/entity_master.sqlite
data/indexes/event_timeline/events.sqlite
data/indexes/financial_analysis/financial_metrics.sqlite
data/indexes/ownership_analysis/ownership_holdings.sqlite
data/indexes/research_analysis/research_views.sqlite
```

展示种子库已经包含在 `deployment/assets/`，可用于首次初始化。若要保留本地现有的最终评测结果与会话，则将运行数据库单独上传到 `runtime/fintrace.sqlite3`；该文件不进入 Git。

## 环境变量

在服务器上以 [.env.example](.env.example) 为基础创建 `/etc/fintrace/fintrace.env`。至少填写：

```dotenv
QWEN_API_KEY=
QWEN_PLANNER_API_KEY=
DASHSCOPE_EMBEDDING_API_KEY=
FINTRACE_INTERNAL_API_KEY=
```

`QWEN_API_KEY` 用于最终回答，`QWEN_PLANNER_API_KEY` 用于解析、规划、审查、修复和记忆摘要，二者可以填写同一个密钥。`DASHSCOPE_EMBEDDING_API_KEY` 用于在线查询向量。`FINTRACE_INTERNAL_API_KEY` 必须是独立随机值，并同时提供给 FastAPI 与 Next.js 服务。

示例文件已经预设服务器运行库和项目内索引路径：

```dotenv
FINTRACE_RUNTIME_DB=/workspace/fintrace/runtime/fintrace.sqlite3
FINTRACE_DEPLOYMENT_MODE=showcase
FINTRACE_API_BASE_URL=http://127.0.0.1:8000
FINTRACE_API_HOST=127.0.0.1
FINTRACE_API_PORT=8000
FINTRACE_KNOWLEDGE_CUTOFF=2026-05-28
```

## 服务器部署

推荐目录：

```text
/workspace/fintrace/                         当前代码
/workspace/fintrace/.venv/                   Python 环境
/workspace/fintrace/data/indexes/            单独上传的冻结索引
/workspace/fintrace/runtime/fintrace.sqlite3 单独上传的持久化运行库
/etc/fintrace/fintrace.env                  服务环境变量
```

1. 拉取本分支到 `/workspace/fintrace/`。
2. 将本地 `data/indexes/` 上传到 `/workspace/fintrace/data/indexes/`。
3. 将本地运行数据库上传到 `/workspace/fintrace/runtime/fintrace.sqlite3`；若不上传，则首次启动时自动使用项目内展示种子初始化。
4. 创建 Python 环境并安装 `requirements.txt`。
5. 在 `fintrace-frontend/` 执行 `npm ci` 和 `npm run build`。
6. 创建 `/etc/fintrace/fintrace.env` 并填写密钥。
7. 安装 `deployment/systemd/` 下的两个服务单元。
8. 安装并修改 `deployment/nginx/fintrace-showcase.conf` 中的域名和证书路径。
9. 使用 `htpasswd` 创建 `/etc/nginx/fintrace.htpasswd`。
10. 启动 `fintrace-api`、`fintrace-frontend` 和 Nginx。

API 服务启动前会执行 `deployment.bootstrap_showcase`：仅当持久化运行库不存在时复制展示种子；服务器重启和代码更新不会覆盖已有演示会话。

完整命令与验收步骤见 [deployment/README.md](deployment/README.md)。

## 本地预览

本分支默认使用服务器路径和 `showcase` 模式。本地预览时，在 PowerShell 中临时覆盖运行库路径：

```powershell
$env:FINTRACE_RUNTIME_DB="runtime/fintrace.sqlite3"
$env:FINTRACE_DEPLOYMENT_MODE="local"
F:\conda_envs\FinTrace\python.exe -m app.api.main
```

另开终端启动前端：

```powershell
Set-Location fintrace-frontend
npm install
npm run dev
```

访问 `http://127.0.0.1:3000`。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `POST` | `/chat` | 非流式执行一轮 Agent |
| `POST` | `/chat/stream` | SSE 流式执行一轮 Agent |
| `GET` | `/runs` | 查询运行摘要 |
| `GET` | `/runs/{run_id}` | 查询工具、证据、节点和 LLM 详情 |
| `GET` | `/showcase/sessions` | 查询展示会话列表 |
| `GET` | `/showcase/sessions/{session_id}` | 恢复展示会话详情 |
| `PATCH` | `/showcase/sessions/{session_id}` | 重命名可写演示会话 |
| `DELETE` | `/showcase/sessions/{session_id}` | 删除可写演示会话 |

最终评测会话为只读；新建演示会话可以写入、重命名和删除。

## 发布验收

```text
1. GET /health 返回 {"status":"ok"}
2. 域名访问需要 Basic Auth
3. 公网不能直接访问 8000 和 3000 端口
4. 前端可加载 35 个最终评测会话
5. 只读评测会话不能修改或追加消息
6. 新建会话能够流式回答并在刷新后恢复
7. 工具轨迹与文件、非文件证据均可展开查看
8. 重启服务后评测结果和新建会话均不丢失
```

## 完整版本

完整研发版本位于：

```text
branch   main
commit   bde526b
```

其中包含离线数据预处理、索引构建、1410 轮评测工作区、专项实验、316 项自动化测试以及技术白皮书文档。
