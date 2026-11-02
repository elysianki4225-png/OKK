# OKK 部署详解：Caddy 反向代理 + SSL 自动申请（支持 nip.io 纯 IP 证书）

本文档覆盖：**一键部署 → 域名/nip.io 选择 → SSL 自全动申请原理 → 手动部署 → 运维与安全加固**。

---

## 一、服务器要求

| 项目 | 最低要求 |
|---|---|
| 配置 | 1 核 1 GB 内存（实测峰值 < 400MB） |
| 系统 | Ubuntu 20.04+ / Debian 11+ / CentOS 7+（推荐 Ubuntu 22.04） |
| 网络 | 公网 IP，安全组/防火墙放行 **80 + 443** |
| 域名 | 可选——没有域名用 **nip.io** 也能拿到受信任的 SSL 证书 |

> 80 端口必须能从公网访问！Let's Encrypt 的 HTTP-01 验证要用它签发证书。

---

## 二、一键部署（推荐）

```bash
git clone https://github.com/elysianki4225-png/OKK.git
cd OKK

# 情况 A：只有公网 IP —— 用 nip.io 自动生成受信任证书
sudo SITE_DOMAIN=你的公网IP.nip.io CADDY_EMAIL=你的邮箱 bash install.sh

# 情况 B：有自己的域名（A 记录已指向服务器 IP）
sudo SITE_DOMAIN=trade.example.com CADDY_EMAIL=你的邮箱 bash install.sh
```

脚本会自动完成 10 步：系统依赖 → Caddy → 代码 → 运行用户 → venv → 生成随机管理密码 →
systemd 注册并启动 → Caddyfile 渲染 → 防火墙放行 → 健康自检（日志走 journald）。

部署完成后终端会打印 **Web 管理密码**（同时保存在 `/opt/okk/.env` 的 `ADMIN_PASSWORD`）。

---

## 三、什么是 nip.io？为什么它能配"IP 证书"？

Let's Encrypt **不能给裸 IP 签证书**，但可以给域名签。nip.io 提供免费的通配 DNS：

```
203.0.113.5.nip.io      →  解析到 203.0.113.5
app.203.0.113.5.nip.io  →  也解析到 203.0.113.5
```

于是 Caddy 把 `203.0.113.5.nip.io` 当普通域名，自动完成 ACME 验证，拿到**受信任的 HTTPS 证书**。
浏览器地址栏显示锁标志，手机 App 访问也不报不安全。完全免费、无需注册。

步骤：
1. 查你服务器公网 IP（云厂商控制台，或 `curl ifconfig.me`）
2. `sudo SITE_DOMAIN=该IP.nip.io bash install.sh`
3. 浏览器打开 `https://该IP.nip.io` 即可

> nip.io 偶尔解析慢，可改用备选 `sslip.io`（用法完全相同）。
> 自有域名永远是更稳的选择——把域名 A 记录指向 IP 即可。

---

## 四、SSL 自动申请是怎么工作的？

`deploy/Caddyfile` 核心片段：

```caddyfile
{$SITE_DOMAIN:localhost} {
    reverse_proxy 127.0.0.1:8000
    encode gzip zstd
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
    }
}
```

Caddy 的自动 HTTPS 流程（零人工干预）：

1. 启动时发现站点是域名（含 nip.io），自动生成 Let's Encrypt 账户密钥
2. 走 **ACME HTTP-01** 验证（临时监听 80 端口响应挑战）签发证书
3. 证书到期前 **自动续期**（约剩 30 天触发）
4. 自动开启 HTTP→HTTPS 301 跳转、HTTP/2、OCSP stapling
5. 配置变更：`sudo systemctl reload caddy`（不中断服务）

排查证书问题：

```bash
journalctl -u caddy -f                  # 看 ACME 日志
sudo caddy validate --config /etc/caddy/Caddyfile
sudo ss -tlnp | grep -E ':80|:443'      # 确认 80/443 在监听
```

常见失败原因：80 被占用 / 防火墙没放行 / DNS 没生效 / 频繁签发触发限速（每域名每周 50 张）。

---

## 五、手动部署（不用脚本）

```bash
# 1. 依赖
sudo apt update && sudo apt install -y python3 python3-venv git curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
  sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
  sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# 2. 代码与依赖
sudo mkdir -p /opt/okk && sudo git clone https://github.com/elysianki4225-png/OKK.git /opt/okk
cd /opt/okk
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# 3. 配置
cp .env.example .env && nano .env     # 设置 ADMIN_PASSWORD 等
mkdir -p data

# 4. Caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile        # 把 {$SITE_DOMAIN:localhost} 改成你的域名或 IP.nip.io
sudo systemctl reload caddy

# 5. 运行（前台测试）
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# 生产：安装 deploy/okk.service 到 /etc/systemd/system/ 并 enable
```

---

## 六、systemd 服务说明

`deploy/okk.service` 已内置 1 核 1G 保护：

```ini
MemoryMax=680M        # 超内存自动重启，避免整机 OOM
CPUQuota=90%          # 不吃满 CPU，留余量给系统与 Caddy
Restart=always        # 崩溃 8 秒后自动拉起
ProtectSystem=strict  # 文件系统只读保护
```

```bash
systemctl status okk        # 状态
journalctl -u okk -f        # 实时日志
systemctl restart okk       # 重启（改 .env 后）
systemctl stop okk          # 停止（交易立即停止，持仓不变！）
```

---

## 七、安全加固清单（重要！）

- [ ] **改管理密码**：登录 Web → 设置 → 安全 → 修改管理密码
- [ ] **交易所 API**：只开「读取+交易」，**绝不开放提币**，绑定服务器 IP 白名单
- [ ] **防火墙**：只放行 22 / 80 / 443（install.sh 已处理 ufw/firewalld）
- [ ] **SSH**：禁密码登录改用密钥（`/etc/ssh/sshd_config` → `PasswordAuthentication no`）
- [ ] **备份**：`crontab -e` 添加每日备份：
      `0 4 * * * tar czf /root/okk-backup-$(date +\%F).tar.gz /opt/okk/data`
- [ ] **先模拟盘**：`TRADING_MODE=paper` 至少跑 1 周，看 AI 决策审计合理再切 live
- [ ] **监控**：`curl https://你的域名/api/health`（免认证探活），配 UptimeRobot 免费监控

## 八、升级与数据

```bash
cd /opt/okk
sudo systemctl stop okk
git pull
./.venv/bin/pip install -r requirements.txt
sudo systemctl start okk
```

所有持久数据（数据库/密钥/配置）都在 `/opt/okk/data/`，升级不丢。
**`data/master.key` 丢失 = 保存的 API 密钥无法解密**，请务必备份该文件（权限 600）。

---

## 九、反向代理进阶

### 同时用 Webhook 通知（可选）
Web 设置里填「通知 Webhook」即可，兼容：
- 飞书群机器人：`https://open.feishu.cn/open-apis/bot/v2/hook/xxx`
- Telegram Bot（经 Bot API 或转发服务）
- 企业微信机器人：`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx`

### 多站点共存
把 Caddyfile 里的站点块复制一份，改成另一个域名，`reverse_proxy` 到不同端口的实例即可。
