# 数据集批量执行器

本目录负责把带有 `session_id/turn_id` 的 JSONL 问题集按原始顺序提交给
FinTrace Agent。Agent 只接收问题、隔离后的会话 ID 和固定信息截止日；金标字段
不会进入 Prompt。

## 1. 准备批次

准备操作只校验并登记数据，不调用工具或大模型：

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.runner.run_dataset prepare `
  --dataset evaluation/questions/questions_annotated_v1.jsonl `
  --agent-version local-dev
```

截止日期默认读取 `.env` 中的 `FINTRACE_KNOWLEDGE_CUTOFF`；需要为某个批次覆盖时，
再显式传入 `--knowledge-cutoff YYYY-MM-DD`。

输出中的 `batch_id` 是后续命令的唯一批次标识。每个批次会建立一个专用本地
用户，并将原始会话映射为 `EVAL-...-SESSION-001`，不会污染普通用户会话。

## 2. 小规模试跑

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.runner.run_dataset run `
  --batch-id <BATCH_ID> --session-id 1 --max-cases 5
```

## 3. 全量与续跑

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.runner.run_dataset run `
  --batch-id <BATCH_ID> --concurrency 2
```

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.runner.run_dataset run `
  --batch-id <BATCH_ID> --concurrency 2 --retry-failed
```

同一会话严格串行，不同会话才允许并发。基础设施异常记为 `failed`；Agent 正常
给出拒答、澄清或证据不足仍记为 `completed`。失败轮次未补齐时不会越过它执行
后续轮次。

## 4. 查看状态

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.runner.run_dataset status `
  --batch-id <BATCH_ID>
```

`evaluation_batches/evaluation_cases` 只保存批次和映射关系。完整回答、工具调用、
证据、工作流节点和 LLM 元数据继续保存在同一个观测数据库，通过 `run_id` 关联。
