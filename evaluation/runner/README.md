# 数据集批量执行器

## 离线预检

离线预检按原会话顺序模拟确定性上下文，只运行实体与时间解析、可回答性判断和首动作预览，不调用工具或大模型：

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.runner.offline_audit `
  --dataset evaluation/questions/questions_annotated_v1.jsonl `
  --knowledge-cutoff 2026-05-28
```

汇总和逐题明细默认写入 `runtime/evaluation/offline_audit/`。首动作与 `valid_tools` 的差异是人工复核线索，不直接作为正式准确率，因为调查模式的真实动作由 Planner 决定，数据标注本身也可能需要修订。

本目录负责把带有 `session_id/turn_id` 的 JSONL 问题集按原始顺序提交给
FinTrace Agent。Agent 只接收问题、隔离后的会话 ID 和固定信息截止日；人工标注字段
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

执行开始后，终端会立即显示批次、会话和当前问题。单轮超过 30 秒时会输出一次心跳；完成后显示回答状态、执行路径、工具数、证据数、LLM 调用数、耗时、总体进度和预计剩余时间。进度写入标准错误流，命令结束时的批次状态 JSON 仍单独写入标准输出流。

## 3. 全量与续跑

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.runner.run_dataset run `
  --batch-id <BATCH_ID> --concurrency 2
```

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.runner.run_dataset run `
  --batch-id <BATCH_ID> --concurrency 2 --retry-failed
```

同一会话严格串行，不同会话才允许并发。基础设施异常，以及 Agent 返回
`answer_status=failed` 或 `workflow_status=failed/llm_failed`，均记为 `failed`。
后者仍保存本次 `run_id`，因此可以审查已经产生的节点、工具与模型调用记录。
Agent 正常给出拒答、澄清或证据不足仍记为 `completed`。失败轮不会提交会话记忆，
也不会越过它执行后续轮次；排除故障后使用 `--retry-failed` 从该轮重试。

## 4. 查看状态

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.runner.run_dataset status `
  --batch-id <BATCH_ID>
```

`evaluation_batches/evaluation_cases` 只保存批次和映射关系。完整回答、工具调用、
证据、工作流节点和 LLM 元数据继续保存在同一个观测数据库，通过 `run_id` 关联。
