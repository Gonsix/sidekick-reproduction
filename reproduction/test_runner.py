#!/usr/bin/env python3
"""Fault-injection checks. Run in the dedicated privileged container."""
import contextlib, io, json, os, pathlib, subprocess, sys, tempfile, unittest
from unittest import mock
import run
class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid()!=0:raise unittest.SkipTest("network namespace tests need the dedicated container")
    @staticmethod
    def namespaces():
        return {line.split()[0] for line in subprocess.check_output(["ip","netns","list"],text=True).splitlines() if line}
    def test_missing_executable_fails_and_cleans_own_network(self):
        before=self.namespaces()
        with tempfile.TemporaryDirectory(prefix="sidekick-fault-") as d:
            out=pathlib.Path(d)/"trial"
            with mock.patch.object(run,"BIN",pathlib.Path("/__sidekick_missing_binary__")):
                with self.assertRaisesRegex(RuntimeError,"sender failed"):
                    run.run_case(run.Case("b","e2e",size=1000),out,False)
            self.assertEqual(json.loads((out/"manifest.json").read_text())["status"],"failed")
            self.assertFalse((out/"result.json").exists())
        self.assertEqual(self.namespaces(),before)
    def test_exception_during_network_use_cleans_up(self):
        before=self.namespaces()
        with tempfile.TemporaryDirectory(prefix="sidekick-cleanup-") as d:
            with self.assertRaisesRegex(RuntimeError,"injected failure"):
                with run.Topology(run.Case("c","e2e"),pathlib.Path(d)) as net:
                    net.start(0,["sleep","60"],"child")
                    child=net.processes[-1]
                    raise RuntimeError("injected failure")
            self.assertIsNotNone(child.poll())
        self.assertEqual(self.namespaces(),before)
    def test_existing_results_are_refused(self):
        before=self.namespaces()
        with tempfile.TemporaryDirectory(prefix="sidekick-existing-") as d:
            marker=pathlib.Path(d)/"keep";marker.write_text("existing measurement")
            with mock.patch.object(sys,"argv",["run.py","--figure","b","--output",d]):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:run.main()
            self.assertEqual(error.exception.code,2)
            self.assertEqual(marker.read_text(),"existing measurement")
            self.assertEqual(list(pathlib.Path(d).iterdir()),[marker])
        self.assertEqual(self.namespaces(),before)
if __name__=="__main__":unittest.main()
