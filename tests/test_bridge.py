import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import http.client
import json
import tempfile
import unittest
from unittest.mock import patch
from tracking_core import Store
from bridge import Bridge

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="tracking-bridge-test-")
        self.store=Store(Path(self.temp.name)/"test.sqlite3")
        # Bind an ephemeral, test-only loopback port; no production listener touched.
        self.port_patch=patch("bridge.PORT",0)
        self.port_patch.start()
        self.bridge=Bridge(self.store)
        self.bridge.start()
        self.assertIsNone(self.bridge.error or None)
        self.port=self.bridge.server.server_address[1]
    def tearDown(self):
        self.bridge.close()
        self.port_patch.stop()
        self.store.close()
        self.temp.cleanup()
    def request(self,payload,token=True,origin=None):
        connection=http.client.HTTPConnection("127.0.0.1",self.port,timeout=3)
        headers={"Host":"127.0.0.1:0","Content-Type":"application/json"}
        if token:
            headers["X-Tracking-Token"]=self.bridge.token
        if origin:
            headers["Origin"]=origin
        connection.request("POST","/api/browser",json.dumps(payload),headers)
        response=connection.getresponse()
        status=response.status
        response.read()
        connection.close()
        return status
    def test_authentication_and_origin(self):
        self.assertEqual(self.request({},token=False),401)
        self.assertEqual(self.request({},origin="https://example.com"),403)
    def test_hostname_only_no_persistence(self):
        payload=dict(Client="chrome",ActiveHost="youtube.com",Focused=True,Hosts=["youtube.com","youtube.com","https://private.example/path?q=secret"])
        self.assertEqual(self.request(payload,origin="chrome-extension://"+"a"*32),200)
        snapshot=self.bridge.current()["chrome.exe"]
        self.assertEqual(snapshot["hosts"],{"youtube.com"})
        self.assertEqual(snapshot["active"],"youtube.com")
        self.store.flush()
        self.assertEqual(self.store.report("2026-10-15"),[])
        self.assertNotIn(b"private.example",self.store.path.read_bytes())

if __name__=="__main__":
    unittest.main()
