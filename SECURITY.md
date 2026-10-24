# Security / 安全说明

- This is privileged infrastructure tooling. Review code/configuration, use staging first, and keep an out-of-band SSH session during migration.
- Configurations and journals are trusted local administrator input, not uploaded user documents. Do not run untrusted inventories/journals as root.
- Never commit tokens, private environment files, certificate keys, or rollback journals. A journal contains an environment backup, not merely harmless metadata.
- API requests only target api.cloudflare.com; HTTPS verification is enabled. No shell is used to execute user-provided command strings. acme.sh remains a separately trusted executable.
- Rollback covers local tracked files. Provider-side DNS, issued certificates and renewal configuration are not transactional and remain for manual review.
- Concurrent runs on the same Linux host are locked. External DNS edits and other administrators are not locked; coordinate maintenance.
- Retaining old DNS/vhosts is deliberate. Remove them only after confirming that links, jobs, integrations and certificate renewals no longer depend on them.
- Do not file public issues containing secrets. Describe the issue using redacted examples; use GitHub's private vulnerability reporting if enabled.

本工具需要管理员权限。先在测试环境验证；配置和回滚记录必须来自可信管理员。不要上传密钥、真实环境文件和迁移记录。回滚不代表所有云资源都被撤销。迁移期间保留 SSH 恢复入口，并协调其他管理员不要同时修改同一项目。
