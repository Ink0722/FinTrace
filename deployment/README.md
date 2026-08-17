# Deployment

当前部署层提供本地 CLI 和 FastAPI 运行说明。系统主要入口有两个：

```text
CLI      app.cli
FastAPI  app.api.main
```

## 本地 CLI

```powershell
F:\conda_envs\FinTrace\python.exe -m app.cli "分析示例公司的财务风险" --trace
```

工作流：

```text
app.cli.main()
→ print_answer()
→ harness.graph.workflow.run_agent()
→ 格式化 final_answer
→ 如果 --trace：打印执行路径、工具调用、证据
```

## FastAPI

启动：

```powershell
F:\conda_envs\FinTrace\python.exe -m app.api.main
```

接口：

```text
GET  /health
POST /chat
```

`POST /chat` 工作流：

```text
app.api.main.chat()
→ run_agent(query, session_id)
→ AgentState.model_dump()
→ JSON response
```

当前知识库和工具数据均按本地文件读取：

```text
data/indexes/document_search/
data/financial/
data/ownership/
data/events/
```
