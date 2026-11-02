#!/usr/bin/env bash
# ============================================================
#  OKK AI 炒币机器人 · 一键部署脚本
#  支持: Ubuntu / Debian / CentOS / Rocky / AlmaLinux
#
#  用法（root 或 sudo）:
#    1) 有域名:       SITE_DOMAIN=trade.example.com bash install.sh
#    2) 只有 IP:      SITE_DOMAIN=你的公网IP.nip.io bash install.sh
#       例:          SITE_DOMAIN=203.0.113.5.nip.io bash install.sh
#    3) 全默认:       bash install.sh   (之后手动改 /etc/caddy/Caddyfile)
#
#  幂等：可重复执行，已装组件会跳过。
# ============================================================
set -euo pipefail

# ---------- 出错不再静默：打印出错行号并退出 ----------
trap 'rc=$?; echo ""; echo -e "\033[0;31m[错误] 安装在第 $LINENO 行失败（退出码 $rc）\033[0m"; echo -e "\033[0;31m请截图本段输出发给开发者排查\033[0m"; exit $rc' ERR

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[注意]${NC} $*"; }
error() { echo -e "${RED}[错误]${NC} $*"; exit 1; }

[[ $EUID -eq 0 ]] || error "请用 root 运行: sudo bash install.sh"

APP_DIR="${APP_DIR:-/opt/okk}"
SITE_DOMAIN="${SITE_DOMAIN:-}"
CADDY_EMAIL="${CADDY_EMAIL:-admin@example.com}"
REPO_URL="${REPO_URL:-https://github.com/elysianki4225-png/OKK.git}"

echo "=========================================================="
echo "  OKK AI 炒币机器人 一键部署"
echo "  安装目录: $APP_DIR"
echo "  站点域名: ${SITE_DOMAIN:-（稍后配置 /etc/caddy/Caddyfile）}"
echo "=========================================================="

# ---------- 1. 系统依赖 ----------
info "安装系统依赖 ..."
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git curl unzip ufw ca-certificates cron
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q python3 python3-pip git curl unzip firewalld cronie
elif command -v yum >/dev/null 2>&1; then
    yum install -y -q python3 python3-pip git curl unzip firewalld cronie
else
    error "未识别的包管理器，请手动安装 python3 / git / curl / caddy"
fi
info "系统依赖完成"

# ---------- 2. Caddy ----------
if ! command -v caddy >/dev/null 2>&1; then
    info "安装 Caddy ..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
            | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null || true
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
            > /etc/apt/sources.list.d/caddy-stable.list
        apt-get update -qq && apt-get install -y -qq caddy
    else
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/rpm/caddy.repo' \
            > /etc/yum.repos.d/caddy-stable.repo 2>/dev/null || true
        (dnf install -y -q caddy || yum install -y -q caddy) || {
            warn "仓库安装失败，使用官方二进制 ..."
            curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=amd64" -o /usr/bin/caddy
            chmod +x /usr/bin/caddy
        }
    fi
    command -v caddy >/dev/null 2>&1 || error "Caddy 安装失败"
fi
CADDY_VER="$(caddy version 2>/dev/null | head -n1 || echo unknown)"
info "Caddy 就绪: ${CADDY_VER}"

# ---------- 3. 应用代码 ----------
mkdir -p "$APP_DIR"
if [[ ! -f "$APP_DIR/app/main.py" ]]; then
    info "拉取代码 ..."
    if [[ -f "$(dirname "$0")/app/main.py" ]]; then
        # 从本地目录安装（脚本与代码一起分发时）
        cp -r "$(dirname "$0")/." "$APP_DIR/"
        rm -rf "$APP_DIR/.venv" "$APP_DIR/data" 2>/dev/null || true
    else
        git clone --depth 1 "$REPO_URL" "$APP_DIR"
    fi
else
    info "检测到已有代码，跳过拉取（如需更新: cd $APP_DIR && git pull）"
fi

# ---------- 4. 运行用户 ----------
if ! id -u okk >/dev/null 2>&1; then
    useradd -r -M -d "$APP_DIR" -s /bin/bash okk
    info "创建运行用户 okk"
fi

# ---------- 5. Python 虚拟环境 ----------
info "安装 Python 依赖（首次约 2~4 分钟）..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
info "Python 依赖完成"

# ---------- 6. 配置文件 ----------
# 注意：此处生成密码严禁使用 "tr ... | head" 管道（SIGPIPE 会让脚本静默中断）
mkdir -p "$APP_DIR/data"
if [[ ! -f "$APP_DIR/.env" ]]; then
    ADMIN_PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
    cat > "$APP_DIR/.env" <<EOF
HOST=127.0.0.1
PORT=8000
ADMIN_PASSWORD=${ADMIN_PW}
DATA_DIR=${APP_DIR}/data
TRADING_MODE=paper
TZ_NAME=Asia/Shanghai
SCAN_INTERVAL=300
SCAN_TOP_N=25
KLINE_LIMIT=120
LOG_LEVEL=INFO
EOF
    info "已生成配置 $APP_DIR/.env"
else
    warn "已存在 .env，保持不变"
    ADMIN_PW="（见 ${APP_DIR}/.env 中的 ADMIN_PASSWORD）"
