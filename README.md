# 域名迁移管理器 · Domain Migration Manager

[English](README.en.md) · [配置示例](examples/projects.example.json) · [安全与边界](SECURITY.md)

**换域名不用到处找文件：一张项目清单，先预览，再执行，失败可回滚。**

适合已经运行在 Linux VPS 上的 **Cloudflare + Nginx + systemd** 小项目，例如 Telegram 图片机器人。无需安装 Python 第三方依赖。不是域名注册工具，也不是把所有服务器“无脑一键搬家”。

## 大白话：它帮你做什么？

以前：改解析、弄证书、改网站配置、改程序地址、重启、检查，漏一步就打不开。

现在：第一次把项目资料填好，以后改清单里的新域名，运行迁移命令。它按顺序处理，只重启选中的项目。多个项目可以分批执行。

| 环节 | 做什么 | 防误操作 |
|---|---|---|
| 预览 | 列出新旧地址及计划步骤 | 离线，不调用 API、不修改文件 |
| DNS | 在目标 Cloudflare 区域新增 A 记录，或复用完全一致的记录 | 有冲突就停止，不覆盖旧记录 |
| HTTPS | 通过 acme.sh DNS-01 申请证书并安装 | 不修改整个域名的 Cloudflare SSL 模式 |
| Nginx | 新增独立站点配置，测试后平滑重载 | 旧站点文件不改、不删 |
| 程序地址 | 精确修改指定环境变量，例如 `TG_PUBLIC_URL` | 其他变量和密钥保留，旧值不一致就停止 |
| 验证 | 检查新旧 HTTPS、页面标识和新站点响应头 | 防止“能打开，但打开的是别的项目” |
| 回滚 | 恢复环境文件、移除本次新增站点、重启对应服务 | 发现后续人工改动就停止，避免覆盖 |

**状态：初版工具，已做模拟测试；没有拿作者现有生产服务做真实迁移实验。先在测试项目验收。**

## 第一次准备

在目标 VPS 上运行。Mac/Windows 可以预览，真正迁移与回滚仅支持 Linux root。

1. Python 3.10+、Nginx、systemd 和 acme.sh 已安装；acme.sh 已注册 ACME 账户并配置定时续期。工具不会安装这些软件、替你同意注册条款或开放防火墙。
2. 新域名已接入 Cloudflare 并处于 Active。创建仅限相关区域的 API Token，权限为 **Zone Read + DNS Write**；DNS-01 验证会创建临时 TXT 记录。不要使用 Global API Key。
3. 配置中的 Nginx 目录确实被 `nginx.conf` include；所选 HTTPS 端口由 Nginx 使用或空闲。不能占用其他程序的端口。
4. 如果 Cloudflare 开启代理，端口必须受支持，目标主机应使用 **Full (strict)** 回源；本工具不自动调整全局 SSL。若现有域名使用 Flexible，请单独配置目标主机规则，不要为此改坏其他网站。
5. 已有服务确实从该环境文件读取 URL，且旧地址能以 HTTPS 返回 200 和你指定的页面文字。目录、配置文件不支持符号链接。

```sh
git clone https://github.com/bullshitAI52/domain-migration-manager.git
cd domain-migration-manager
cp examples/projects.example.json projects.json
# 编辑 projects.json，换成自己的资料；此文件已被 .gitignore 排除。
python3 migrate.py plan --config projects.json
```

## 配置怎么填？

配置清单不需要机器人 Token、SSH 密码或证书私钥。

| 字段 | 含义 |
|---|---|
| `old_domain` / `new_domain` | 旧、新主机名；不加 https、端口或斜杠 |
| `zone_id` | 新域名在 Cloudflare 的 Zone ID，不是 Account ID |
| `ipv4` | 你的 VPS 公网 IPv4；示例地址不可用于生产 |
| `https_port` | 对外 HTTPS 端口，例如 443 或 2087 |
| `upstream_port` | 本机程序端口，例如 8770；固定连接 127.0.0.1 |
| `service` | 对应 systemd 服务名，例如 pic-tg.service |
| `env_file` / `url_key` | 环境文件绝对路径和要更新的变量名 |
| `health_text` | 页面中有辨识度的固定文字；不是仅检查端口通不通 |

