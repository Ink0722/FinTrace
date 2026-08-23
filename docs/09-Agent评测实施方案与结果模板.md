# FinTrace Agent 评测实施方案与结果模板

本文给出 FinTrace 的正式评测口径、人工标注表、计算公式和结果模板，可直接作为技术白皮书“实验设计与评测”章节的底稿。竞赛阈值以 [00-竞赛要求与验收口径.md](00-竞赛要求与验收口径.md) 为准；问题集字段和标注纪律以 [05-评测、金标与人工标注规范.md](05-评测、金标与人工标注规范.md) 为准；实际批次、失败案例和结果登记在 [08-统一评测清单与实施记录.md](08-统一评测清单与实施记录.md)。

> 本文定义“怎样测”，不宣称当前已经达标。只有冻结代码、数据、Prompt、模型和评测集后生成的正式报告，才能写入达标结论。

## 1. 评测目标与分层

评测分为三层，三层结果不得互相替代：

| 层级 | 评测对象 | 回答的问题 | 典型指标 |
| --- | --- | --- | --- |
| Agent 端到端 | 从自然语言输入到最终回答 | 系统是否理解问题、选对工具并给出可信回答 | 回答准确率、关键事实 Recall、工具调用 Precision、自纠错成功率 |
| 工具专项 | 不经过最终回答，直接调用工具 | 确定性查询和计算本身是否正确、及时 | 股权路径准确率、事件节点 Recall、风险预警 F1、工具延迟 |
| 报告质量 | 基于冻结工具证据生成的财务研判 | 表述是否专业、自洽、可追溯且不过度断言 | 专家盲评优秀率、引用支持率、数值准确率 |

正式验收只使用第 3 节的九项竞赛指标。第 4 节的通用金融 Agent 指标用于定位问题，不额外包装成竞赛门槛。

## 2. 正式运行前的冻结协议

每次正式评测创建唯一 `evaluation_run_id`，随后冻结以下内容。中途变更任何一项，必须新建批次，不能把两次结果合并。

### 2.1 运行配置表

| 字段 | 填写值 |
| --- | --- |
| evaluation_run_id |  |
| 运行日期 |  |
| Git commit |  |
| Python / 依赖锁定版本 |  |
| 主回答模型及参数 |  |
| Planner 模型及参数 |  |
| Prompt manifest / 各 Prompt 版本 |  |
| `knowledge_cutoff` |  |
| 问题集路径、版本、SHA-256 |  |
| 数据与索引 manifest |  |
| 风险规则版本 |  |
| 实体映射版本 |  |
| 操作系统、CPU、内存、磁盘 |  |
| 网络条件 |  |
| 随机种子 / 重复次数 |  |

### 2.2 数据集清点表

| 子集 | 样本单位 | 总数 | 开发集 | 测试集 | 正样本 | 负样本 | 数据不足 | 备注 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 多轮问答 | Turn / Session |  |  |  |  |  |  |  |
| 0.5M 长上下文压力集 | Session |  |  |  |  |  |  |  |
| 工具路由 | Turn |  |  |  |  |  |  |  |
| 自纠错故障注入 | Fault case |  |  |  |  |  |  |  |
| 股权穿透 | Query case |  |  |  |  |  |  | 深度必须大于 3 |
| 事件时间线 | Company-window case |  |  |  |  |  |  |  |
| 财务风险 | Company-period-risk row |  |  |  |  |  |  |  |
| 财务报告盲评 | Report |  |  |  |  |  |  |  |

现有 `evaluation/questions/questions_annotated_v1.jsonl` 继续作为问题与基础标注的唯一主表：`required_entities`、`required_date`、`valid_tools` 和 `required_chunk_ids` 分别服务于实体/时间核对、允许的工具范围和证据定位。关键点、故障注入、股权路径、事件节点、风险标签和专家评分使用下文的独立表，并通过 `case_id` 关联；不把所有专项金标重新塞回问题 JSONL。