fi

chown -R okk:okk "$APP_DIR"
chmod 600 "$APP_DIR/.env"

# ---------- 7. systemd ----------
cp "$APP_DIR/deploy/okk.service" /etc/systemd/system/okk.service
sed -i "s|/opt/okk|${APP_DIR}|g" /etc/systemd/system/okk.service
systemctl daemon-reload
systemctl enable okk >/dev/null 2>&1
systemctl restart okk
info "OKK 服务已启动（systemctl status okk 查看状态）"

# ---------- 8. Caddy 配置（日志走 journald，免文件权限坑）----------
mkdir -p /etc/caddy
render_caddyfile() {
    if [[ -n "$SITE_DOMAIN" ]]; then
        sed "s|{\$SITE_DOMAIN:localhost}|${SITE_DOMAIN}|; s|{\$CADDY_EMAIL admin@example.com}|${CADDY_EMAIL}|" \
            "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
    else
        cp "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
    fi
}
render_caddyfile
if ! CADDY_ERR="$(caddy validate --config /etc/caddy/Caddyfile 2>&1)"; then
    warn "完整版 Caddyfile 校验未通过（常见于旧版 Caddy），原始报错："
    echo "$CADDY_ERR" | sed 's/^/    /'
    warn "自动降级为精简版 Caddyfile（仅保留最核心功能）..."
    cat > /etc/caddy/Caddyfile <<EOF
{
	email ${CADDY_EMAIL}
}
${SITE_DOMAIN:-localhost} {
	encode gzip
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
	}
	reverse_proxy 127.0.0.1:8000
	log
}
EOF
    if ! CADDY_ERR2="$(caddy validate --config /etc/caddy/Caddyfile 2>&1)"; then
        warn "精简版校验报错："
        echo "$CADDY_ERR2" | sed 's/^/    /'
        error "Caddyfile 无法通过校验，请把以上报错发给开发者"
    fi
fi
systemctl enable caddy >/dev/null 2>&1
if ! CADDY_START_ERR="$(systemctl restart caddy 2>&1)"; then
    warn "Caddy 启动失败，最近日志："
    journalctl -u caddy --no-pager -n 15 2>/dev/null | sed 's/^/    /' || true
    error "Caddy 启动失败（80/443 是否被占用？云安全组是否放行？）"
fi
if [[ -n "$SITE_DOMAIN" ]]; then
    info "Caddy 站点: https://${SITE_DOMAIN} （SSL 自动申请，首次需 1~2 分钟）"
else
    warn "未设置 SITE_DOMAIN，Caddy 暂以 localhost 运行。"
    warn "之后请执行: SITE_DOMAIN=你的IP.nip.io bash install.sh  重新配置域名"
fi
info "Caddy 反向代理已启动"

# ---------- 9. 防火墙 ----------
if command -v ufw >/dev/null 2>&1; then
    ufw allow 80/tcp >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ufw allow 22/tcp >/dev/null 2>&1 || true
    info "防火墙已放行 22/80/443（ufw；如云厂商还有安全组请一并放行）"
elif command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --permanent --add-service=http --add-service=https >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    info "防火墙已放行 80/443（firewalld；如云厂商还有安全组请一并放行）"
fi

# ---------- 10. 健康自检 ----------
info "执行健康自检 ..."
HEALTH_OK=0
for _ in $(seq 1 20); do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    sleep 1
done
if [[ $HEALTH_OK -eq 1 ]]; then
    info "后端健康检查通过"
else
    warn "后端 20 秒内未响应，请执行: journalctl -u okk -e 查看原因"
fi
CADDY_ACTIVE="$(systemctl is-active caddy 2>/dev/null || echo unknown)"
info "Caddy 状态: ${CADDY_ACTIVE}（日志: journalctl -u caddy -f）"

echo ""
echo "=========================================================="
echo -e " ${GREEN}部署完成！${NC}"
echo "----------------------------------------------------------"
if [[ -n "$SITE_DOMAIN" ]]; then
    echo -e "  Web 管理界面:  ${GREEN}https://${SITE_DOMAIN}${NC}"
else
    echo -e "  Web 管理界面:  ${GREEN}http://你的IP:8000${NC} （临时，建议尽快配 Caddy）"
fi
echo -e "  Web 管理密码:  ${GREEN}${ADMIN_PW}${NC}"
echo ""
echo "  常用命令:"
echo "    systemctl status okk        # 查看运行状态"
echo "    journalctl -u okk -f        # 查看实时日志"
echo "    journalctl -u caddy -f      # 查看 Caddy/证书日志"
echo "    systemctl restart okk       # 重启服务"
echo "    nano ${APP_DIR}/.env          # 修改基础配置"
echo ""
echo "  下一步:"
echo "    1. 浏览器打开上面的地址登录"
echo "    2. 设置 → 交易所绑定 → 填入 OKX/币安 API Key"
echo "    3. 设置 → AI 模型 → 添加 DeepSeek/GPT/Gemini 模型"
echo "    4. 先跑【模拟盘】观察 1 周，再切实盘"
echo "=========================================================="
