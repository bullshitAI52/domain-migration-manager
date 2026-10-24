# 手把手使用说明

[返回首页](../README.md) · [English](USAGE.en.md)

## 先弄清楚：这个工具不是网站

它是在服务器终端里运行的工具，不是点开网页就能操作的管理面板。第一次需要把项目资料填好，之后换域名才方便。

**只想看看会改什么，用 `plan`；真正换域名才用 `apply`。** 不要直接复制执行迁移命令去试生产服务。

适用场景：程序和数据还在原来的 VPS，只把对外地址换掉。比如图片机器人的旧地址换成新地址，但机器人、图片处理程序和服务器都不搬家。

不适用：把 VPS 搬到另一台机器、迁移数据库、注册新域名，或修改所有仓库里的网址。

## 1. 准备好再开始

- 新域名已经接入 Cloudflare，状态为 Active。
- VPS 已有 Python 3.10+、Nginx、systemd 和配置好的 acme.sh；定时续期也已准备好。
- 旧项目正在运行，旧 HTTPS 页面能正常打开。
- 你知道项目的服务名、环境文件路径和程序端口。
- Cloudflare API Token 只授权相关区域的 Zone Read 和 DNS Write。**它不是 TG Bot Token。**
- 新域名的代理、TLS 回源模式、端口和防火墙已经按[首页前置条件](../README.md#第一次准备)检查。

正式操作在目标 VPS 的 root 会话进行；本机 Mac/Windows 可先预览。操作时保留一个 SSH 窗口，以便出错时恢复。第一次先拿测试项目演练。

## 2. 下载工具，复制配置

```sh
git clone https://github.com/bullshitAI52/domain-migration-manager.git
cd domain-migration-manager
cp examples/projects.example.json projects.json
nano projects.json
```

已经有 `projects.json` 时，不要再次复制覆盖它。更新工具可在仓库里运行 `git pull --ff-only`，然后检查版本说明。

`projects.json` 是自己的项目清单，已默认排除 Git 提交。JSON 不能加注释，最后一个字段后不能多写逗号。

## 3. 以图片机器人为例填清单

打开[完整示例](../examples/projects.example.json)，不要只复制其中几个字段。

假设把 `pic-tg.old-example.com` 换成 `pic-tg.new-example.com`：

| 字段 | 怎么填 |
|---|---|
| `id` | 自己给项目起的短名字，例如 `pic-tg` |
| `old_domain` | 当前在用的域名，不含 `https://`、端口和 `/` |
| `new_domain` | 准备启用的新域名，同样只填主机名 |
| `zone_id` | **新域名**对应的 Cloudflare Zone ID，不是 Account ID |
| `ipv4` | VPS 的真实公网 IPv4，不能保留示例的 `192.0.2.10` |
| `proxied` | `true` 表示通过 Cloudflare 代理；`false` 表示仅 DNS |
| `https_port` | 对外 HTTPS 端口；机器人示例用 `2087` |
| `upstream_port` | 程序在服务器本机监听的端口；示例用 `8770` |
| `service` | 实际服务名，例如 `pic-tg.service` |
| `env_file` | 实际环境文件路径，例如 `/etc/pic-tg/bot.env` |
| `url_key` | 要修改的配置项，例如 `TG_PUBLIC_URL` |
| `health_text` | 新旧页面里都应该出现的特征文字，例如 `高清图片裁剪` |

示例里的域名、IP、Zone ID 都是占位资料。服务名和路径也必须核实，不能照抄后直接执行。

清单顶部的 `nginx_dir`、`acme_bin`、`acme_home` 要与服务器实际安装位置一致。`cert_dir` 存新证书，`state_dir` 存私密备份；后者若已经存在，必须由当前执行用户拥有，权限为 700。

环境文件里的 URL 必须与清单推导出的旧地址完全一致。例如端口为 2087 时：

```text
TG_PUBLIC_URL=https://pic-tg.old-example.com:2087
```

不要加末尾 `/`。工具不会猜测、追加缺失变量或覆盖不匹配的旧值。只替换这一个变量，其他配置保留。

## 4. 先预览，不做任何修改

```sh
python3 migrate.py plan --config projects.json --project pic-tg
```

看到类似：

```text
pic-tg: https://pic-tg.old-example.com:2087 -> https://pic-tg.new-example.com:2087
...
OFFLINE PREVIEW ONLY. No network requests, file writes, or service changes.
```

重点核对：项目是否选对、旧地址是否正确、新地址是否拼错、端口是否正确。

**预览通过只说明清单格式可用，不代表 DNS、证书、服务器权限已经验证。** 真正的远程检查在执行时进行。

## 5. 安全输入 Cloudflare Token

在目标 VPS 的 root Bash 会话中输入：

```bash
read -r -s -p 'Cloudflare scoped token: ' CLOUDFLARE_API_TOKEN
export CLOUDFLARE_API_TOKEN
```

第一条命令会等待输入，粘贴 Token 后按回车。输入不会显示在屏幕上，这是正常现象。

不要把真实 Token 写进 JSON、命令示例、聊天或 GitHub。也不要把机器人 Token 当作 Cloudflare Token。不要先在普通用户会话设置变量、再期待 `sudo` 自动保留它。

## 6. 确认后迁移一个项目

```sh
python3 migrate.py apply --config projects.json --project pic-tg --confirm MIGRATE
```

它会检查旧项目、配置新 DNS 和证书、新增独立 Nginx 站点、检查新入口，再更新服务地址并重启该服务。

看到 `Journal: .../journal.json` 时保存这个**路径**。看到 `Completed: pic-tg -> ...` 才表示脚本完成。不要只看 DNS 记录已经出现，就认为所有步骤完成。

迁移过程中可能等待 DNS 验证。不要重复启动多个迁移进程。

## 7. 多个项目怎么一起处理？

在 JSON 的 `projects` 数组中添加项目条目，每个项目使用不同 `id`、目标域名和环境文件；端口按实际情况填写。

```sh
# 只处理指定的两个项目；second-app 必须是清单中真实存在的 id
python3 migrate.py apply --config projects.json --project pic-tg --project second-app --confirm MIGRATE

# 处理清单中全部项目
python3 migrate.py apply --config projects.json --confirm MIGRATE
```

按清单顺序逐个处理，一个失败就停止后面的项目。**前面已经成功的项目不会一起撤销。** 不同 VPS 分别执行，不会自动 SSH 到所有服务器。

## 8. 迁移后自己再验收一次

1. 新、旧地址都打开，确认显示的是正确项目。
2. 用真实功能走一遍，不能只看首页。
3. TG 机器人重新发一张测试图片，检查新按钮、四角裁剪和返回图片。
4. 稳定后，把清单中的 `old_domain` 更新为当前域名；下一次再填新的 `new_domain`。

TG 历史消息里的按钮不会自动更新；重启还可能清空内存中的任务，所以应重新发图。GitHub README、收藏夹和其他平台链接也需要另行更新，本工具不会自动修改它们。

旧 DNS 和旧站点默认保留，不会自动重定向或删除。确认没人再依赖后，再安排清理。

## 9. 出错怎么恢复？

常规失败会尝试恢复本项目的本地配置。断电、强制结束或想手动撤销时，用该次输出的真实路径：

```sh
python3 migrate.py rollback --journal /var/lib/domain-migration/实际任务目录/journal.json --confirm ROLLBACK
```

不要照抄 `实际任务目录`，要换成自己的。也不要上传或展示 `journal.json` 内容：里面可能有环境变量密钥。

回滚会恢复环境文件、移除本次新建的站点配置、重新加载 Nginx 并重启对应服务。**新 DNS、已签发证书和续期配置会保留，不会自动删除。** 回滚后仍须验证旧业务正常。

如果出现 `rollback_failed` 或文件冲突，停止重复执行，保留记录，通过 SSH 检查并人工恢复。

## 常见提示怎么理解？

| 提示 | 含义与处理 |
|---|---|
| `Unknown project selection` | `--project` 名字与清单中的 `id` 不一致 |
| `does not match the configured old URL` | 环境文件中的旧 URL 不匹配；核对端口、末尾斜杠和域名 |
| `Target DNS exists with conflicting settings` | 新名字已经被其他解析使用；先确认用途，别直接删除 |
| `Target managed Nginx file already exists` | 可能以前已迁移或中断；先查记录，不要覆盖或强删 |
| `HTTPS health check failed` | 可能是证书、DNS、代理模式、端口、防火墙、应用启动或页面内容不符 |
| `New domain did not reach the dedicated Nginx virtual host` | 请求没到新站点；检查 Cloudflare 回源、Nginx include 和监听端口 |
| `Rollback conflict` | 迁移后有人手动改过文件；工具拒绝覆盖，需要人工核对 |
| `Command failed` | 为保护密钥隐藏了原始输出；管理员应在服务器检查相关组件，不要公开贴环境文件 |

## 记住三件事

**先测试、先预览、保留恢复入口。** 这是减少重复劳动的工具，不是免检查的万能迁移器。