开发集可用于规则、阈值和 Prompt 调整；冻结测试集只运行一次。发现数据错误时先记录勘误，再决定整批重跑，禁止只重跑失败样本。

## 3. 九项竞赛指标

### 3.1 回答准确率

**样本单位：** 一个 Turn。现有 35 个 Session 必须按 `turn_id` 从小到大完整回放，不能把后续问题拆成单轮测试。

#### 正确边界

每轮先按问题集的 `answerability` 判定预期行为，再使用下表给出二元标签 `pass/fail`：

| 金标状态 | 判为 `pass` 的必要条件 |
| --- | --- |
| `answerable` | 回答了核心问题；主体、期间、口径、数值或方向正确；关键结论均有本轮可用证据；没有越过 `knowledge_cutoff`。 |
| `unanswerable` | 明确说明缺少哪类数据或能力；没有编造确定答案；可以给出已有信息，但必须和无法确认的部分分开。 |
| `clarification_required` | 给出当前参数下仍可安全提供的部分结果，并指出缺失参数及其影响；仅当完全无法安全执行时才只提澄清问题。 |

以下任一情况属于**实质错误**，整轮为 `fail`：答错公司或期间；核心数值、单位、正负号或增减方向错误；把研报观点写成已核实事实；把风险信号写成造假结论；引用不存在或不支持结论的证据；泄露截止日之后的信息；应当拒答却编造；有证据且可回答却无理由拒答。

不影响结论的措辞、排版、非核心背景遗漏属于轻微问题，不单独把整轮判错。这样可以防止“文风评分”污染准确率。

#### 人工与 LLM 双评

1. 两名评估者独立判分：一名金融标注者和一个冻结版本的 LLM Judge；二者都只能看到问题、允许的对话历史、金标关键点和证据，不能看到对方结果。
2. 两者一致时直接采用；不一致时由第二名金融标注者仲裁，形成 `final_label`。
3. 正式准确率使用仲裁后的 `final_label`；同时报告人工原始准确率、LLM 原始准确率、一致率和 Cohen's kappa。
4. 先用不少于 100 个双人已判样本校准 LLM Judge。若 kappa 小于 0.70，LLM 只能用于辅助筛查，正式测试集全部改为双人工判定。

公式：

```text
Answer Accuracy = final_label=pass 的 Turn 数 / 纳入评测的 Turn 总数
```

目标：`>= 90%`。另按 `answerability`、任务类型、轮次区间分别报告，防止大量简单拒答掩盖可回答问题的错误。

#### 回答判分表

| case_id | session_id | turn_id | answerability | human_label | llm_label | final_label | error_code | adjudicator | notes |
| --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |

推荐 `error_code`：`ENTITY`、`TIME`、`NUMBER`、`DIRECTION`、`UNSUPPORTED_CLAIM`、`CUTOFF`、`WRONG_REFUSAL`、`MISSED_CORE_ANSWER`、`SEMANTIC_BOUNDARY`、`OTHER`。

### 3.2 关键事实 Recall

Chunk 是证据载体，关键事实是证据支持的原子命题，两者不能等同。例如，某公告 Chunk 是证据；“贵州茅台 2024 年营业收入为 X 元”才是可判分事实。

只为需要事实性回答的 Turn 标注**必要关键点**，不穷举材料中的所有事实。一个关键点必须只包含一个可独立判断的命题，并明确主体、时间和口径；数值事实还要记录单位和容差。`required_chunk_ids` 用于证明关键点来源，不直接计作关键事实命中。

命中条件：回答表达了同一命题，且主体、期间、指标口径、数值/方向均正确。只贴出证据编号但没有表达事实，不算命中；表达了错误事实，即使引用了正确 Chunk，也不算命中。

```text
Key Fact Recall = 被回答正确覆盖的金标关键点数 / 金标关键点总数
```

目标：`>= 90%`。该指标按竞赛要求由人工评估；可用程序预填数值比对结果，但不得由关键词匹配直接替代人工判定。

