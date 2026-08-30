# FinTrace 竞赛提交包部署说明

本文面向赛事组委会和技术评审人员，用于从 `FinTrace-Submission.tar.gz` 复现并运行 FinTrace。提交包包含在线运行代码、前端源码和全部冻结索引，不包含 API Key、历史会话、评测结果、Python 环境、Node.js 依赖及运行数据库。

## 1. 运行条件

推荐环境如下：

| 项目 | 建议配置 |
| --- | --- |
| 操作系统 | Linux x86_64；亦可使用 Windows 10/11 |
| Python | 3.12 |
| Node.js | 20 或 22，最低不低于 18.18 |
| 内存 | 8 GB 及以上 |
| 可用磁盘 | 8 GB 及以上 |
| 网络 | 能够访问配置的大模型与 Embedding API |
| 默认端口 | 前端 3000，后端 8100 |

Linux 环境已在 Alibaba Cloud Linux 4 上验证，Windows 环境可使用 Conda 和 PowerShell。评审机器不需要安装 Nginx、数据库服务器、Neo4j 或 Docker。

## 2. 解压与完整性检查

将压缩包放到希望安装的位置。Linux 示例：

```bash
mkdir -p /workspace
tar -xzf FinTrace-Submission.tar.gz -C /workspace
cd /workspace/fintrace
```

Windows PowerShell 示例：

```powershell
New-Item -ItemType Directory -Force C:\workspace | Out-Null
tar -xzf .\FinTrace-Submission.tar.gz -C C:\workspace
Set-Location C:\workspace\fintrace
```

解压后项目根目录应包含：

```text
app/                  FastAPI 接口
data/indexes/         文档、财务、股权、事件和研报索引
data_pipeline/        在线查询复用函数
deployment/           空库初始化与服务器部署配置
fintrace-frontend/    Next.js 前端
harness/              Agent 工作流、路由、证据和记忆
prompts/              版本化提示词
schemas/              数据契约
tools/                五类金融工具
.env.example          环境变量模板
requirements.txt      Python 依赖
SHA256SUMS.txt         索引文件校验值
```

在 Linux 下校验 29 个索引文件：

```bash
sha256sum -c SHA256SUMS.txt
```

所有条目均应显示 `OK`。若任一索引缺失或校验失败，应重新解压或重新获取提交包，不应继续运行。

## 3. 安装后端环境

### 3.1 Conda

Linux：

```bash
cd /workspace/fintrace
conda create -y --prefix .conda-env python=3.12
./.conda-env/bin/python -m pip install --upgrade pip
./.conda-env/bin/pip install -r requirements.txt
```

Windows PowerShell：

```powershell
Set-Location C:\workspace\fintrace
conda create -y --prefix .conda-env python=3.12
.\.conda-env\python.exe -m pip install --upgrade pip
.\.conda-env\python.exe -m pip install -r requirements.txt
```

如访问 PyPI 较慢，可以增加阿里云镜像参数：

```text
-i https://mirrors.aliyun.com/pypi/simple/
```

验证关键依赖：

```bash
./.conda-env/bin/python -c "import fastapi, langgraph, faiss, numpy; print('backend dependencies: ok')"
```

Windows 将 Python 路径替换为 `.\.conda-env\python.exe`。

## 4. 配置模型与运行路径

复制环境变量模板：

Linux：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

编辑项目根目录下的 `.env`，至少填写：

```dotenv
QWEN_API_KEY=评审环境可用的Qwen密钥
QWEN_PLANNER_API_KEY=评审环境可用的Qwen密钥
DASHSCOPE_EMBEDDING_API_KEY=评审环境可用的Embedding密钥
```

回答模型和规划模型可以使用同一个密钥。若使用阿里云百炼 Token Plan，应同步填写对应的 `QWEN_BASE_URL`、`QWEN_PLANNER_BASE_URL` 和可用模型名称；Embedding 默认使用 `text-embedding-v4`。

评审本地运行时，将以下配置改为：

```dotenv
FINTRACE_RUNTIME_DB=runtime/fintrace.sqlite3
FINTRACE_DEPLOYMENT_MODE=local
FINTRACE_API_BASE_URL=http://127.0.0.1:8100
FINTRACE_API_HOST=127.0.0.1
FINTRACE_API_PORT=8100
FINTRACE_INTERNAL_API_KEY=
```

