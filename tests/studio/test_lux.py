import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from studio import lux
from studio import lux_upload


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "front.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(100))
        self.events = []
        self.work = self.root / "job"
        uploaded = patch.object(lux_upload, "upload_image", return_value="https://cdn.example/helmet.png")
        self.upload_image = uploaded.start()
        self.addCleanup(uploaded.stop)

    def event(self, message, remote_id):
        self.events.append((message, remote_id))

    def test_creates_once_and_resumes_after_download_failure(self):
        result = {"status":3,"taskId":27,"outputs":[{"content":"https://cdn.example/model.zip"},
                                                   {"content":"https://cdn.example/model.glb"}]}
        with patch.dict(os.environ, {"AHOLO_API_KEY":"local-test-credential"}), \
                patch.object(lux,"_gateway",side_effect=[27,result,result]) as remote, \
                patch.object(lux,"_download",side_effect=[lux.NetworkError("interrupted"),None]) as download:
            with self.assertRaises(lux.NetworkError):
                lux.generate([self.image],self.work,self.event)
            self.assertIn(("Aholo 已接收生成任务", "27"),self.events)
            lux.generate([self.image],self.work,self.event)
            self.assertEqual(remote.call_count, 3)
            self.assertEqual(remote.call_args_list[0].args[3]["outputFormat"],["glb"])
            self.assertIn("img",remote.call_args_list[0].args[3])
            self.assertEqual(download.call_args.args[0],"https://cdn.example/model.glb")
            self.assertEqual(json.loads((self.work/"lux-task.json").read_text())["taskId"],"27")

    def test_multi_view_uses_imgs_exclusively(self):
        body, hashes = lux._prepare([self.image,self.image],lambda data,mime: "https://cdn.example/helmet.png")
        self.assertIn("imgs",body)
        self.assertNotIn("img",body)
        self.assertEqual(body["version"],"G1")
        self.assertEqual(len(hashes),2)

    def test_uncertain_creation_is_not_repeated(self):
        with patch.dict(os.environ,{"AHOLO_API_KEY":"local-test-credential"}), \
                patch.object(lux,"_gateway",side_effect=lux.NetworkError("unavailable")) as remote:
            for _ in range(2):
                with self.assertRaises(lux.LuxError):
                    lux.generate([self.image],self.work,self.event)
            self.assertEqual(remote.call_count,1)

    def test_wrong_key_can_be_changed_without_restart(self):
        envfile = self.root / "key.env"
        envfile.write_text("AHOLO_API_KEY=temporary-first-value\nAHOLO_REGION=cn",encoding="utf-8")
        with patch.dict(os.environ, {"HELMETFIT_AHOLO_KEY_FILE":str(envfile)},clear=True), \
                patch.object(lux,"_gateway",side_effect=[lux._provider_error(401),21,{"status":4,"taskId":21}]) as remote:
            with self.assertRaises(lux.LuxError):
                lux.generate([self.image],self.work,self.event)
            envfile.write_text("new-test-value",encoding="utf-8")
            with self.assertRaises(lux.LuxError):
                lux.generate([self.image],self.work,self.event)
            self.assertEqual(remote.call_args_list[1].args[0],"new-test-value")
            self.assertEqual(remote.call_args_list[1].args[1],"com")

    def test_corrupt_cache_recovers_existing_remote_only(self):
        self.work.mkdir()
        (self.work/"original.glb").write_bytes(b"broken")
        (self.work/"lux-task.json").write_text('{"region":"cn","taskId":"27","state":"complete"}')
        result = {"taskId":27,"status":3,"outputs":[{"content":"https://cdn.example/result.glb"}]}
        with patch.dict(os.environ,{"AHOLO_API_KEY":"example-test-key"}), \
                patch.object(lux,"_gateway",return_value=result) as remote, \
                patch.object(lux,"_download") as download:
            lux.generate([self.image],self.work,self.event)
            remote.assert_called_once_with("example-test-key","cn","generate/task/get?taskid=27")
            download.assert_called_once()

    def test_bad_journal_needs_id_then_recovers_without_creation(self):
        self.work.mkdir()
        record = self.work/"lux-task.json"
        record.write_text('[]')
        result = {"taskId":28,"status":3,"outputs":[{"content":"https://cdn.example/result.glb"}]}
        with patch.dict(os.environ,{"AHOLO_API_KEY":"example-test-key","AHOLO_REGION":"com"}), \
                patch.object(lux,"_gateway",return_value=result) as remote, \
                patch.object(lux,"_download"):
            with self.assertRaises(lux.LuxError):
                lux.generate([self.image],self.work,self.event)
            remote.assert_not_called()
            record.write_text('{not-json')
            lux.generate([self.image],self.work,self.event,resume_id="28")
            remote.assert_called_once_with("example-test-key","com","generate/task/get?taskid=28")

    def test_corrupt_cache_without_receipt_blocks_paid_creation(self):
        self.work.mkdir()
        (self.work/"original.glb").write_bytes(b"broken")
        with patch.dict(os.environ,{"AHOLO_API_KEY":"example-test-key"}), \
                patch.object(lux,"_gateway") as remote:
            with self.assertRaises(lux.LuxError):
                lux.generate([self.image],self.work,self.event)
            remote.assert_not_called()
        self.assertEqual(lux.read_journal(self.work/"lux-task.json")["state"],"submitting")

    def test_glb_download_requires_embedded_model(self):
        doc = json.dumps({"asset":{"version":"2.0"},"meshes":[{"primitives":[]}]}).encode()
        doc += b" " * (-len(doc)%4)
        blob = struct.pack("<4sIII4s", b"glTF",2,20+len(doc),len(doc),b"JSON")+doc
        output = self.root/"original.glb"
        with patch.object(lux, "build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(blob)
            lux._download("https://example.org/result",output)
            self.assertFalse(opener.return_value.open.call_args.args[0].has_header("Authorization"))
        self.assertEqual(output.read_bytes(), blob)
        output.write_bytes(blob[:-2])
        with self.assertRaises(lux.LuxError):
            lux._verify_glb(output)


class UploadTests(unittest.TestCase):
    def test_upload_resumes_only_missing_blocks(self):
        self.assertEqual(lux_upload._remaining_parts({"lackBlocks":["2-3","5"]},6),[2,3,5])
        self.assertEqual(lux_upload._remaining_parts({"lackBlocks":[]},6),[])
        with self.assertRaises(lux.LuxError):
            lux_upload._remaining_parts({"lackBlocks":["1-1000000"]},4)

    def test_single_uses_scoped_token_only_and_returns_url(self):
        token = dict(ousToken="scoped",globalDomain="https://ous-sg.kujiale.com",blockSize=16)
        with patch.object(lux_upload, "_read_json",side_effect=[
                token, {"c":"0","d":{"taskId":"1"}},
                {"c":"0","d":{"status":5,"url":"https://cdn.example/helmet.png"}}]) as http:
            link = lux_upload.upload_image("secret-test","com", b"hello","image/png")
            self.assertTrue(link.endswith("helmet.png"))
            requests = [call.args[0] for call in http.call_args_list]
            self.assertEqual(requests[0].headers["Authorization"],"secret-test")
            self.assertTrue(requests[1].full_url.endswith("/single/upload"))
            self.assertEqual(requests[1].headers["Ous-token-v2"],"scoped")
            self.assertNotIn("Authorization",requests[1].headers)
            self.assertNotIn("secret-test",repr(requests[1].data))

    def test_multiblock_uses_named_parts_and_one_token(self):
        token = dict(ousToken="scoped",globalDomain="https://ous-sg.kujiale.com",blockSize=3)
        with patch.object(lux_upload, "_read_json",side_effect=[
                token, {"c":"0","d":{"taskId":1}}, {"c":"0","d":None},{"c":"0","d":None},
                {"c":"0","d":{"status":5,"url":"https://cdn.example/helmet.png"}}]) as http:
            lux_upload.upload_image("test-secret","cn",b"hello","image/png")
            reqs = [call.args[0] for call in http.call_args_list]
            self.assertIn("blocks=2",reqs[1].full_url)
            self.assertIn(b'\r\nhel\r\n',reqs[2].data)
            self.assertIn(b'\r\nlo\r\n',reqs[3].data)


class WireContractTests(unittest.TestCase):
    """Run the complete adapter against the published response shapes."""
    def test_single_and_multiview_choose_the_glb_slot(self):
        for views in (1, 2):
            with self.subTest(views=views), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                image = root / "image.png"
                image.write_bytes(b"\x89PNG\r\n\x1a\nexample")
                job = root / "project"
                geo = json.dumps({"asset":{"version":"2.0"}, "meshes":[{"primitives":[]}]}).encode()
                geo += b" " * (-len(geo)%4)
                glb = struct.pack("<4sIII4s",b"glTF",2,20+len(geo),len(geo),b"JSON") + geo
                transaction = []
                def reply(request, **kwargs):
                    link = request.full_url
                    transaction.append(request)
                    if link.endswith("/asset/v1/token"):
                        data = {"ousToken":"upload-only", "globalDomain":"https://ous-sg.kujiale.com",
                                "blockSize":4096}
                    elif link.endswith("/single/upload"):
                        data = {"c":"0","d":{"taskId":"upload-01"}}
                    elif link.endswith("/upload/status"):
                        data = {"c":"0","d":{"status":5,"url":"https://cdn.example/ref.png"}}
                    elif link.endswith("/generate/img-to-3d/task/create"):
                        data = {"c":"0","d":1389515}
                    elif link.endswith("/generate/task/get?taskid=1389515"):
                        data = {"c":"0","d":{"taskId":1389515,"status":3,"outputs":[
                            {"content":"https://cdn.example/results.zip"},{"content":"https://cdn.example/result.glb"}]}}
                    elif link == "https://cdn.example/result.glb":
                        return io.BytesIO(glb)
                    else:
                        self.fail("unexpected request")
                    return io.BytesIO(json.dumps(data).encode())
                with patch.dict(os.environ,{"AHOLO_API_KEY":"local-test-key","AHOLO_REGION":"com"}), \
                        patch.object(lux, "build_opener") as opener:
                    opener.return_value.open.side_effect = reply
                    output = lux.generate([image]*views,job,lambda *_:None)
                    self.assertEqual(output.read_bytes(),glb)
                    before = len(transaction)
                    self.assertEqual(lux.generate([image]*views,job,lambda *_:None),output)
                    self.assertEqual(len(transaction),before)
                body = next(json.loads(r.data) for r in transaction if r.full_url.endswith("/task/create"))
                self.assertEqual(body["img"] if views == 1 else body["imgs"],
                                 "https://cdn.example/ref.png" if views == 1 else ["https://cdn.example/ref.png"]*2)
                self.assertNotIn("local-test-key",(job / "lux-task.json").read_text())


if __name__ == "__main__":
    unittest.main()