#### 关键点金标表

| fact_id | case_id | 原子关键点 | entity | date/period | value | unit | tolerance | source_chunk_ids | importance | annotator_a | annotator_b | final_label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  | core |  |  |  |

`importance` 正式分母只使用 `core`。关键点必须来自冻结结构化数据或可回溯 Chunk，不允许根据系统答案反向制作金标。

### 3.3 0.5M Tokens、10 轮以上压力协议

现有 35 个多轮 Session 用于真实问题回放；另构造长上下文压力版本，使每个 Session 至少 10 轮，且“累计对话历史 + 可访问材料”达到 0.5M Tokens。固定同一个公开 tokenizer 统计数据集规模，同时单独记录每次 LLM 调用的实际输入 Token；二者不能混写。

压力集应覆盖四类探针：早期主体与指代、早期数值事实、跨轮期间约束、无关话题切换。扩充材料只能来自冻结语料，不能把目标答案重复塞入干扰文本。正式结论使用 0.5M 档，10K、50K、100K、250K 档只画退化曲线。

| session_id | turns | dataset_tokens | max_llm_input_tokens | probe_type | probe_turn | answer_pass | fact_hit | fact_total | notes |
| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: | --- |
|  |  |  |  |  |  |  |  |  |  |

0.5M 档分别计算回答准确率和关键事实 Recall，二者均须 `>= 90%`。若实际没有构造并运行 0.5M 数据，必须写“尚未验证”，不能以模型标称上下文长度代替。

### 3.4 工具调用 Precision

**样本单位：** 实际发生的“逻辑工具调用”。同一 `tool_call_id` 内因网络或 Schema 重试产生的物理请求只算一次；参数发生实质变化并再次执行时算新的逻辑调用。

一次调用只有同时满足以下条件才是 TP：

- 工具及 operation 属于该 Turn 的 `valid_tools`；
- 调用对回答该问题是必要或合理的，而非重复、无关调用；
- 主体、日期/报告期、指标、事件类型、截止日等关键参数正确；
- 对 `unanswerable` 或参数尚不足以安全执行的场景，没有提前查询无关数据。

工具正确但关键参数错误，或重复调用同一请求，均记 FP。漏调不会降低 Precision，因此必须同时报告 Tool Recall 和端到端回答准确率，避免“从不调用工具”获得虚高 Precision。

```text
Tool Call Precision = TP / (TP + FP)
Tool Recall（诊断） = 命中的必要 operation 数 / 金标必要 operation 数
Argument Accuracy（诊断） = 关键参数全部正确的调用数 / 被评调用数
```

正式报告使用全测试集 micro Precision，目标 `>= 92%`；同时按 operation 报告分项结果。

#### 路由复核表

先为每个 Turn 填写最小路由金标。`required_any_of` 表示完成任务至少命中其中一个 operation；允许多条合理路径时可以填多个集合，但不能事后根据系统输出修改。

| case_id | valid_tools | required_any_of | expected_no_call | key_arguments | annotator | status |
| --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |

再逐个复核实际逻辑调用：

| case_id | logical_call_id | expected_valid_tools | actual_tool | actual_operation | necessary | arguments_correct | TP/FP | fp_reason | reviewer |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |

### 3.5 自纠错成功率

自纠错不是“工具失败后返回报错”，而是系统面对**预先定义且可恢复**的故障，能够检测失败、执行受控修复，并最终完成原任务。普通无数据、无权限、API Key 错误和不支持的能力属于不可恢复故障，不进入自纠错成功率分母；它们另测安全停止率。

建议冻结四类故障注入：首次工具调用超时后恢复、Planner 输出非法 operation、关键参数可修复的 Schema 错误、LLM 首次返回非法结构化输出。每个故障案例必须真的触发注入点，否则标为 `invalid_case` 并排除。

成功必须同时满足：识别故障；选择了允许的重试或修复路径；没有超过动作/重试预算；最终取得任务所需证据并正确回答；没有重复副作用或编造结果。

