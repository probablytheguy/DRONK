"""Security tests use private temporary settings and synthetic local resources only."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from test_support import IsolatedConfigTestCase
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import configuration as cfg
import command_center as cc


class ConfigurationTests(IsolatedConfigTestCase):
    def test_environment_overrides_private_toml_without_password_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml'
            path.write_text('mode="CAPTURE"\n[tak]\nhost="tak.example.test"\nport=8089\n')
            path.chmod(0o600)
            settings=cfg.load_settings(environ={'DRONK_CONFIG':str(path),'DRONK_MODE':'LIVE','DRONK_TAK_HOST':'alternate.example.test',
                'DRONK_TAK_PORT':'9000','DRONK_TAK_CONNECT_ADDRESS':'192.0.2.20','DRONK_TAK_CLIENT_P12':'private.p12',
                'DRONK_TAK_CA_FILE':'ca.pem','DRONK_TAK_PASSWORD_FILE':'password-file'})
            self.assertEqual(settings['mode'],'LIVE')
            self.assertEqual(settings['tak']['host'],'alternate.example.test')
            self.assertEqual(settings['tak']['connect_address'],'192.0.2.20')
            self.assertEqual(settings['tak']['password_file'],'password-file')
            self.assertNotIn('password',settings['tak'])

    def test_private_configuration_mode_and_errors_do_not_echo_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'private.toml'
            path.write_text('[tak]\npassword="TEST_ONLY_SECRET_MUST_NOT_APPEAR"\n')
            path.chmod(0o600)
            with self.assertRaises(ValueError) as error: cfg.load_settings(path,environ={})
            self.assertNotIn('TEST_ONLY_SECRET',str(error.exception))
            self.assertNotIn(str(path),str(error.exception))
            path.chmod(0o644)
            with self.assertRaises(ValueError): cfg.load_settings(path,environ={})
            with self.assertRaises(ValueError): cfg.load_settings(environ={'DRONK_TAK_PASSWORD':'TEST_ONLY_SECRET'})

    def test_demo_and_capture_refuse_network_or_devices_before_spawn(self):
        state=cc.State(True)
        with patch.object(cc.subprocess,'Popen',side_effect=AssertionError('process spawned')):
            for settings in ({'mode':'DEMO','devices':{'front':'/dev/DO_NOT_OPEN'}},
                             {'mode':'DEMO','forward_live':True},
                             {'mode':'CAPTURE','forward_live':True,'devices':{'front':'/dev/DO_NOT_OPEN'}}):
                with self.assertRaises(ValueError): cc.HostController(state,settings).start()

    def test_live_requires_complete_configuration_before_spawn(self):
        complete=dict(mode='LIVE',devices={'front':'/dev/DO_NOT_OPEN'},tak_host='tak.example.test',
                      client_p12='private.p12',password_file='password-file',ca_file='ca.pem')
        for field in ('tak_host','client_p12','password_file','ca_file'):
            settings=dict(complete);del settings[field]
            with patch.object(cc.subprocess,'Popen',side_effect=AssertionError('process spawned')):
                with self.assertRaises(ValueError): cc.HostController(cc.State(),settings).start()
        validated=cfg.validate_runtime(complete)
        self.assertEqual(validated['tak_connect_address'],'tak.example.test')
        self.assertEqual(validated['tak_port'],8089)
        self.assertTrue(validated['forward_live'])

    def test_capture_and_live_dashboard_modes_remain_distinct(self):
        from datetime import datetime,timezone
        for mode in ('CAPTURE','LIVE'):
            state=cc.State(mode=mode)
            state.apply({'kind':'receiver','receiver':{'stream':'front','connected':True,
                        'last_seen':datetime.now(timezone.utc).isoformat()}})
            self.assertEqual(state.snapshot()['mode'],mode)
            self.assertEqual(state.snapshot()['operation_mode'],mode)

    def test_alias_defaults_and_collisions(self):
        base=dict(mode='CAPTURE',devices={'front':'selected','rear':'other'})
        for aliases in ({'front':'rear'},{'front':'north','rear':'north'},{'front':'c5-021122334455'}):
            with self.assertRaises(ValueError): cfg.validate_runtime(dict(base,receiver_aliases=aliases))
        self.assertEqual(cfg.validate_runtime(dict(base,receiver_aliases={'front':'north'}))['receiver_aliases'],{'front':'north'})

    def test_cli_precedence_and_mode_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml';path.write_text('mode="CAPTURE"\n');path.chmod(0o600)
            args=argparse.Namespace(config=str(path),mode='CAPTURE',demo=False,forward_live=False,
                                    tak_host='cli.example.test',tak_port=None)
            with patch.dict(os.environ,{'DRONK_MODE':'LIVE','DRONK_TAK_HOST':'env.example.test'},clear=True):
                resolved=cfg.resolve(args,{'front':'selected'}, {})
            self.assertEqual(resolved['mode'],'CAPTURE')
            self.assertFalse(resolved['forward_live'])
            self.assertEqual(resolved['tak_host'],'cli.example.test')

    def test_credential_metadata_is_checked_without_reading_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            config={'mode':'LIVE'}
            for key in ('client_p12','password_file','ca_file'):
                path=Path(directory)/key;path.write_text('TEST_ONLY_LOCAL_CREDENTIAL');path.chmod(0o600)
                config[key]=str(path)
            with patch.object(Path,'read_text',side_effect=AssertionError('credential content read')):
                cfg.validate_credentials(config)
            Path(config['password_file']).chmod(0o644)
            with self.assertRaises(ValueError) as error:cfg.validate_credentials(config)
            self.assertNotIn(str(directory),str(error.exception))
        with patch.object(Path,'stat',side_effect=AssertionError('credential metadata read')):
            cfg.validate_credentials({'mode':'DEMO'})
            cfg.validate_credentials({'mode':'CAPTURE'})

    def test_accidental_password_option_is_not_echoed(self):
        result=subprocess.run([sys.executable,str(cc.ROOT/'tools/dronk/command_center.py'),'--password','TEST_ONLY_SECRET_VALUE'],
                              capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn(b'TEST_ONLY_SECRET_VALUE',result.stdout+result.stderr)

    def test_bridge_incomplete_live_errors_omit_private_paths(self):
        processor=cc.ROOT/'linux-bridge/build/install/linux-bridge/bin/linux-bridge'
        env={k:v for k,v in os.environ.items() if not k.startswith('DRONK_')}
        result=subprocess.run([str(processor),'--live','--forward-live','--tak-host','tak.example.test',
                               '--client-p12','/private/TEST_ONLY_FILE.p12','--password-file','/private/TEST_ONLY_PASSWORD_FILE',
                               '--ca-file','/private/TEST_ONLY_CA.pem'],capture_output=True,env=env,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn(b'TEST_ONLY',result.stdout+result.stderr)
        self.assertNotIn(b'/private/',result.stdout+result.stderr)
