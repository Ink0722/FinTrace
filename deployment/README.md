# FinTrace 线上部署

## 准备目录

将提交压缩包解压为 `/workspace/fintrace`，然后执行：

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin fintrace
sudo mkdir -p /workspace/fintrace/runtime /etc/fintrace
sudo chown -R fintrace:fintrace /workspace/fintrace/runtime
sudo chmod -R a+rX /workspace/fintrace/data/indexes
```

提交包已包含冻结索引，不包含历史会话。首次启动会创建空的 `/workspace/fintrace/runtime/fintrace.sqlite3`，已有运行库不会被覆盖。

## 安装依赖

```bash
conda create -y --prefix /workspace/fintrace/.conda-env python=3.12
/workspace/fintrace/.conda-env/bin/pip install -r /workspace/fintrace/requirements.txt
cd /workspace/fintrace/fintrace-frontend
npm ci
npm run build
sudo chown -R fintrace:fintrace /workspace/fintrace/fintrace-frontend/.next
```

## 环境配置

以 `.env.example` 创建 `/etc/fintrace/fintrace.env`，填写 Qwen、Embedding 和内部 API 密钥：

```bash
sudo install -d -o root -g fintrace -m 750 /etc/fintrace
sudo cp /workspace/fintrace/.env.example /etc/fintrace/fintrace.env
sudo chown root:fintrace /etc/fintrace/fintrace.env
sudo chmod 640 /etc/fintrace/fintrace.env
```

## 安装服务

```bash
sudo cp /workspace/fintrace/deployment/systemd/fintrace-api.service /etc/systemd/system/
sudo cp /workspace/fintrace/deployment/systemd/fintrace-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fintrace-api fintrace-frontend
```

FinTrace 前端监听 `127.0.0.1:3000`，后端监听 `127.0.0.1:8100`。

## 配置公网入口

Alibaba Cloud Linux 4 使用 `/etc/nginx/conf.d/`：

```bash
sudo dnf install -y nginx httpd-tools
sudo htpasswd -c /etc/nginx/fintrace.htpasswd fintrace
sudo cp /workspace/fintrace/deployment/nginx/fintrace-showcase.conf /etc/nginx/conf.d/fintrace-showcase.conf
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

正式启用前应确认域名证书路径与 `deployment/nginx/fintrace-showcase.conf` 一致。服务器防火墙只开放 80 和 443；3000 与 8100 仅监听 `127.0.0.1`。

## 检查

```bash
curl http://127.0.0.1:8100/health
curl -I http://127.0.0.1:3000
systemctl status fintrace-api --no-pager
systemctl status fintrace-frontend --no-pager
journalctl -u fintrace-api -n 100 --no-pager
journalctl -u fintrace-frontend -n 100 --no-pager
```

浏览器应能新建对话、获得流式回答并查看工具轨迹。刷新页面或重启服务后，会话应保持不变。