```text
Self-correction Success Rate = 成功恢复的可恢复故障案例数 / 实际触发的可恢复故障案例数
```

目标：`>= 80%`。

#### 故障注入表

| fault_case_id | base_case_id | injection_point | fault_type | recoverable | expected_repair | max_attempts | triggered | detected | repaired | final_task_pass | success | trace_run_id |
| --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |  |  |  |

不可恢复故障另填：

| fault_case_id | fault_type | expected_behavior | stopped_safely | fabricated_output | user_message_clear | result |
| --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |

### 3.6 深度大于 3 的股权穿透端到端准确率

**样本单位：** 一个自然语言穿透查询案例。深度指路径边数，必须至少 4 跳。评测从自然语言主体/时间解析开始，包含 `penetration` 路由、图查询和路径结果组装，不评最终自然语言润色。

一个案例只有在以下内容全部正确时才通过：起点、终点和观察日；路径方向；每一跳主体；每一跳持股比例；累计比例；所有必要路径均被返回；没有把未确认的别名、子公司或额外路径写成确定关系。比例按源数据精度比较；源数据未说明时使用绝对误差 `<= 1e-6`。

```text
Ownership E2E Accuracy = 严格通过的查询案例数 / 深度大于 3 的有效查询案例数
```

目标：`>= 85%`。节点命中率、边命中率和比例正确率只用于诊断，不能替代严格端到端准确率。

现有数据主要是前十大股东快照和经审核的主体桥接，不是完整工商股权图。只把数据能够证明的路径制成金标，并单独报告“可评覆盖率”；不得通过删除系统尚未支持但赛题要求覆盖的有效案例来抬高准确率。

#### 股权路径金标表

| path_case_id | question | source_entity | target_entity | as_of_date | knowledge_cutoff | gold_node_sequence | gold_edge_ratios | cumulative_ratio | depth | source_record_ids | reviewer | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |  |  |  |

#### 股权结果复核表

| path_case_id | parse_correct | route_correct | nodes_correct | directions_correct | edge_ratios_correct | cumulative_correct | required_paths_complete | false_path | strict_pass | error_hop |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |  |

### 3.7 事件关键节点 Recall 与脉络完整性

**样本单位：** 公司 + 时间窗 + 事件类型过滤条件。事件金标必须来自冻结公告并能回溯到源文档或 Chunk。否定性历史描述不标成已发生事件；正文没有明确因果关系时，不制作因果金标。

节点优先用稳定 `event_id` 匹配；无法直接对齐时，以公司、规范事件类型、源文档和日期共同匹配。日期精度必须保持原文口径，不能把只有月份的信息虚构成具体日。

```text
Event Node Recall = 命中的核心金标事件节点数 / 核心金标事件节点总数
```

目标：`>= 85%`。同时报告事件 Precision 和聚类 pairwise F1，防止把大量无关事件全部返回来换取高 Recall。

“因果/时序无明显断裂”采用案例级硬检查：日期顺序无倒置；不返回截止日后的事件；同一事件的 initial/progress/response 阶段不冲突；任何显式因果关系均有来源支持。工具没有生成因果关系时，不因缺少推测性因果链扣分。正式报告列出严重断裂数，验收口径为 `0` 个严重断裂，并同时给出案例通过率。

#### 事件金标表

| event_case_id | company_id | start_date | end_date | event_types | knowledge_cutoff | gold_event_id | event_date | date_precision | event_type | cluster_id | stage | core | source_document_id | source_chunk_ids |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |  |  | yes |  |  |

#### 事件结果复核表

| event_case_id | gold_core | matched_core | returned_total | false_positive | cluster_pair_TP | cluster_pair_FP | cluster_pair_FN | chronology_pass | cutoff_pass | unsupported_causality | severe_break | reviewer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |  |  |  |

### 3.8 股权穿透与事件工具延迟

