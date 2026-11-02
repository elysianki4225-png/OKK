# OKK · 24H AI 自动炒币 Agent 🤖（FastAPI + ccxt + litellm）

> **AI Agent 全自动合约交易机器人**：AI 自动选币、自动开多/开空、自动平仓、自动止盈止损、7×24 小时巡检。
> Web 管理界面（Material 3 风格 / 亮暗切换 / 手机不溢出），一键脚本部署到 1 核 1G 服务器。

⚠️ **风险警告**：合约交易有高风险，可能损失全部本金。本项目仅供学习研究，请先用**模拟盘**验证，实盘后果自负。

---

## ✨ 功能全景

### 🖥 前端 UI（原生 HTML + Tailwind + Phosphor Icons，Material 3 设计）
- 响应式布局：手机/平板/桌面自适应，移动端底部导航，表格横向滚动**不溢出**
- 亮色 / 暗色主题一键切换（记忆偏好），**全矢量图标、零 Emoji**
- 仪表盘：**总资产、今日盈亏（%）、未实现盈亏、模型状态（延迟/成功率）、净值曲线**
- **启动 / 停止 / 一键全平（急停）** 按钮
- **Windows 更新式「暂停时长」下拉菜单**：15 分钟 / 1 小时 / 4 小时 / 8 小时 / 24 小时 / 无限期
  → `Timestamp` 存入 SQLite，到点自动恢复，也可手动提前恢复
- AI 决策审计流：每条指令（含被风控拒绝的）都可回溯原因
- 行情页展示 AI 扫描的全部币种（成交额 TopN）
- 设置页：交易所绑定、AI 模型管理（测速/故障切换）、风控参数、密码管理

### ⚙️ 后端（Python FastAPI）
- **ccxt** 连接 **欧易 OKX** 或 **币安 Binance**（USDT 永续，实盘 / 模拟盘）
- **SQLite**（WAL 模式）存储：状态、暂停时间戳、决策、交易、净值快照、日志、加密密钥
- **litellm** 多模型接入：**实时测速 + 自动故障切换**（一个挂了自动换下一个）
  支持 OpenAI / DeepSeek / Gemini / Kimi / Groq / 硅基流动 / 任何 OpenAI 兼容接口
- **24H Agent 循环**：扫描 → 算指标 → AI 决策 → 风控 → 执行 → 记录，全自动

### 💰 省 Token 机制（核心设计）
程序端先用纯 Python 计算好指标，只发**压缩 JSON**给 AI（约为原始 K 线的 1/20）：
```json
{"p":67231.5,"chg1":0.32,"chg4":1.1,"chg24":-2.4,"rsi":58.2,
 "macd":{"macd":12.1,"signal":9.8,"hist":2.3},
 "boll":{"mid":66800,"upper":68100,"lower":65500,"width":0.039,"pos":0.62},
 "atrPct":0.85,"ema7":67100,"ema25":66500,"ema99":64200,"volx":1.4,"score":1.5,"htfScore":1}
```
指标含：RSI、MACD、BOLL、ATR、EMA7/25/99、量比、多周期评分（15m 主周期 + 1h 趋势过滤）。
另有：扫描 TopN 限制、K 线根数限制、扫描间隔可调，三重控费。

### 🛡 风控（AI 只是建议，代码强制执行）
| 规则 | 说明 |
|---|---|
| 防重锁 | 同币同方向 N 分钟内禁止重复开仓（时间戳入库） |
| 杠杆上限 | **硬性 ≤ 3 倍**（Web 端配置无法突破） |
| 仓位上限 | **单笔 ≤ 20% 总资产**（硬性） |
| 止盈止损 | **每单强制附带**（交易所原生 SL/TP，失败自动补触发单） |
| 并发限制 | 最大同时持仓 3（硬上限 5），单币单仓不加仓 |
| 熔断 | 当日回撤超限 → 自动停止开仓 |
| 连亏冷却 | 连亏 N 单 → 冷却 M 分钟 |
| 置信度门槛 | AI conf < 0.6 不开仓 |

