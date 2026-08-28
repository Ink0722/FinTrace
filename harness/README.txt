作用：组织 Agent 的模型调用、提示词、工具执行、流式输出和运行数据库。
主要文件：llm.py 访问 Qwen；skills.py 调用工具；answering.py 生成回答；runtime_db.py 管理运行数据。
子目录：routing、graph、evidence、memory、guards、tracing 分别承担路由、流程、证据、记忆、校验和日志职责。