延迟从工具入口接收到已校验参数开始，到完整 `ToolResult`（含图库/SQLite 查询、计算和结果组装）返回为止。排除自然语言解析、Planner、最终 LLM 回答、前端渲染和离线建库；工具内部若访问外部服务，其网络等待仍计入工具时间。

每个 operation 按有结果/空结果、短/长时间窗、浅/深路径等分层；每类先预热 5 次，再正式运行至少 30 次。只统计参数合法的有效调用，失败率单独报告，不能删除超时样本。

竞赛门槛按每次有效调用判断：`execution_time_ms <= 5000`，达标率应为 100%。P50、P95、最大值用于说明性能分布。

| tool.operation | case_group | runs | success | failures | P50_ms | P95_ms | max_ms | within_5s | hardware | result |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `ownership_analysis.penetration` |  |  |  |  |  |  |  |  |  |  |
| `event_timeline.event_query` |  |  |  |  |  |  |  |  |  |  |
| `event_timeline.event_cluster` |  |  |  |  |  |  |  |  |  |  |

### 3.9 财务风险预警 F1

**样本单位：** 公司 + 目标报告期 + 风险类型。金标判断的是“可观察风险信号”，不是直接判定财务造假。每行由两名金融标注者依据冻结财务记录和截止日前证据独立标注，再仲裁为 `positive`、`negative`、`insufficient_data` 或 `not_applicable`。

- `TP`：金标为 positive，工具也预警该风险类型和期间。
- `FP`：金标为 negative，工具却预警。
- `FN`：金标为 positive，工具未预警。
- `insufficient_data` 和 `not_applicable` 不进入 F1 正负分母，另算状态识别准确率。
- 如果数据足以标为 positive，但系统没有实现该风险类型，必须记 FN，不能改标为数据不足。

```text
Precision = TP / (TP + FP)
Recall = TP / (TP + FN)
Risk F1 = 2 * Precision * Recall / (Precision + Recall)
```

正式指标使用冻结风险类型全集上的 micro F1，目标 `>= 85%`；同时逐风险类型报告 Precision、Recall、F1 和样本数。开发集用于阈值校准，测试集不得调参。风险类型覆盖表必须明确“异常关联交易”等赛题要求是否有字段和规则支撑。

#### 风险金标表

| risk_case_id | company_id | report_period | comparison_periods | knowledge_cutoff | risk_type | gold_label | severity | metric_evidence_ids | supporting_chunk_ids | rationale | annotator_a | annotator_b | final_label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |  |  |  |  |

#### 风险类型覆盖表

| risk_type | 赛题要求 | 数据字段可用 | 规则已实现 | 正样本 | 负样本 | 是否进入正式测试 | 缺口说明 |
| --- | --- | --- | --- | ---: | ---: | --- | --- |
| 利润与经营现金流背离 | 是 |  |  |  |  |  |  |
| 存货与收入背离 | 是 |  |  |  |  |  |  |
| 应收账款与收入背离 | 是 |  |  |  |  |  |  |
| 异常费用或汇兑 | 是 |  |  |  |  |  |  |
| 异常关联交易 | 是 |  |  |  |  |  |  |

### 3.10 财务排雷报告专家盲评优秀率

从冻结测试集中分层抽取报告，去除模型名称、运行版本和开发者信息后随机排序。每份报告由两名具备财务、审计或证券研究背景的专家独立评分；单维相差超过 1 分或优秀结论不一致时，由第三名专家仲裁。

每个维度使用 1 至 5 分：

| 维度 | 5 分 | 3 分 | 1 分 |
| --- | --- | --- | --- |
| 数据与引用 | 数值、期间、单位及证据完全可核验 | 有轻微引用或口径瑕疵，不影响核心结论 | 存在核心数据错误、伪造引用或证据不支持 |
| 逻辑自洽 | 风险信号、计算和结论链条完整，无矛盾 | 主要逻辑成立，但解释不充分 | 推理跳跃、前后矛盾或因果倒置 |
| 金融专业性 | 正确区分风险信号、可能原因和已证实事实 | 基本专业，个别术语或边界不严谨 | 把信号直接写成造假结论等重大越界 |
| 完整与可用性 | 回答核心问题、披露限制，可支持进一步核查 | 核心结论基本完整，但有非关键遗漏 | 遗漏核心风险或无法用于判断 |

