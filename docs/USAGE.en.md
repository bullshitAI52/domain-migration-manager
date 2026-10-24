# Step-by-step usage guide

[Home](../README.en.md) · [中文](USAGE.zh-CN.md)

## What this tool is

A terminal tool, not a web control panel. It changes an application's public hostname while leaving the application and data on the same VPS. It does not register domains, move servers/databases or rewrite every repository link.

Use `plan` to preview. Use `apply` only when ready to make infrastructure changes. Test against staging first.

## 1. Prerequisites

The target Linux VPS needs Python 3.10+, Nginx, systemd, a configured acme.sh installation/ACME account and renewal scheduler. Apply and rollback require root. macOS/Windows can run offline preview.

The old HTTPS application must work. Your new Cloudflare zone must be Active. Use a scoped Cloudflare API token with Zone Read and DNS Write; **this is not the Telegram bot token**. Verify your Nginx include directory, HTTPS port availability, firewall and Cloudflare proxy/TLS configuration using the [README prerequisites](../README.en.md#prerequisites).

Keep an SSH recovery session open.

## 2. Download and configure

```sh
git clone https://github.com/bullshitAI52/domain-migration-manager.git
cd domain-migration-manager
cp examples/projects.example.json projects.json
nano projects.json
```

Do not repeat the copy command over an existing inventory. To update an existing checkout, use `git pull --ff-only` and review changes. JSON does not accept comments or trailing commas. `projects.json` is gitignored.

Use the [complete example](../examples/projects.example.json). Replace all placeholder domains, zone IDs and IP addresses.

| Field | Meaning |
|---|---|
| `id` | Your unique short project name, such as pic-tg |
| `old_domain`, `new_domain` | Hostnames only; no scheme, port or slash |
| `zone_id` | The target Cloudflare Zone ID, not Account ID |
| `ipv4`, `proxied` | Real VPS IPv4 and desired Cloudflare proxy setting |
| `https_port` | Public HTTPS port, such as 443 or 2087 |
| `upstream_port` | Application port on 127.0.0.1, such as 8770 |
| `service` | Actual systemd unit, such as pic-tg.service |
| `env_file`, `url_key` | Actual environment-file path and public URL key |
| `health_text` | Distinctive text expected on both application homepages |

Top-level paths must match your server. `cert_dir` stores certificates; `state_dir` stores sensitive backups and must be owned by the executing user with mode 700 if it already exists.

The environment variable must exactly match the old URL derived from the inventory, for example:

```text
TG_PUBLIC_URL=https://pic-tg.old-example.com:2087
```

Do not add a trailing slash. Missing keys, duplicates and unexpected values are rejected. Other environment variables are preserved.

## 3. Preview first

```sh
python3 migrate.py plan --config projects.json --project pic-tg
```

Check the selected project, old/new names and ports. `OFFLINE PREVIEW ONLY` means no API calls or file/service changes. It validates configuration syntax, **not DNS readiness or server access**.

## 4. Supply the token securely

In the target server's root Bash session:

```bash
read -r -s -p 'Cloudflare scoped token: ' CLOUDFLARE_API_TOKEN
export CLOUDFLARE_API_TOKEN
```

Paste the token and press Enter; input is hidden. Never place real tokens in JSON, command history, chat or GitHub. Do not assume sudo preserves environment variables from an ordinary user session.

## 5. Apply

```sh
python3 migrate.py apply --config projects.json --project pic-tg --confirm MIGRATE
```

The tool checks the old application, prepares DNS/TLS, adds and validates a dedicated vhost, updates the URL variable, restarts that application, and verifies both endpoints.

Save the printed `Journal: .../journal.json` **path**. Only `Completed: ...` indicates success. A DNS record appearing alone does not prove the migration completed. DNS validation can take time; do not launch concurrent runs.

## 6. Batch projects

Add entries to `projects` with unique IDs, target names and environment files.

```sh
# Both IDs must exist in your inventory
python3 migrate.py apply --config projects.json --project pic-tg --project second-app --confirm MIGRATE
# All entries
python3 migrate.py apply --config projects.json --confirm MIGRATE
```

Projects run in inventory order. A failure stops subsequent projects; previously successful projects are not automatically rolled back. One inventory applies to one VPS. There is no automatic SSH fleet orchestration.

## 7. Acceptance checks

Open both URLs and exercise real functionality. For Telegram, resend a test image and verify the new button, corner adjustment and returned image. Existing chat buttons do not change; restarting may invalidate in-memory jobs. HTTP checks alone cannot prove that workflow works.

After acceptance, update `old_domain` to the current name for the next migration. Update README links/bookmarks/other integrations separately. Old DNS/vhosts remain without automatic redirects; remove them only after confirming no dependencies remain.

## 8. Recovery

```sh
python3 migrate.py rollback --journal /var/lib/domain-migration/ACTUAL-RUN-DIRECTORY/journal.json --confirm ROLLBACK
```

Use your actual printed path. The journal contains environment-file backups and may contain secrets: never publish its contents.

Rollback restores local configuration, removes the newly generated vhost, reloads Nginx and restarts the application. **DNS records, certificates and renewal configuration remain** for manual review. Verify old functionality after rollback. For `rollback_failed` or a file conflict, stop retrying and recover through SSH.

## Troubleshooting

| Message | Check |
|---|---|
| `Unknown project selection` | Match --project to an inventory id |
| `does not match the configured old URL` | Domain, port and trailing slash in the environment variable |
| `Target DNS exists with conflicting settings` | Existing record ownership/purpose; do not blindly delete it |
| `Target managed Nginx file already exists` | Prior completion or interruption; inspect the private journal |
| `HTTPS health check failed` | Certificate, DNS, proxy/TLS mode, firewall, port, startup or expected text |
| `New domain did not reach the dedicated Nginx virtual host` | Origin routing, Nginx includes and listener |
| `Rollback conflict` | Later manual edits; inspect before overwriting |
| `Command failed` | Inspect the component privately; raw output is hidden to protect credentials |

**Test first, preview first, and keep a recovery path.**