多个项目：在 `projects` 数组里添加条目，每个条目有不同的 `id`、目标域名和环境文件。**一个清单管理同一台 VPS**；多台 VPS 各自执行，不把 SSH 密码集中进清单。

## 开始迁移

先在目标服务器的 root 会话中安全设置 `CLOUDFLARE_API_TOKEN`。不要把真实值写在命令历史、配置清单或 GitHub 中。例如 Bash 交互式隐藏输入：

```bash
read -r -s -p 'Cloudflare scoped token: ' CLOUDFLARE_API_TOKEN
export CLOUDFLARE_API_TOKEN
```

```sh
# 只迁移一个项目
python3 migrate.py apply --config projects.json --project pic-tg --confirm MIGRATE

# 迁移选中的多个项目（id 来自你自己的清单）
python3 migrate.py apply --config projects.json --project pic-tg --project another-app --confirm MIGRATE

# 按清单顺序迁移全部项目
python3 migrate.py apply --config projects.json --confirm MIGRATE
```

没有 `--confirm MIGRATE` 就不执行写入。每个项目先检查旧服务、目标 DNS 与配置；上一个成功才继续下一个，失败就停止。**不是整批原子事务：前面成功的项目不会因为后面失败而自动撤销。**

执行过程打印私密迁移记录路径。目录权限 700，记录权限 600；记录包含环境文件备份，**里面可能有密钥，禁止上传 GitHub 或发截图**。acme.sh 为自动续期可能在其 home 目录保存 DNS 凭据，请限制目录权限并按它的文档管理。

## 回滚与中断恢复

```sh
python3 migrate.py rollback --journal /var/lib/domain-migration/实际任务目录/journal.json --confirm ROLLBACK
```

- 失败时自动尝试回滚本项目本地配置；断电或强制结束后，用打印的记录手动回滚。
- 先校验所有文件内容，再恢复；如果有人已手动改过，拒绝覆盖。
- **Cloudflare 新记录、证书、acme.sh 续期条目不会自动删除或撤销。** 新记录保持在日志中，确认不再使用后由你手动清理；这是保守策略，不是完整云资源事务。
- Nginx 或服务重启也可能失败；看到 `rollback_failed` 必须人工检查，不能认为已经恢复。
- 旧 Nginx 和 DNS 保留兼容。观察稳定后再自行清理，不自动发起旧域名重定向。
- 同一配置成功后再运行会拒绝覆盖现有新站点。下一次迁移应把 `old_domain` 更新为当前域名，再填新的 `new_domain`。

## Telegram 特别说明

更换 `TG_PUBLIC_URL` 只影响后续生成的按钮，聊天里的旧按钮不会自动改变。重启机器人还可能让内存任务失效，应让用户重新发图。网页健康检查不能代替 Telegram 内“发图—拖角—回传”的人工验收。

## 第一版不做什么？

- 不改已有共享 Nginx 配置、不全盘搜索替换、不更新 GitHub README/源码中的历史链接。
- 不迁移数据库、文件、服务器、不注册域名、不改 Nameserver。
- 不自动创建 Cloudflare Worker、Tunnel、页面规则或收费资源。
- 只支持新增/复用 A 记录与单个根 URL 环境变量；不支持 CNAME/IPv6、WebSocket 专用配置、路径前缀或复杂站点模板。
- 不保留活跃的内存会话，不保证零停机；对应应用会重启。

## 测试

```sh
python3 -m unittest discover -s tests -v
```

GitHub Actions 在 Python 3.10、3.12、3.13 上运行模拟测试与离线预览，不使用真实密钥，不部署、不触碰生产资源。

参考：[Cloudflare DNS API](https://developers.cloudflare.com/api/resources/dns/subresources/records/)、[acme.sh DNS API](https://github.com/acmesh-official/acme.sh/wiki/dnsapi)、[Cloudflare SSL 模式](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/)。

MIT License。