“优秀”必须同时满足：四个维度均 `>=4`；平均分 `>=4.0`；不存在核心事实/数值错误、无证据断言、虚假引用、截止日泄露或直接定性造假的硬性失败。

```text
Expert Excellent Rate = 被最终判定为优秀的报告数 / 有效盲评报告总数
```

目标：`>= 80%`。

#### 专家盲评表

| report_id | blind_order | 数据与引用 | 逻辑自洽 | 金融专业性 | 完整与可用性 | hard_fail | excellent | expert_id | comments |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |

## 4. 通用金融 Agent 诊断指标

以下指标参考金融问答、数值推理、工具调用和长上下文评测中的常见做法，用来解释九项正式指标为何成功或失败，不改变竞赛目标。

| 指标 | 测量方法 | 主要定位 |
| --- | --- | --- |
| Answerability Macro-F1 | 对 `answerable/unanswerable/clarification_required` 三类计算 macro F1 | 错误拒答、强答和澄清策略 |
| Citation Precision | 真正支持相邻主张的引用数 / 全部引用数 | 伪引、错引 |
| Evidence Recall@k | Top-k 中命中的 `required_chunk_ids` / 全部必要 Chunk | 文档检索漏召回 |
| 数值准确率 | 主体、期间、指标、单位、数值均正确的数值主张 / 全部数值主张 | 财务计算和单位错误 |
| 截止日合规率 | 无 `knowledge_cutoff` 后信息的案例数 / 截止日案例数 | 前视偏差 |
| 工具任务成功率 | 返回有效结果或正确空结果的调用 / 合法调用 | 工具可靠性 |
| No-call Accuracy | 金标不应调用工具且实际未调用的 Turn / 金标 no-call Turn | 无意义工具调用 |
| 稳定性 | 同一案例固定配置重复运行后结论一致的比例 | LLM 随机性与脆弱性 |
| Agent 端到端延迟 | 用户提交至最终事件的 P50/P95/最大值 | 交互体验，不替代工具 5 秒门槛 |
| 成本 | 每 Turn 的输入/输出 Token、Embedding/API 费用 | 部署预算 |

方法来源只用于选择评测思想，不照搬其数据或阈值：