### 🔒 安全
- 管理密码（PBKDF2 20 万轮哈希），登录后才能操作
- 交易所 / LLM 密钥 **Fernet 加密落盘**
- Caddy 自动 HTTPS（Let's Encrypt 自动申请+续期）+ 安全响应头
- API 只监听 127.0.0.1，外网必须经 Caddy

---

## 🚀 一键部署（推荐，1 核 1G 服务器）

```bash
# ① 下载项目（或 git clone 后进入目录）
git clone https://github.com/elysianki4225-png/OKK.git && cd OKK

# ② 一键安装（二选一）

#    A. 只有服务器 IP（用 nip.io 自动生成证书）
sudo SITE_DOMAIN=你的公网IP.nip.io bash install.sh
#    例: sudo SITE_DOMAIN=203.0.113.5.nip.io bash install.sh

#    B. 有自己的域名（DNS A 记录指向服务器 IP）
sudo SITE_DOMAIN=trade.example.com CADDY_EMAIL=you@example.com bash install.sh
```

脚本自动完成：安装 Python/Caddy → 建 venv 装依赖 → 生成随机管理密码 → 注册 systemd 服务 →
配置 Caddy 反代与 SSL → 放行防火墙 80/443 → 健康自检（日志统一走 journald，免手动轮转）。

完成后浏览器打开 `https://你的域名`，用脚本打印的管理密码登录即可。

### 连接交易所（欧易 / 币安）

1. **欧易 OKX**：头像 → API → 创建 API Key
   - 权限：勾选「读取 + 交易」，**不要勾提币**
   - 建议绑定服务器 IP 白名单
   - 记下 **API Key / Secret / Passphrase**（Passphrase 是创建时自己设的口令）
2. **币安 Binance**：账户 → API 管理 → 创建 API
   - 权限：勾选「允许合约」，**不开提币**，绑定 IP 白名单
3. 打开 Web → **设置 → 交易所绑定** → 选择交易所 → 填入三项 → **测试并绑定**
   （绑定前会真实测试连通性并显示余额，失败不入库）

> 想先练手？交易所选择「模拟盘（Testnet）」，或把 Web 设置里的**交易模式**保持为「模拟盘」——
> 模拟盘下所有交易只记录不真实下单，AI/风控全链路照常运行。

### 配置 AI 模型（litellm 格式）

**设置 → AI 模型 → 添加模型**，模型串示例：

| 提供商 | 模型串 | API Key 来源 | Base URL |
|---|---|---|---|
| DeepSeek（便宜推荐） | `deepseek/deepseek-chat` | platform.deepseek.com | `https://api.deepseek.com/v1` |
| OpenAI | `openai/gpt-4o-mini` | platform.openai.com | 留空 |
| Google Gemini | `gemini/gemini-2.0-flash` | aistudio.google.com | 留空 |
| Moonshot Kimi | `openai/moonshot-v1-8k` | platform.moonshot.cn | `https://api.moonshot.cn/v1` |
| Groq（超快） | `groq/llama-3.3-70b-versatile` | console.groq.com | 留空 |
| 硅基流动 | `openai/Qwen/Qwen2.5-7B-Instruct` | siliconflow.cn | `https://api.siliconflow.cn/v1` |

- **建议至少配 2 个模型**（不同厂商），一个挂了自动切换
- 点「全体测速」→ 系统给每个模型发微型请求测延迟，**最快的自动优先**
- 故障切换链：按优先级+延迟排序逐个尝试，全挂则本轮只观察不下单

---

## 🧠 AI Agent 工作流（每轮扫描）

