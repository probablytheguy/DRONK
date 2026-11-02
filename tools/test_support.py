"""Per-test operator configuration isolation; subprocesses inherit these fixtures."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class IsolatedConfigTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        temporary=tempfile.TemporaryDirectory(prefix='dronk-test-config-')
        self.addCleanup(temporary.cleanup)
        self.test_home=Path(temporary.name)
        directory=self.test_home/'.config/dronk'
        directory.mkdir(parents=True,mode=0o700)
        config=directory/'config.toml'
        config.write_text('mode="CAPTURE"\n[tak]\nforward_pilot_location=false\n')
        config.chmod(0o600)
        environment={k:v for k,v in os.environ.items() if not k.startswith('DRONK_') and k not in ('XDG_CONFIG_HOME','XDG_STATE_HOME','OUITAK_LOG_ROOT')}
        environment.update(HOME=str(self.test_home),XDG_CONFIG_HOME=str(self.test_home/'.config'))
        context=patch.dict(os.environ,environment,clear=True)
        context.start();self.addCleanup(context.stop)