提交包不包含真实密钥。请勿将填写后的 `.env` 上传、提交或公开。

## 5. 初始化空运行库

提交包不包含历史会话和评测记录。首次运行前初始化空数据库：

Linux：

```bash
./.conda-env/bin/python -m deployment.bootstrap_showcase --runtime runtime/fintrace.sqlite3
```

Windows PowerShell：

```powershell
.\.conda-env\python.exe -m deployment.bootstrap_showcase --runtime runtime\fintrace.sqlite3
```

首次执行应输出：

```text
initialized
```

再次执行应输出 `preserved`，表示已有会话不会被覆盖。运行数据库保存于 `runtime/fintrace.sqlite3`。

## 6. 启动后端

Linux：

```bash
cd /workspace/fintrace
./.conda-env/bin/python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8100
```

Windows PowerShell：

```powershell
Set-Location C:\workspace\fintrace
.\.conda-env\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8100
```

另开终端验证：

```bash
curl http://127.0.0.1:8100/health
```

预期返回：

```json
{"status":"ok"}
```

## 7. 安装并启动前端

确认 Node.js 版本：

```bash
node --version
npm --version
```

安装依赖并构建：

```bash
cd fintrace-frontend
npm ci
npm run build
npm start -- --hostname 127.0.0.1 --port 3000
```

若 npm 官方源访问较慢，可以先执行：

```bash
npm config set registry https://registry.npmmirror.com
```

浏览器访问：

```text
http://127.0.0.1:3000
```

首次进入时会话列表为空，这是提交包未携带评测数据的正常结果。新建对话并发送问题后，会话、工具调用、证据和执行轨迹将写入运行数据库；刷新页面或重启服务后仍可恢复。

## 8. 建议验收流程

建议依次执行以下检查：

1. 后端 `/health` 返回 `ok`。
2. 前端首页可以打开并新建会话。
3. 输入公司名称或股票代码后，界面展示主体与时间解析结果。
4. 财务问题可以调用 `financial_analysis`。
5. 股东问题可以调用 `ownership_analysis`。
6. 公告与研报问题可以调用 `document_search` 或 `research_analysis`。
7. 事件问题可以调用 `event_timeline`。
8. 回答页面可以展开工具结果、证据和执行轨迹。
9. 刷新页面后新建会话仍然存在。

可使用以下示例问题：

```text
分析一下600519.SH在2024年的财务风险
查询贵州茅台的重要股东及持股变化
查询贵州茅台2020年至2024年的重要事件
检索与贵州茅台相关的公告和研报观点
```

系统只基于提交包内冻结数据和工具证据回答。数据不足、工具失败或模型调用失败时，系统会明确提示，不使用确定性模板补造结论。

## 9. 常见问题

### 9.1 页面可以打开，但不能生成回答

检查 `.env` 中的模型密钥、Base URL 和模型名称是否属于同一服务，并确认评审机器能够访问相应 API。

### 9.2 文档检索失败

先执行 `sha256sum -c SHA256SUMS.txt`。同时确认 `DASHSCOPE_EMBEDDING_API_KEY` 可调用 `text-embedding-v4`，且维度保持为 1024。

### 9.3 返回 401

评审本地运行应设置：

```dotenv
FINTRACE_DEPLOYMENT_MODE=local
```

`showcase` 模式用于公网部署，需要前端和后端共享 `FINTRACE_INTERNAL_API_KEY`。

### 9.4 端口被占用

使用其他空闲端口启动后端时，需要同步修改 `FINTRACE_API_BASE_URL`；更改前端端口只需修改 `npm start` 的 `--port` 参数。

### 9.5 首次进入没有历史会话

这是预期行为。竞赛提交包只提供可运行系统和冻结知识索引，不携带开发阶段的评测会话与运行轨迹。

## 10. 可选服务器部署

若需长期运行，可参考 `deployment/README.md` 使用 systemd 和 Nginx。该部分不是组委会本地复现的必要条件，本地验收不需要域名、证书或公网端口。
