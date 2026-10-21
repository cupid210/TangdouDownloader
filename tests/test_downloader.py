import io
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests

import main
import segmented
import tangdou


DATA = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 1024


class Handler(BaseHTTPRequestHandler):
    calls = []
    broken = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).calls.append((self.path, self.headers.get("Range")))
        if self.path == "/no-range" and self.headers.get("Range"):
            self.send_error(416)
            return
        if self.path.startswith("/missing") or ("P.mp4" in self.path and "H720P" not in self.path):
            self.send_error(404)
            return
        if self.path == "/denied":
            self.send_error(403)
            return
        if self.path == "/html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"error page")
            return
        if self.path == "/broken" and not self.headers.get("Range"):
            type(self).broken += 1
        partial = self.headers.get("Range") == "bytes=0-0"
        body = DATA[:1] if partial else DATA
        range_start, range_end = 0, 0
        if self.path.startswith("/range") and self.headers.get("Range"):
            range_start, range_end = map(int, self.headers["Range"][6:].split("-"))
            body = DATA[range_start:range_end + 1]
            partial = True
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "video/mp4")
        if partial:
            self.send_header("Content-Range", "bytes {}-{}/{}".format(range_start, range_end, len(DATA)))
        if self.path != "/unknown-size":
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.path == "/range-broken" and range_end > 0:
            self.wfile.write(body[:10])
        elif self.path == "/broken" and type(self).broken == 1 and not partial:
            self.wfile.write(body[:10])
        else:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass


class DownloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:{}".format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        Handler.calls = []
        Handler.broken = 0

    def test_extract_shared_links_and_deduplicate(self):
        vids, invalid = tangdou.parse_inputs(
            '分享 https://www.tangdouddn.com/h5/play?vid=123&utm_source=android\n'
            '[视频](https://example.com/?vid=456) 123，789;https://example.com/?vid=bad')
        self.assertEqual(vids, ["123", "456", "789"])
        self.assertEqual(len(invalid), 1)

    def test_html_escaped_link(self):
        self.assertEqual(tangdou.get_vid("https://x/?a=1&amp;vid=123"), "123")
        self.assertIsNone(tangdou.get_vid("https://x/?vid=abc"))

    def test_candidates_preserve_resolution_and_orientation(self):
        candidates = tangdou.quality_candidates(self.base + "/1_V2160P.mp4?sign=abc")
        self.assertEqual(candidates[0][0], "V2160P")
        self.assertEqual(candidates[2][0], "V1080P")
        self.assertTrue(all(url.endswith("?sign=abc") for _, url in candidates))

    def test_highest_quality_stops_probing_lower_variants(self):
        api = tangdou.VideoAPI()
        self.addCleanup(api.close)
        with patch.object(api, "get_api_info", return_value={"data": {
                "title": "sample", "video_url": self.base + "/1_H360P.mp4"}}):
            info = api.get_video_info("123")
        self.assertEqual(list(info["urls"]), ["H720P"])
        self.assertEqual(len(Handler.calls), 3)
        self.assertTrue(all(value == "bytes=0-0" for _, value in Handler.calls))

    def test_api_missing_video_fails_clearly(self):
        api = tangdou.VideoAPI()
        self.addCleanup(api.close)
        with patch.object(api, "get_api_info", return_value={"data": None}):
            with self.assertRaises(ValueError):
                api.get_video_info("123")

    def test_download_exact_bytes_and_no_partial(self):
        result = main.downloader("sample", self.base + "/video", self.path)
        self.assertEqual(result.read_bytes(), DATA)
        self.assertFalse(list(self.path.glob("*.part")))

    def test_segmented_download_reassembles_exact_bytes(self):
        with patch("segmented.MIN_SIZE", 1):
            result = main.downloader("sample", self.base + "/range", self.path)
        self.assertEqual(result.read_bytes(), DATA)
        ranges = [value for _, value in Handler.calls if value != "bytes=0-0"]
        self.assertEqual(len(ranges), 4)
        self.assertFalse(list(self.path.glob("*.part")))

    def test_segmented_falls_back_when_server_ignores_ranges(self):
        with patch("segmented.MIN_SIZE", 1):
            result = main.downloader("sample", self.base + "/video", self.path)
        self.assertEqual(result.read_bytes(), DATA)
        self.assertEqual(Handler.calls[-1][1], None)

    def test_server_rejects_range_header_but_normal_download_works(self):
        api = tangdou.VideoAPI()
        self.addCleanup(api.close)
        with patch.object(api, "get_api_info", return_value={"data": {
                "title": "sample", "video_url": self.base + "/no-range"}}):
            info = api.get_video_info("123")
        result = main.downloader("sample", info["urls"]["unknown"], self.path)
        self.assertEqual(result.read_bytes(), DATA)

    def test_segmented_incomplete_never_publishes_file(self):
        with patch("segmented.MIN_SIZE", 1):
            with self.assertRaises(requests.RequestException):
                main.downloader("sample", self.base + "/range-broken", self.path)
        self.assertFalse(list(self.path.iterdir()))

    def test_unknown_content_length(self):
        result = main.downloader("sample", self.base + "/unknown-size", self.path)
        self.assertEqual(result.read_bytes(), DATA)

    def test_retry_truncated_stream(self):
        result = main.downloader("sample", self.base + "/broken", self.path)
        self.assertEqual(result.read_bytes(), DATA)
        self.assertEqual(Handler.broken, 2)

    def test_skip_existing_complete_file(self):
        target = self.path / "sample.mp4"
        target.write_bytes(DATA)
        with patch("os.replace") as replace:
            main.downloader("sample", self.base + "/video", self.path)
        replace.assert_not_called()

    def test_http_failure_preserves_existing_file(self):
        target = self.path / "sample.mp4"
        target.write_bytes(b"existing")
        with self.assertRaises(requests.HTTPError):
            main.downloader("sample", self.base + "/denied", self.path)
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertFalse(list(self.path.glob("*.part")))

    def test_reject_error_page(self):
        with self.assertRaises(ValueError):
            main.downloader("sample", self.base + "/html", self.path)
        self.assertFalse(list(self.path.iterdir()))

    def test_cancel_does_not_publish_file(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(InterruptedError):
            main.downloader("sample", self.base + "/video", self.path, stop=stop)
        self.assertFalse(list(self.path.iterdir()))

    def test_long_titles_keep_vid_and_quality(self):
        name = "{}_123_H1080P".format(main.safe_filename("标题" * 100))
        result = main.downloader(name, self.base + "/video", self.path)
        self.assertTrue(result.name.endswith("_123_H1080P.mp4"))

    def test_batch_runs_concurrently_deduplicates_and_continues(self):
        barrier = threading.Barrier(2, timeout=5)
        calls = []

        def worker(vid, path, stop):
            calls.append(vid)
            barrier.wait()
            if vid == "2":
                raise ValueError("unavailable")

        with patch("main.download_one", side_effect=worker):
            failed = main.batch_download(["1", "1", "2"], self.path, 2)
        self.assertEqual(sorted(calls), ["1", "2"])
        self.assertEqual(failed, ["2"])

    def test_cli_interactive_paste_without_extra_prompts(self):
        with patch("builtins.input", side_effect=["https://x/?vid=123", "456", "", ""]):
            with patch("main.batch_download", return_value=[]) as batch:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main.main([]), 0)
        self.assertEqual(batch.call_args.args[0], ["123", "456"])

    def test_cli_file_and_failure_exit_status(self):
        links = self.path / "links.txt"
        links.write_text("\ufeffhttps://x/?vid=123\n456", encoding="utf-8")
        with patch("main.batch_download", return_value=["456"]) as batch:
            self.assertEqual(main.main(["--file", str(links)]), 1)
        self.assertEqual(batch.call_args.args[0], ["123", "456"])


if __name__ == "__main__":
    unittest.main()
