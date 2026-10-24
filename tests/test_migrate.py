import base64
import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch, Mock
import migrate as m

ROOT = Path(__file__).resolve().parents[1]


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.config = json.loads((ROOT/'examples/projects.example.json').read_text())
        self.p = self.config['projects'][0]
        for key in ('nginx_dir','cert_dir','state_dir'):
            folder = self.root/key; folder.mkdir(mode=0o700)
            self.config[key] = str(folder)
        self.p['env_file'] = str(self.root/'bot.env')
        self.env = Path(self.p['env_file'])
        self.original = f'TG_BOT_TOKEN=secret-do-not-print\nTG_PUBLIC_URL={m.url(self.p,False)}\nOTHER=value\n'.encode()
        self.env.write_bytes(self.original); self.env.chmod(0o600)
        self.journal = self.root/'journal.json'
        self.state = {'files':[],'service':'pic-tg.service'}

    def test_example_is_valid(self):
        self.assertEqual(m.load_config(ROOT/'examples/projects.example.json')['version'],1)

    def test_invalid_hostnames(self):
        for host in ['abc;rm.com','a.com/path','A.com','*.test.com','a..com','-a.com','a.com:443']:
            with self.subTest(host=host), self.assertRaises(m.MigrationError): m.hostname(host)

    def test_exact_env_key_preserves_secret(self):
        result=m.env_text(self.original.decode(),'TG_PUBLIC_URL',m.url(self.p,False),m.url(self.p))
        self.assertIn('TG_BOT_TOKEN=secret-do-not-print',result)
        self.assertIn('OTHER=value',result)
        self.assertIn(m.url(self.p),result)

    def test_env_drift_duplicate_and_missing_rejected(self):
        for text in ['TG_PUBLIC_URL=https://different.example.com\n', 'OTHER=x\n', self.original.decode()+'TG_PUBLIC_URL=x\n']:
            with self.assertRaises(m.MigrationError): m.env_text(text,'TG_PUBLIC_URL',m.url(self.p,False),m.url(self.p))

    def test_new_vhost_does_not_include_old(self):
        text=m.nginx_text(self.config,self.p)
        self.assertIn('listen 2087 ssl',text)
        self.assertIn('127.0.0.1:8770',text)
        self.assertNotIn(self.p['old_domain'],text)

    def test_plan_is_offline_and_read_only(self):
        with patch('sys.argv',['migrate.py','plan','--config',str(ROOT/'examples/projects.example.json')]), patch.object(m,'command') as command, patch.object(m.Cloudflare,'request') as request, patch.object(m,'atomic') as write, patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(m.main(),0)
            command.assert_not_called(); request.assert_not_called(); write.assert_not_called()
            self.assertIn('OFFLINE PREVIEW',out.getvalue())

    def test_apply_needs_confirmation(self):
        with patch('sys.argv',['migrate.py','apply','--config',str(ROOT/'examples/projects.example.json')]), patch.object(m,'apply') as apply, patch('sys.stdout',new_callable=io.StringIO), patch('sys.stderr',new_callable=io.StringIO):
            self.assertEqual(m.main(),1); apply.assert_not_called()

    def test_private_journal_and_permissions(self):
        m.change_file(self.journal,self.state,self.env,b'changed\n')
        self.assertEqual(stat.S_IMODE(self.journal.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode),0o600)
        self.assertEqual(base64.b64decode(self.state['files'][0]['before']),self.original)

    @patch.object(m,'command')
    def test_rollback_restores_env_and_removes_only_new_vhost(self,command):
        target=self.root/'new.conf'
        m.change_file(self.journal,self.state,self.env,b'changed')
        m.change_file(self.journal,self.state,target,b'new vhost')
        m.rollback(self.journal,self.state)
        self.assertEqual(self.env.read_bytes(),self.original)
        self.assertFalse(target.exists())
        self.assertEqual(self.state['status'],'rolled_back')

    @patch.object(m,'command')
    def test_rollback_conflict_touches_nothing(self,command):
        target=self.root/'new.conf'
        m.change_file(self.journal,self.state,self.env,b'changed')
        m.change_file(self.journal,self.state,target,b'new vhost')
        target.write_bytes(b'user edit')
        with self.assertRaises(m.MigrationError): m.rollback(self.journal,self.state)
        self.assertEqual(self.env.read_bytes(),b'changed'); command.assert_not_called()

    def test_symlink_rejected(self):
        link=self.root/'link'; link.symlink_to(self.env)
        with self.assertRaises(m.MigrationError): m.absolute(str(link))

    def test_dns_conflict_not_overwritten(self):
        cf=m.Cloudflare('fake')
        cf.request=Mock(side_effect=[{'status':'active','name':'new-example.com'},[{'type':'CNAME','content':'other'}]])
        with self.assertRaises(m.MigrationError): cf.inspect(self.p)
        self.assertTrue(all(call.args[0]=='GET' for call in cf.request.call_args_list))

    def test_dns_matching_record_reused(self):
        cf=m.Cloudflare('fake')
        record={'type':'A','content':self.p['ipv4'],'proxied':True}
        cf.request=Mock(side_effect=[{'status':'active','name':'new-example.com'},[record]])
        self.assertEqual(cf.inspect(self.p),[record])

    def test_foreign_zone_rejected(self):
        cf=m.Cloudflare('fake'); cf.request=Mock(return_value={'status':'active','name':'another.com'})
        with self.assertRaises(m.MigrationError): cf.inspect(self.p)

    @patch.object(m,'health')
    @patch.object(m,'command')
    def test_apply_success(self,command,health):
        cf=Mock(token='fake'); cf.inspect.return_value=[]; cf.create.return_value={'id':'created'}
        m.apply(self.config,self.p,cf)
        self.assertIn(m.url(self.p),self.env.read_text())
        journal=next(Path(self.config['state_dir']).glob('*/journal.json'))
        self.assertEqual(json.loads(journal.read_text())['status'],'completed')
        self.assertTrue(m.paths(self.config,self.p)[1].exists())

    @patch.object(m,'command')
    def test_failed_new_health_rolls_back(self,command):
        cf=Mock(token='fake'); cf.inspect.return_value=[{'id':'existing'}]
        with patch.object(m,'health',side_effect=[None,m.MigrationError('bad TLS')]), self.assertRaises(m.MigrationError):
            m.apply(self.config,self.p,cf)
        self.assertEqual(self.env.read_bytes(),self.original)
        self.assertFalse(m.paths(self.config,self.p)[1].exists())
        cf.create.assert_not_called()

    def test_health_rejects_wrong_route_marker(self):
        response=Mock(status=200,headers={'X-Domain-Migration':'wrong.example.com'})
        response.read.return_value=b'expected app'
        response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        with patch.object(m,'build_opener') as opener:
            opener.return_value.open.return_value=response
            with self.assertRaises(m.MigrationError): m.health('https://example.com','expected app','example.com')

    @patch.object(m,'health')
    def test_restart_failure_restores_original_environment(self,health):
        cf=Mock(token='fake'); cf.inspect.return_value=[{'id':'existing'}]
        failed=False
        def command(args,*unused,**kwargs):
            nonlocal failed
            if args == ['systemctl','restart',self.p['service']] and not failed:
                failed=True
                raise m.MigrationError('simulated restart failure')
        with patch.object(m,'command',side_effect=command), self.assertRaises(m.MigrationError):
            m.apply(self.config,self.p,cf)
        self.assertTrue(failed)
        self.assertEqual(self.env.read_bytes(),self.original)
        journal=next(Path(self.config['state_dir']).glob('*/journal.json'))
        self.assertEqual(json.loads(journal.read_text())['status'],'rolled_back')

    def test_nginx_target_collision_stops_before_network(self):
        target=m.paths(self.config,self.p)[1]; target.write_text('existing configuration')
        cf=Mock()
        with patch.object(m,'command') as command, self.assertRaises(m.MigrationError): m.apply(self.config,self.p,cf)
        command.assert_not_called(); cf.inspect.assert_not_called()
        self.assertEqual(target.read_text(),'existing configuration')

    def test_duplicate_project_rejected(self):
        config=copy.deepcopy(self.config)
        config['projects'].append(copy.deepcopy(self.p))
        path=self.root/'config.json'; path.write_text(json.dumps(config))
        with self.assertRaises(m.MigrationError): m.load_config(path)


if __name__=='__main__': unittest.main()
