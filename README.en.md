# Domain Migration Manager

[中文](README.md) · [Example inventory](examples/projects.example.json) · [Security](SECURITY.md)

**One inventory, an offline preview, an explicit apply command, and recoverable local configuration changes.**

A Python standard-library CLI for existing **Cloudflare + Nginx + systemd** applications on a Linux VPS. Supports sequential project batches. It does not migrate your entire server or replace arbitrary strings across your filesystem.

## Workflow

1. Validate project configuration and old HTTPS endpoint.
2. Check the active target Cloudflare zone; create an A record or reuse an exact match. Conflicting records are never overwritten.
3. Obtain and install a certificate using an existing acme.sh installation and DNS-01 validation.
4. Add a dedicated Nginx virtual host. Test and gracefully reload Nginx, retaining the old host.
5. Verify the new HTTPS endpoint, application text and dedicated response header.
6. Change exactly one public-URL environment variable, preserving all other values and permissions. Restart only that application's systemd service.
7. Check old and new endpoints. On failure, attempt local configuration rollback.

**Initial release: tested with mocks, not a production domain migration. Validate against a staging application first.**

## Prerequisites

Python 3.10+, Nginx, systemd, and an existing configured acme.sh installation with an ACME account and renewal scheduler. Apply/rollback run as root on the target Linux server. Preview also works on macOS/Windows.

The target domain must already be active in Cloudflare. Provide a scoped API token with Zone Read and DNS Write for the relevant zone(s). DNS-01 creates temporary TXT records. No global API key is required. acme.sh may persist DNS credentials in its home for renewal; protect that directory.

The configured Nginx directory must be included by nginx.conf. The chosen HTTPS port must be available or already served by Nginx, exposed through your firewall, and supported by Cloudflare if proxying. Configure Full (strict) for the target hostname. This tool does not change global SSL settings or firewall rules. Never break other sites by changing their TLS policy.

Applications must read the named environment file and expose a root HTTPS page returning 200 with a distinctive string. Environment files and managed paths must not be symlinks. Complex Nginx setups need separate adaptation.

## Quick start

```sh
git clone https://github.com/bullshitAI52/domain-migration-manager.git
cd domain-migration-manager
cp examples/projects.example.json projects.json
# Edit your private inventory. projects.json is gitignored.
python3 migrate.py plan --config projects.json
```

Preview makes **no network calls or writes**. It validates schema, not actual server readiness. Fill in your domain names, target zone ID, VPS IPv4, ports, systemd service, environment file/key, and health-check text. The example IP and zone ID are placeholders, not deployable values. One inventory describes one VPS; run separately on each host.

Set `CLOUDFLARE_API_TOKEN` securely in the root process environment. Do not place secrets in the inventory, GitHub or shell history. For interactive Bash:

```bash
read -r -s -p 'Cloudflare scoped token: ' CLOUDFLARE_API_TOKEN
export CLOUDFLARE_API_TOKEN
```

```sh
# One project
python3 migrate.py apply --config projects.json --project pic-tg --confirm MIGRATE
# Multiple selected projects: repeat --project; omit it for the entire inventory.
```

Missing confirmation prevents writes. The batch stops on first failure. **Previously successful projects remain migrated; the batch is not atomic.**

## Rollback and recovery

```sh
python3 migrate.py rollback --journal /var/lib/domain-migration/RUN-DIRECTORY/journal.json --confirm ROLLBACK
```

The command prints the exact private journal location. Journals contain original environment-file bytes and may therefore contain secrets. Directories use mode 700, journals 600. Never publish them. Only use your own trusted root-owned journals.

Write-ahead records allow recovery after interruption. Rollback validates every file hash first, refusing to overwrite later manual edits. It restores the environment, removes only the newly generated vhost, tests/reloads Nginx, and restarts the selected application. Rollback itself can fail; `rollback_failed` requires manual recovery.

**DNS records, certificates and acme.sh renewal entries are deliberately retained.** They are not deleted or revoked during rollback. Inspect the journal and clean them up manually only when safe. Existing DNS and virtual hosts are never removed. Re-running a completed migration refuses to overwrite its generated vhost; for the next move, update old_domain to the current hostname and choose a new target.

## Limitations

- A-record migrations, local HTTP upstreams, one root public URL per project.
- No DNS registration, nameserver changes, database/data migration, Worker/Tunnel creation, paid resources, or README/source-code rewriting.
- No special WebSocket or path-prefix configuration; generated vhosts use a 20 MB body limit and 90-second upstream timeout.
- Application restarts can invalidate in-memory sessions; this is not guaranteed zero-downtime migration.
- Telegram buttons already sent to chats keep their old URLs. Ask users to resend images after migration. HTTP checks do not prove the full Telegram workflow works.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

GitHub Actions runs mocked tests and offline preview on Python 3.10, 3.12 and 3.13; no production credentials or deployments.

References: [Cloudflare DNS API](https://developers.cloudflare.com/api/resources/dns/subresources/records/), [acme.sh DNS API](https://github.com/acmesh-official/acme.sh/wiki/dnsapi).

MIT licensed.