```
┌──────────────────────────────────────────────────────────┐
│ 1. 状态检查：引擎开关 / 暂停时间戳 / 熔断状态              │
│ 2. 账户同步：余额 + 持仓 + 未实现盈亏（记净值快照）        │
│ 3. 选币：成交额 TopN 合约 + 你当前持仓币种                │
│ 4. 拉 K 线（15m + 1h）→ 纯 Python 算 RSI/MACD/BOLL/ATR   │
│ 5. 压缩 JSON → litellm 发给 AI（自动测速/故障切换）        │
│ 6. AI 返回 {actions:[{sym,act,conf,reason}]}              │
│ 7. 风控闸门：防重锁/杠杆≤3/仓位≤20%/熔断/置信度/并发      │
│ 8. 执行：市价开仓+强制止盈止损 / 平仓；写交易流水          │
│ 9. 审计：决策全记录（含拒绝原因）；Webhook 推送（可选）    │
│ 10. 休眠至下一轮（默认 300 秒，可调）                     │
└──────────────────────────────────────────────────────────┘
```

---

## 📁 项目结构

```
OKK/
├── install.sh              # 一键部署脚本（装 Caddy + systemd + 依赖）
├── run.sh                  # 本地开发启动
├── requirements.txt
├── .env.example            # 环境变量说明
├── app/
│   ├── main.py             # FastAPI 入口 / 生命周期
│   ├── routes.py           # REST API（认证/状态/控制/设置）
│   ├── engine.py           # 24H Agent 主循环
│   ├── ai_engine.py        # litellm 多模型 + 测速 + 故障切换
│   ├── risk.py             # 风控引擎（硬性规则强制执行）
│   ├── trader.py           # 下单执行 / 止盈止损 / 一键全平
│   ├── exchanges.py        # ccxt 封装（OKX / Binance）
│   ├── indicators.py       # RSI/MACD/BOLL/ATR/EMA（纯 Python）
│   ├── db.py               # SQLite 存储层
│   ├── security.py         # Fernet 加密 / 口令哈希 / 会话令牌
│   └── config.py           # 配置与硬性风控上限
├── static/
│   ├── index.html          # Material 3 前端（Tailwind + Phosphor）
│   └── app.js              # 前端逻辑
├── deploy/
│   ├── Caddyfile           # Caddy 反代 + 自动 SSL + 安全头
│   └── okk.service         # systemd 单元（内存限制 680M）
└── docs/
    └── DEPLOY.md           # 部署详解（nip.io / 域名 / SSL / 运维）
```

---

## 🖥 本地开发 / 测试

```bash
bash run.sh
# 打开 http://127.0.0.1:8000
```

## ⚙️ 常用运维命令

```bash
systemctl status okk          # 运行状态
journalctl -u okk -f          # 实时日志
systemctl restart okk         # 重启
nano /opt/okk/.env            # 基础配置（改后 restart）
```

## ❓ FAQ

**Q：安装中途报错/中断怎么办？**
A：脚本是幂等的，修复问题后直接重跑同一条命令即可（已装组件自动跳过）。报错时会打印「失败行号」，把它发给开发者。所有日志用 `journalctl -u okk -f` / `journalctl -u caddy -f` 查看。

**Q：1 核 1G 跑得动吗？**
A：可以。uvicorn 单进程 + SQLite + 无重型前端构建，内存峰值 < 400MB，systemd 里已设 MemoryMax=680M 保护。

**Q：怎么不费 Token？**
A：指标本地算只发压缩 JSON；扫描 TopN 限制币数；间隔可调；K 线根数可调。一轮 25 币约消耗 2~4K tokens，DeepSeek 成本约 ¥0.01/轮。

**Q：暂停后会自动恢复吗？**
A：会。暂停时长的时间戳写入 SQLite，引擎每 5 秒检查，到点自动继续；「无限期暂停」则直到手动恢复。

**Q：AI 乱下单怎么办？**
A：AI 无直接下单权限——每条指令必须过风控闸门（防重锁/杠杆/仓位/熔断/置信度），并且每单强制带止盈止损。可随时「一键全平」急停。

**Q：想换模型/多模型？**
A：设置里随时增删，「全体测速」后自动按延迟排序，挂了自动切换。

---

## ⚖️ 免责声明

本软件按 MIT 协议开源，仅供学习与研究。数字资产合约交易风险极高，使用本软件产生的任何盈亏由使用者自行承担。请遵守所在地区法律法规。
