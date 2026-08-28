# FinTrace事件脉络外部复核提示词

你是金融公告事件脉络的独立复核员。请逐行读取
`event_llm_review_input.jsonl`，每一行对应一个既有实验案例。不得联网，
不得使用模型记忆补充输入中不存在的事实，也不得新增、删除或改写事件ID。

本次复核只评价现有事件索引中的事件组织质量，不评价系统是否从全部原始公告中
漏抽了事件。系统事件簇仅供比较，不能直接作为参考划分。

对每个案例依次完成以下工作：

1. 先阅读 `events`，识别对理解该公司事件发展过程不可缺少的关键事件，将其
   `event_id` 写入 `key_event_ids`。不要因为事件标题醒目就机械标为关键节点。
2. 根据事件主题、公告文号、涉及主体、事件阶段和时间连续性，独立形成
   `reference_clusters`。每个事件必须且只能出现一次；不能确认有关联时应拆成
   单例簇，不能仅因日期接近就合并。
3. 逐项复核 `system_relations`。只有输入中的标题、摘要、公告文号或阶段能够明确
   支持关系时才写 `supported`；明确不支持时写 `unsupported`；证据不足时写
   `uncertain`。时间先后本身只能支持 `FOLLOWED_BY`，不能证明回复、整改或解决。
4. 若事件顺序或关系方向存在会改变事件发展含义的明显错误，将
   `severe_temporal_break` 设为 true。普通日期精度不足不属于严重断裂。

每个案例仅输出一个JSON对象，每行一个对象，保存到
`event_llm_review_result.jsonl`。不要输出Markdown代码块或额外说明。格式如下：

{
  "case_id": "EVENT-QUALITY-001",
  "company_id": "603377.SH",
  "key_event_ids": ["EVENT-001"],
  "reference_clusters": [
    {
      "reference_cluster_id": "REF-CLUSTER-001",
      "event_ids": ["EVENT-001", "EVENT-002"],
      "reason": "两条记录分别为监管问询及对应回复"
    }
  ],
  "relation_reviews": [
    {
      "source_event_id": "EVENT-002",
      "target_event_id": "EVENT-001",
      "relation_type": "RESPONDS_TO",
      "verdict": "supported",
      "reason": "公告文号与事件阶段相互印证"
    }
  ],
  "severe_temporal_break": false,
  "review_notes": "简短说明判断边界"
}

严格要求：

- `case_id` 和 `company_id` 必须原样返回；
- `key_event_ids` 只能引用该案例 `events` 中的ID，可以为空；
- `reference_clusters` 必须完整覆盖全部事件，且不得重复；
- `reference_cluster_id` 在当前案例内不得重复；
- `relation_reviews` 必须逐条覆盖全部 `system_relations`，不得新增关系；
- `verdict` 只能是 `supported`、`unsupported` 或 `uncertain`；
- 判断不充分时从严选择 `uncertain`，不得猜测因果关系。
