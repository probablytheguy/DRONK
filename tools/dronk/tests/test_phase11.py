import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_support import IsolatedConfigTestCase
import configuration as cfg
import command_center as cc


class AuditRegressionTests(IsolatedConfigTestCase):
    def test_launcher_log_defaults_and_overrides_use_real_paths(self):
        wrapper=cc.ROOT/'tools/milestone-0.8/ouitak-live'
        with tempfile.TemporaryDirectory() as cwd:
            for changes,expected in [({},self.test_home/'.local/state/ouitak/logs'),
                                     ({'XDG_STATE_HOME':str(self.test_home/'custom state')},self.test_home/'custom state/ouitak/logs'),
                                     ({'OUITAK_LOG_ROOT':str(self.test_home/'direct logs')},self.test_home/'direct logs')]:
                env=dict(os.environ,**changes)
                result=subprocess.run(['bash',str(wrapper),'--print-log-dir'],cwd=cwd,env=env,capture_output=True,text=True,check=True)
                self.assertEqual(result.stdout.strip(),str(expected))
                self.assertFalse((Path(cwd)/'~').exists())
            for name in ('logs/run.log','~/local/runtime.log','runtime.log'):
                result=subprocess.run(['git','check-ignore','--stdin'],input=name+'\n',cwd=cc.ROOT,capture_output=True,text=True)
                self.assertEqual(result.returncode,0)

    def test_default_fixture_is_independent_of_operator_config(self):
        self.assertNotIn('DRONK_CONFIG',os.environ)
        settings=cfg.load_settings()
        self.assertEqual(settings['mode'],'CAPTURE')
        self.assertEqual(settings['tak'],{'forward_pilot_location':False})
        self.assertEqual(Path.home(),self.test_home)

    def test_demo_ignores_stored_devices_aliases_and_expected_ids(self):
        path=self.test_home/'.config/dronk/config.toml'
        path.write_text('mode="LIVE"\n[receivers]\nfront="/dev/DO_NOT_OPEN"\n[expected_receivers]\nfront="raw-test-id"\n[receiver_aliases]\nfront="north"\n[tak]\nforward_pilot_location=true\n')
        args=argparse.Namespace(demo=True,forward_live=False,mode=None,config=None)
        result=cfg.resolve(args,{}, {})
        self.assertEqual((result['mode'],result['devices'],result['expected'],result['receiver_aliases']),('DEMO',{},{},{}))
        with self.assertRaises(ValueError):cfg.resolve(args,{'explicit':'/dev/DO_NOT_OPEN'}, {})
        child=subprocess.Popen([sys.executable,str(cc.ROOT/'tools/dronk/command_center.py'),'--demo','--port','0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        first=child.stdout.readline(); child.terminate()
        out,err=child.communicate(timeout=5); out=first+out
        self.assertEqual(child.returncode,0)
        self.assertIn(b'host STOPPED',out)
        self.assertNotIn(b'DO_NOT_OPEN',out+err)

    def test_malformed_and_wrong_type_toml_errors_are_redacted(self):
        path=self.test_home/'.config/dronk/config.toml'
        for content in ('[tak\nhost="TEST_ONLY_SECRET"','mode=123','[receivers]\nfront=123','[tak]\nclient_p12=["TEST_ONLY_SECRET"]'):
            path.write_text(content)
            with self.assertRaises(ValueError) as error:cfg.resolve(argparse.Namespace(),{}, {})
            self.assertNotIn('TEST_ONLY_SECRET',str(error.exception))
            self.assertNotIn(str(path),str(error.exception))
        path.write_text('mode="CAPTURE"\n');path.chmod(0o640)
        with self.assertRaises(ValueError):cfg.load_settings()
        path.chmod(0o600);path.parent.chmod(0o755)
        self.assertEqual(cfg.load_settings()['mode'],'CAPTURE') # file confidentiality is enforced independently

    def test_jvm_startup_error_has_actionable_category_without_private_values(self):
        processor=cc.ROOT/'linux-bridge/build/install/linux-bridge/bin/linux-bridge'
        result=subprocess.run([str(processor),'--dronk','--stream','front','--live','--forward-live',
            '--tak-host','tak.example.test','--client-p12','/private/TEST_ONLY_CLIENT',
            '--password-file','/private/TEST_ONLY_PASSWORD','--ca-file','/private/TEST_ONLY_CA'],capture_output=True,text=True,timeout=8)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('E_CREDENTIAL_FILE',result.stdout+result.stderr)
        self.assertNotIn('TEST_ONLY',result.stdout+result.stderr)
        self.assertNotIn('/private/',result.stdout+result.stderr)

    def test_pilot_policy_defaults_and_overrides_never_change_local_data(self):
        self.assertFalse(cfg.resolve(argparse.Namespace(),{}, {})['forward_pilot_location'])
        with patch.dict(os.environ,{'DRONK_TAK_FORWARD_PILOT_LOCATION':'true'}):
            self.assertTrue(cfg.resolve(argparse.Namespace(),{}, {})['forward_pilot_location'])
            self.assertFalse(cfg.resolve(argparse.Namespace(forward_pilot_location=False),{}, {})['forward_pilot_location'])
        with patch.dict(os.environ,{'DRONK_TAK_FORWARD_PILOT_LOCATION':'not-a-boolean'}):
            with self.assertRaises(ValueError):cfg.resolve(argparse.Namespace(),{}, {})
