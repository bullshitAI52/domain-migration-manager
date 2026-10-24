#!/usr/bin/env python3
"""Domain migration CLI. Standard library only; Python 3.10+, Linux for apply."""
import argparse
import base64
import contextlib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler


class MigrationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise MigrationError(message)


def hostname(value):
    require(isinstance(value, str) and len(value) <= 253 and '.' in value, 'Invalid hostname')
    require(all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in value.split('.')), 'Use a lowercase ASCII/Punycode hostname, without port or path')
    return value


def absolute(value, check_links=True):
    p = Path(value)
    require(p.is_absolute() and '..' not in p.parts and str(p) != '/', 'Expected an absolute, specific path')
    if check_links:
        require(not any(x.is_symlink() for x in (p, *p.parents)), f'Symlink paths are not supported: {p}')
    return p


def load_config(path):
    config = json.loads(Path(path).read_text())
    require(config.get('version') == 1, 'Unsupported config version')
    require(isinstance(config.get('projects'), list) and config['projects'], 'No projects configured')
    ids, domains, envs = set(), set(), set()
    for p in config['projects']:
        require(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,40}', p['id']) is not None, 'Invalid project id')
        require(p['id'] not in ids, 'Duplicate project id'); ids.add(p['id'])
        hostname(p['old_domain']); hostname(p['new_domain'])
        require(p['old_domain'] != p['new_domain'], 'Old and new domains must differ')
        require(p['new_domain'] not in domains, 'Duplicate target hostname'); domains.add(p['new_domain'])
        require(re.fullmatch(r'[a-f0-9]{32}', p['zone_id']) is not None, 'Invalid Cloudflare zone ID')
        ipaddress.IPv4Address(p['ipv4'])
        require(type(p.get('proxied', True)) is bool, 'proxied must be boolean')
        for key in ('https_port', 'upstream_port'):
            require(type(p[key]) is int and 1 <= p[key] <= 65535, f'Invalid {key}')
        require(p['https_port'] != p['upstream_port'], 'HTTPS and upstream ports must differ')
        require(re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', p['service']) is not None and not p['service'].startswith('-'), 'Invalid systemd service')
        absolute(p['env_file'], check_links=False)
        require(p['env_file'] not in envs, 'Projects must not share an environment file'); envs.add(p['env_file'])
        require(re.fullmatch(r'[A-Z_][A-Z0-9_]*', p['url_key']) is not None, 'Invalid environment key')
        require(isinstance(p['health_text'], str) and p['health_text'], 'health_text must identify your application')
    for key in ('nginx_dir', 'cert_dir', 'acme_bin', 'acme_home', 'state_dir'):
        absolute(config[key], check_links=False)
    return config


def url(project, new=True):
    domain = project['new_domain' if new else 'old_domain']
    port = project['https_port']
    return f'https://{domain}' + (f':{port}' if port != 443 else '')


def paths(config, project):
    cert = Path(config['cert_dir']) / project['new_domain']
    nginx = Path(config['nginx_dir']) / f"dm-{project['id']}-{project['new_domain']}.conf"
    return cert, nginx


def nginx_text(config, p):
    cert, _ = paths(config, p)
    # These are generated dedicated vhosts, not search-and-replace in shared files.
    return f'''# Managed by domain-migration-manager; old virtual host is retained.
server {{
    listen {p['https_port']} ssl;
    server_name {p['new_domain']};
    ssl_certificate "{cert}/fullchain.pem";
    ssl_certificate_key "{cert}/key.pem";
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 20m;
    access_log off;
    add_header X-Domain-Migration {p['new_domain']} always;
    location / {{
        proxy_pass http://127.0.0.1:{p['upstream_port']};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_read_timeout 90s;
    }}
}}
'''


def env_text(text, key, old, new):
    lines = text.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith(key + '=')]
    require(len(matches) == 1, f'{key} must appear exactly once, without export/leading whitespace')
    i = matches[0]
    value = lines[i].split('=', 1)[1].strip()
    require(value in (old, '"'+old+'"', "'"+old+"'"), f'{key} does not match the configured old URL; no file changed')
    lines[i] = key + '=' + new + '\n'
    return ''.join(lines)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o600, owner=None):
    path = absolute(str(path))
    require(path.parent.is_dir(), f'Missing parent: {path.parent}')
    fd, tmp = tempfile.mkstemp(prefix='.dm-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            if owner is not None:
                os.fchown(stream.fileno(), *owner)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(parent_fd)
        finally: os.close(parent_fd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def save(journal, state):
    atomic(journal, json.dumps(state, ensure_ascii=False, indent=2).encode())


def change_file(journal, state, path, data):
    path = absolute(str(path))
    exists = path.exists()
    require(not exists or path.is_file(), f'Not a regular file: {path}')
    before = path.read_bytes() if exists else None
    info = path.stat() if exists else None
    entry = {'path': str(path), 'before': base64.b64encode(before).decode() if exists else None,
             'after_sha256': digest(data), 'mode': stat.S_IMODE(info.st_mode) if info else 0o644,
             'uid': info.st_uid if info else os.getuid(), 'gid': info.st_gid if info else os.getgid()}
    state['files'].append(entry)
    save(journal, state)  # Write-ahead record makes an interrupted write recoverable.
    atomic(path, data, entry['mode'], (entry['uid'], entry['gid']))


def command(args, env=None, allowed=(0,)):
    try:
        result = subprocess.run(args, capture_output=True, timeout=300, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise MigrationError(f'Command unavailable or timed out: {Path(args[0]).name}') from None
    require(result.returncode in allowed, f'Command failed: {Path(args[0]).name}; output hidden because it may contain secrets')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def health(address, expected, marker=None):
    try:
        with build_opener(NoRedirect).open(address, timeout=20) as response:
            body = response.read(1024*1024).decode('utf-8', errors='replace')
            require(response.status == 200 and expected in body, 'Health check returned the wrong application')
            require(marker is None or response.headers.get('X-Domain-Migration') == marker, 'New domain did not reach the dedicated Nginx virtual host')
    except (HTTPError, URLError, TimeoutError, OSError):
        raise MigrationError('HTTPS health check failed (certificate, network, status, or redirect)') from None


class Cloudflare:
    def __init__(self, token):
        require(bool(token), 'Set CLOUDFLARE_API_TOKEN in the environment')
        self.token = token

    def request(self, method, path, data=None):
        req = Request('https://api.cloudflare.com/client/v4' + path,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={'Authorization': 'Bearer '+self.token, 'Content-Type':'application/json'}, method=method)
        try:
            with build_opener(NoRedirect).open(req, timeout=30) as response:
                result = json.load(response)
        except (HTTPError, URLError, TimeoutError):
            raise MigrationError('Cloudflare request failed; check scoped token and network') from None
        require(result.get('success'), 'Cloudflare rejected request')
        return result['result']

    def inspect(self, p):
        zone = self.request('GET', '/zones/'+p['zone_id'])
        require(zone.get('status') == 'active', 'Target Cloudflare zone is not active')
        require(p['new_domain'] == zone['name'] or p['new_domain'].endswith('.'+zone['name']), 'Target hostname does not belong to this zone')
        records = self.request('GET', '/zones/'+p['zone_id']+'/dns_records?'+urlencode({'name':p['new_domain'], 'per_page':100}))
        require(not records or (len(records) == 1 and records[0]['type'] == 'A' and records[0]['content'] == p['ipv4'] and records[0]['proxied'] == p.get('proxied',True)), 'Target DNS exists with conflicting settings; nothing overwritten')
        return records

    def create(self, p):
        return self.request('POST', '/zones/'+p['zone_id']+'/dns_records',
                            {'type':'A','name':p['new_domain'],'content':p['ipv4'],'ttl':1,'proxied':p.get('proxied',True)})


def rollback(journal, state):
    # Validate ALL hashes before touching anything; do not overwrite later manual edits.
    for item in state['files']:
        path = absolute(item['path'])
        current = path.read_bytes() if path.exists() else None
        before = base64.b64decode(item['before']) if item['before'] is not None else None
        require(current == before or (current is not None and digest(current) == item['after_sha256']), 'Rollback conflict: a managed file was changed after migration')
    for item in reversed(state['files']):
        path = Path(item['path'])
        if item['before'] is None:
            if path.exists(): path.unlink()
        else:
            atomic(path, base64.b64decode(item['before']), item['mode'], (item['uid'],item['gid']))
    command(['nginx','-t'])
    command(['systemctl','reload','nginx'])
    command(['systemctl','restart',state['service']])
    state['status'] = 'rolled_back'
    save(journal, state)


def apply(config, p, cf):
    for key in ('nginx_dir','cert_dir','state_dir','acme_bin','acme_home'):
        absolute(config[key])
    cert, nginx = paths(config, p)
    absolute(str(cert)); absolute(str(nginx))
    require(not nginx.exists(), 'Target managed Nginx file already exists; inspect journal before retrying')
    require(Path(config['nginx_dir']).is_dir(), 'Nginx include directory is missing')
    # Prevent path interpolation in nginx configuration.
    require(re.fullmatch(r'/[A-Za-z0-9_./-]+', str(cert)) is not None, 'Certificate directory contains unsupported characters')
    env_path = absolute(p['env_file'])
    new_env = env_text(env_path.read_text(), p['url_key'], url(p,False), url(p)).encode()
    command(['nginx','-t'])
    command(['systemctl','is-active','--quiet',p['service']])
    health(url(p,False)+'/', p['health_text'])
    records = cf.inspect(p)
    root = absolute(config['state_dir'])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(stat.S_IMODE(root.stat().st_mode) == 0o700 and root.stat().st_uid == os.getuid(), 'State directory must be owned by current user with mode 700')
    run = Path(tempfile.mkdtemp(prefix=p['id']+'-',dir=root))
    journal = run/'journal.json'
    state = {'version':1,'project':p['id'],'service':p['service'],'status':'preparing','files':[],
             'old_url':url(p,False),'new_url':url(p),'dns_created':None,'note':'DNS and certificates are retained on rollback; never delete old DNS automatically.'}
    save(journal,state)
    print(f'Journal: {journal}', flush=True)
    try:
        if not records:
            state['dns_intent'] = {'zone_id':p['zone_id'],'name':p['new_domain']}
            save(journal,state)
            record = cf.create(p)
            state['dns_created'] = record['id']; save(journal,state)
        cert.mkdir(mode=0o700,parents=True,exist_ok=True)
        acme_env = os.environ.copy()
        acme_env['CF_Token'] = cf.token
        args = [config['acme_bin'],'--home',config['acme_home']]
        # acme.sh returns 2 when an existing, not-yet-due certificate is skipped.
        command(args+['--issue','--server','letsencrypt','--dns','dns_cf','-d',p['new_domain'],'--keylength','ec-256'],acme_env,allowed=(0,2))
        command(args+['--install-cert','-d',p['new_domain'],'--ecc','--key-file',str(cert/'key.pem'),
                      '--fullchain-file',str(cert/'fullchain.pem'),'--reloadcmd','nginx -t && systemctl reload nginx'],acme_env)
        change_file(journal,state,nginx,nginx_text(config,p).encode())
        command(['nginx','-t'])
        command(['systemctl','reload','nginx'])
        # Check new TLS route before changing the application's public URL.
        health(url(p)+'/',p['health_text'],p['new_domain'])
        change_file(journal,state,env_path,new_env)
        command(['systemctl','restart',p['service']])
        for attempt in range(6):
            try:
                health(url(p)+'/',p['health_text'],p['new_domain'])
                health(url(p,False)+'/',p['health_text'])
                break
            except MigrationError:
                if attempt == 5: raise
                time.sleep(3)
        state['status']='completed'; save(journal,state)
        print(f"Completed: {p['id']} -> {url(p)} (old endpoint retained)")
    except (Exception, KeyboardInterrupt):
        state['status']='failed'; save(journal,state)
        if state['files']:
            try: rollback(journal,state)
            except Exception:
                state['status']='rollback_failed'; save(journal,state)
                raise MigrationError(f'Migration and automatic rollback need attention. Private journal: {journal}') from None
        raise MigrationError(f'Migration stopped; local changes rolled back where possible. DNS/certificates retained. Journal: {journal}') from None


@contextlib.contextmanager
def lock():
    import fcntl
    flags = os.O_CREAT | os.O_RDWR | getattr(os,'O_NOFOLLOW',0)
    fd = os.open('/run/lock/domain-migration-manager.lock', flags, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise MigrationError('Another migration is running') from None
        yield
    finally: os.close(fd)


def main():
    parser = argparse.ArgumentParser(description='Preview and safely migrate isolated Nginx/systemd projects')
    parser.add_argument('action',choices=['plan','apply','rollback'])
    parser.add_argument('--config',default='projects.json')
    parser.add_argument('--project',action='append',help='Repeat to select projects; omitted = all')
    parser.add_argument('--confirm',help='Required for writes: MIGRATE or ROLLBACK')
    parser.add_argument('--journal',help='Private journal path for rollback')
    args = parser.parse_args()
    try:
        if args.action == 'rollback':
            require(args.confirm == 'ROLLBACK' and args.journal, 'Use --journal PATH --confirm ROLLBACK')
            require(sys.platform.startswith('linux') and os.geteuid() == 0, 'Run rollback as root on the target Linux server')
            journal = absolute(args.journal)
            require(journal.stat().st_uid == 0 and stat.S_IMODE(journal.stat().st_mode) == 0o600, 'Journal must be root-owned, mode 600; only use your own trusted journal')
            state=json.loads(journal.read_text())
            with lock(): rollback(journal,state)
            print('Local configuration restored; DNS and certificates retained.')
            return 0
        config = load_config(args.config)
        projects = [p for p in config['projects'] if not args.project or p['id'] in args.project]
        require(projects and (not args.project or set(args.project) <= {p['id'] for p in projects}), 'Unknown project selection')
        for p in projects:
            print(f"{p['id']}: {url(p,False)} -> {url(p)}")
            print(f"  DNS (add/reuse only), DNS-01 certificate, dedicated Nginx vhost, {p['url_key']}, restart {p['service']}, HTTPS checks")
            print('  Old DNS/vhost retained. No secret values shown.')
        if args.action == 'plan':
            print('OFFLINE PREVIEW ONLY. No network requests, file writes, or service changes. Server/DNS checks run during apply.')
            return 0
        require(args.confirm == 'MIGRATE', 'Write operations require --confirm MIGRATE')
        require(sys.platform.startswith('linux') and os.geteuid() == 0, 'Run apply as root on the target Linux server')
        require(Path(config['acme_bin']).is_file(), 'Install acme.sh separately and set acme_bin')
        cf = Cloudflare(os.environ.get('CLOUDFLARE_API_TOKEN',''))
        with lock():
            for p in projects: apply(config,p,cf)
        return 0
    except (MigrationError, ValueError, KeyError, OSError) as exc:
        # Never print raw parser data / system exceptions that may embed environment contents.
        print(str(exc) if isinstance(exc,MigrationError) else 'Configuration or filesystem error. Check the schema, paths and permissions.',file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
