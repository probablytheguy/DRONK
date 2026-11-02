import importlib.util
from pathlib import Path
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from test_support import IsolatedConfigTestCase

PATH=Path(__file__).resolve().parents[2]/'security/check_public_tree.py'
SPEC=importlib.util.spec_from_file_location('public_scan',PATH)
scan=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(scan)


class PublicTreeScanTests(IsolatedConfigTestCase):
    def test_private_values_are_categories_not_results(self):
        private='TEST_ONLY_SENSITIVE_VALUE'
        results=scan.findings('setting='+private,[private])
        self.assertEqual(results,[(1,'private-exact-match')])
        self.assertNotIn(private,str(results))

    def test_examples_standards_and_versions_are_preserved(self):
        text='192.0.2.20 127.0.0.1 02:00:00:00:00:01\nISO/IEC 9899:1999 Chapter 6.5.2.3\nATAK 5.5.1.8'
        self.assertEqual(scan.findings(text),[])
        cgnat='.'.join(map(str,[100,64,0,1]))
        self.assertEqual(scan.findings(cgnat),[(1,'non-example-IP')])
