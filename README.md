# auto-dns

一个零依赖的 Python 脚本，用来把 Cloudflare DNS 的 `A` 或 `AAAA` 记录自动更新为当前公网 IP，适合做轻量 DDNS。

## 准备

先在 Cloudflare 创建 API Token，至少需要：

- `Zone:DNS:Read`
- `Zone:DNS:Edit`

## 快速使用

直接执行：

```bash
python3 auto_dns.py \
  --token "your_cloudflare_api_token" \
  --zone "example.com" \
  --record "home.example.com" \
  --type A \
  --create
```

常用说明：

- `--type A` 更新 IPv4，`--type AAAA` 更新 IPv6
- 不传 `--ip` 时会自动检测公网 IP，默认优先使用 `ip.me`
- `--create` 表示记录不存在时自动创建
- `--dry-run` 只演练，不写入 Cloudflare

## 使用环境变量

也可以先复制一份配置：

```bash
cp .env.example .env
```

然后填写真实值，直接运行：

```bash
python3 auto_dns.py
```

脚本会自动读取当前目录下的 `.env` 文件；命令行参数优先级高于环境变量。

## 常用参数

- `CF_API_TOKEN`：Cloudflare API Token
- `CF_ZONE`：Zone，例如 `example.com`
- `CF_RECORD`：完整记录名，例如 `home.example.com`
- `CF_RECORD_TYPE`：`A` 或 `AAAA`
- `CF_TTL`：TTL，默认 `1`（自动）
- `CF_PROXIED`：`true` / `false`
- `CF_CREATE`：`true` / `false`
- `CF_DRY_RUN`：`true` / `false`
- `GATEWAY` / `GATEWAY_INTERFACE`：可选；只有需要强制经指定网关取公网 IP 时才设置，不填则直接使用当前默认网络出口探测公网 IP

## 定时运行

可配合 cron 或 `systemd` 定时执行，仓库中的 `systemd/` 目录提供了示例配置。

## 临时绕过旁路由获取公网 IP

如果默认网关走的是旁路由，但你想临时改为主路由获取公网 IP，可以使用：

```bash
sudo python3 get_ip_via_gateway.py --gateway 192.168.1.1 --interface eth0
```

说明：

- 脚本会临时替换默认路由
- 获取公网 IP 后自动恢复原路由
- 需要 root 权限

如果你不想影响整机默认路由，推荐使用 policy routing 版本：

```bash
sudo python3 get_ip_via_policy_routing.py --gateway 192.168.1.1 --interface eth0
```

说明：

- 只给这次请求单独选路
- 不修改系统默认网关
- 仍然需要 root 权限
- 默认会给临时路由加 `onlink`，对 `192.168.x.x` 这类主路由网关更稳
- 临时 `ip rule` 优先级默认在 `main` 路由表之前，避免被现有默认路由抢先匹配
- 默认会自动尝试多个公网 IP 服务，单个站点不通时会自动回退

在 Docker 定时任务中，`GATEWAY` 和 `GATEWAY_INTERFACE` 也是可选项：

- 不设置：直接使用容器所在宿主机的默认网络出口获取公网 IP
- 设置：先通过策略路由脚本，经指定网关获取公网 IP，再更新 Cloudflare
