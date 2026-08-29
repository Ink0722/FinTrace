# FinTrace 线上展示部署

## 准备目录

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin fintrace
sudo mkdir -p /opt/fintrace/current /opt/fintrace/shared /var/lib/fintrace /etc/fintrace
sudo chown -R fintrace:fintrace /opt/fintrace /var/lib/fintrace
```

代码放在 `/opt/fintrace/current`，大型索引上传到 `/opt/fintrace/current/data/indexes`。将展示种子复制到：

```text
/opt/fintrace/shared/fintrace-showcase-seed.sqlite3
```

可用以下文件核验种子：

```text
deployment/assets/fintrace-showcase-seed.manifest.json
deployment/assets/fintrace-showcase-seed.sha256
```

## 安装依赖

```bash
python3 -m venv /opt/fintrace/venv
/opt/fintrace/venv/bin/pip install -r /opt/fintrace/current/requirements.txt
cd /opt/fintrace/current/fintrace-frontend
npm ci
npm run build
```

## 环境配置

以 `.env.example` 创建 `/etc/fintrace/fintrace.env`，填写 Qwen、Embedding 和内部 API 密钥。文件权限建议设为：

```bash
sudo chown root:fintrace /etc/fintrace/fintrace.env
sudo chmod 640 /etc/fintrace/fintrace.env
```

## 安装服务

```bash
sudo cp deployment/systemd/fintrace-api.service /etc/systemd/system/
sudo cp deployment/systemd/fintrace-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fintrace-api fintrace-frontend
```

API 的 `ExecStartPre` 会在首次启动时初始化 `/var/lib/fintrace/fintrace.sqlite3`。若运行库已经存在，则原样保留。

## 配置公网入口

修改 `deployment/nginx/fintrace-showcase.conf` 中的域名与证书路径，然后执行：

```bash
sudo apt-get install nginx apache2-utils
sudo htpasswd -c /etc/nginx/fintrace.htpasswd fintrace
sudo cp deployment/nginx/fintrace-showcase.conf /etc/nginx/sites-available/fintrace-showcase
sudo ln -s /etc/nginx/sites-available/fintrace-showcase /etc/nginx/sites-enabled/fintrace-showcase
sudo nginx -t
sudo systemctl reload nginx
```

服务器防火墙只开放 80 和 443；8000 与 3000 仅监听 `127.0.0.1`。

## 检查

```bash
curl http://127.0.0.1:8000/health
systemctl status fintrace-api
systemctl status fintrace-frontend
journalctl -u fintrace-api -n 100 --no-pager
journalctl -u fintrace-frontend -n 100 --no-pager
```

浏览器应能读取最终评测会话、新建对话并查看流式工具轨迹。服务重启后，评测数据和新建会话应保持不变。