- [FinQA](https://arxiv.org/abs/2109.00122) 将金融问答中的数值推理拆成可核验程序与执行结果，支持本项目单独报告数值准确性。
- [FinanceBench](https://arxiv.org/abs/2311.11944) 强调开放材料金融问答中的证据检索和答案正确性分离，支持本项目区分 Evidence Recall 与 Answer Accuracy。
- [Berkeley Function Calling Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html) 对函数选择、参数结构和不应调用场景分别评价，支持本项目的工具 Precision、参数准确率和 No-call Accuracy。
- [RULER](https://arxiv.org/abs/2404.06654) 使用检索、多跳和聚合任务诊断长上下文能力，支持本项目按长度档位和探针类型报告退化曲线。

## 5. 标注与质控流程

1. 数据工程人员冻结问题、数据、索引和截止日，只负责生成候选记录，不参与测试答案判分。
2. 金融标注者根据原始证据制作关键点、风险和事件金标。工具当前输出不得作为金标来源。
3. 所有正式金标双人独立标注；冲突由第三人仲裁。保留原始标签和最终标签，不覆盖历史。
4. 先做 50 至 100 条试标，统一实体、日期、单位、事件粒度和风险边界，再开始全量标注。
5. 正式评测脚本只读取 `adjudicated` 金标；`draft/review_required` 不进入分母。
6. 评测完成后按错误类型抽查所有失败案例和不少于 10% 的通过案例，防止计算脚本或 LLM Judge 系统性误判。

### 标注进度表

| dataset | total | draft | double_labeled | conflict | adjudicated | excluded | owner | due_date |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
|  |  |  |  |  |  |  |  |  |

### 排除记录表

| sample_id | dataset | exclusion_reason | evidence | reviewer | approved_by | date |
| --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |

允许的排除原因只包括重复样本、原始数据损坏、金标无法仲裁、故障注入未实际触发。系统答错、工具不支持或执行超时都不能作为排除理由。

## 6. 最终结果表

### 6.1 竞赛指标总表

| 编号 | 正式指标 | 样本数/分母 | 分子 | 结果 | 95% CI | 目标 | 是否达标 | 报告或产物 |
| --- | --- | ---: | ---: | ---: | --- | ---: | --- | --- |
| M1 | 0.5M 多轮回答准确率 |  |  |  |  | >=90% |  |  |
| M2 | 0.5M 多轮关键事实 Recall |  |  |  |  | >=90% |  |  |
| M3 | 工具调用 Precision |  |  |  |  | >=92% |  |  |
| M4 | 自纠错成功率 |  |  |  |  | >=80% |  |  |
| M5 | 深度>3股权穿透端到端准确率 |  |  |  |  | >=85% |  |  |
| M6 | 事件关键节点 Recall |  |  |  |  | >=85% |  |  |
| M7 | 股权/事件工具 5 秒内达标率 |  |  |  |  | 100% |  |  |
| M8 | 财务风险预警 micro F1 |  |  |  |  | >=85% |  |  |
| M9 | 财务报告专家盲评优秀率 |  |  |  |  | >=80% |  |  |

比例指标使用 Wilson 95% 置信区间；F1 使用按案例分层 bootstrap 95% 置信区间。置信区间用于说明不确定性，是否达标仍按预先冻结的点估计口径判定，不在看到结果后更换算法。

### 6.2 诊断指标总表

| 指标 | 全体 | answerable | unanswerable | clarification | 财务 | 股权 | 事件 | 文档 | 备注 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Answerability Macro-F1 |  |  |  |  |  |  |  |  |  |
| Tool Recall |  |  |  |  |  |  |  |  |  |
| Argument Accuracy |  |  |  |  |  |  |  |  |  |
| Citation Precision |  |  |  |  |  |  |  |  |  |
| Evidence Recall@8 |  |  |  |  |  |  |  |  |  |
| 数值准确率 |  |  |  |  |  |  |  |  |  |
| 截止日合规率 |  |  |  |  |  |  |  |  |  |
| 端到端 P95 延迟 |  |  |  |  |  |  |  |  |  |

### 6.3 失败案例表

| sample_id | run_id | failed_metric | error_code | expected | actual | root_cause_layer | severity | remediation | retest_batch |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |

`root_cause_layer` 统一使用：`DATA`、`GOLD`、`ENTITY_TIME_PARSE`、`ROUTING`、`TOOL`、`EVIDENCE_REVIEW`、`ANSWER_GENERATION`、`INFRASTRUCTURE`。一条失败可以有多个诊断标签，但只能有一个主因，便于统计改进优先级。

## 7. 白皮书写作口径

正式白皮书应按“数据集与切分 -> 冻结配置 -> 指标定义 -> 总体结果 -> 分层结果 -> 消融/故障注入 -> 失败分析 -> 局限性”的顺序呈现。必须同时给分子、分母和样本规模，不能只给百分比。

以下表述不应出现：用 `pytest passed` 证明业务准确率；用模型标称窗口证明 0.5M 能力；用规则触发率冒充风险 F1；用单次最快耗时证明 5 秒达标；用检索到 Chunk 证明回答事实正确；把无法覆盖的风险类型从测试集静默删除。

在九项总表全部填完前，统一写：**“当前已完成评测框架与数据留痕能力，竞赛指标尚待冻结测试集正式验证。”**
