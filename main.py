"""Paste multiple Tangdou links and download the highest available quality."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import re
import sys
import threading
import time

import requests

from get_vid import get_vid_set
from headers import headers
from network import TIMEOUT, create_session
from segmented import try_segmented
import tangdou


def default_output():
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "Download"


def safe_filename(name, max_length=100):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")
    return name[:max_length].rstrip(" .") or "video"


def downloader(name, url, path, session=None, stop=None):
    """Stream to a temporary file; publish only a complete, nonempty response."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    target = path / (safe_filename(name, 180) + ".mp4")
    partial = target.with_suffix(".mp4.part")
    owned = session is None
    session = session or create_session()
    start = time.monotonic()
    header = headers(url).buildHeader()
    header["Accept-Encoding"] = "identity"
    try:
        if stop and stop.is_set():
            raise InterruptedError("下载已取消")
        segmented = try_segmented(url, target, partial, session, stop)
        if segmented is not None:
            size, skipped = segmented
            if not skipped:
                os.replace(str(partial), str(target))
                elapsed = max(time.monotonic() - start, 0.001)
                print("下载完成：{} | {:.1f} MB | {:.2f} MB/s".format(
                    target.name, size / 1024**2, size / 1024**2 / elapsed), flush=True)
            return target
        for attempt in range(3):
            if stop and stop.is_set():
                raise InterruptedError("下载已取消")
            try:
                with session.get(url, headers=header, stream=True, timeout=TIMEOUT) as response:
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError("下载响应不完整：HTTP {}".format(response.status_code))
                    content_type = response.headers.get("Content-Type", "").lower()
                    if "text/" in content_type or "json" in content_type:
                        raise ValueError("服务器返回了非视频内容")
                    length = response.headers.get("Content-Length")
                    expected = int(length) if length is not None else None
                    if expected and target.is_file() and target.stat().st_size == expected:
                        print("已存在，跳过：{}".format(target.name), flush=True)
                        return target
                    size = 0
                    print("正在下载：{}".format(target.name), flush=True)
                    with partial.open("wb") as output:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if stop and stop.is_set():
                                raise InterruptedError("下载已取消")
                            if chunk:
                                output.write(chunk)
                                size += len(chunk)
                    if not size or (expected is not None and size != expected):
                        raise requests.exceptions.ChunkedEncodingError("文件长度不完整")
                os.replace(str(partial), str(target))
                elapsed = max(time.monotonic() - start, 0.001)
                print("下载完成：{} | {:.1f} MB | {:.2f} MB/s".format(
                    target.name, size / 1024**2, size / 1024**2 / elapsed), flush=True)
                return target
            except requests.RequestException as error:
                retryable = error.response is None or error.response.status_code in (429, 500, 502, 503, 504)
                if attempt == 2 or not retryable:
                    raise
                if stop:
                    stop.wait(0.5 * (attempt + 1))
                else:
                    time.sleep(0.5 * (attempt + 1))
    finally:
        if partial.exists():
            partial.unlink()
        if owned:
            session.close()


def download_one(vid, path, stop):
    if stop.is_set():
        raise InterruptedError("下载已取消")
    # Sessions are never shared between threads; API/probe/download reuse connections.
    with create_session() as session:
        info = tangdou.VideoAPI(session).get_video_info(vid)
        clarity, url = next(iter(info["urls"].items()))
        print("vid {}：已选择最高可用画质 {}".format(vid, clarity), flush=True)
        name = "{}_{}_{}".format(safe_filename(info["name"]), vid, clarity)
        return downloader(name, url, path, session, stop)


def batch_download(vids, path=None, max_threads=5):
    vids = list(dict.fromkeys(str(vid) for vid in vids))
    if not 1 <= max_threads <= 16:
        raise ValueError("并发数必须在 1 到 16 之间")
    path = Path(path) if path is not None else default_output()
    path.mkdir(parents=True, exist_ok=True)
    print("共 {} 个视频，最多同时下载 {} 个，保存到：{}".format(
        len(vids), min(max_threads, len(vids)), path.resolve()), flush=True)
    failed, completed = [], 0
    stop = threading.Event()
    executor = ThreadPoolExecutor(max_workers=max_threads)
    futures = {}
    try:
        futures = {executor.submit(download_one, vid, path, stop): vid for vid in vids}
        for future in as_completed(futures):
            vid = futures[future]
            try:
                future.result()
                completed += 1
            except Exception as error:
                failed.append(vid)
                print("vid {} 下载失败：{}".format(vid, error), flush=True)
    except KeyboardInterrupt:
        stop.set()
        for future in futures:
            future.cancel()
        print("\n正在停止下载，请稍候……", flush=True)
        raise
    finally:
        executor.shutdown(wait=True)
    print("本批完成：成功/已存在 {}，失败 {}".format(completed, len(failed)), flush=True)
    if failed:
        print("失败 vid（可复制重试）：{}".format(" ".join(failed)), flush=True)
    return failed


def read_links():
    print("\n请粘贴多个含 vid 的链接或 vid 编号（每行一个，也可用空格分隔）。")
    print("粘贴完再按一次回车开始下载；直接回车退出。")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="批量下载糖豆视频，自动选择最高可用清晰度")
    parser.add_argument("links", nargs="*", help="含 vid 的链接或 vid 编号")
    parser.add_argument("-f", "--file", type=Path, help="从 UTF-8 文本文件读取多个链接")
    parser.add_argument("--json-dir", type=Path, help="读取旧版 DownloadList JSON 文件")
    parser.add_argument("-o", "--output", type=Path, default=default_output(), help="视频保存目录")
    parser.add_argument("-j", "--workers", type=int, choices=range(1, 17), default=5,
                        metavar="1-16", help="同时下载的视频数，默认 5")
    args = parser.parse_args(argv)
    print("=================== 糖豆视频批量下载器 ===================")
    print("自动选择最高可用画质，直接保存 MP4。")
    supplied = bool(args.links or args.file or args.json_dir)
    text = "\n".join(args.links)
    if args.file:
        text += "\n" + args.file.read_text(encoding="utf-8-sig")
    vids, invalid = tangdou.parse_inputs(text)
    if args.json_dir:
        vids = list(dict.fromkeys(vids + [str(vid) for vid in sorted(get_vid_set(args.json_dir), key=str)]))
    while True:
        if not supplied:
            text = read_links()
            if not text:
                return 0
            vids, invalid = tangdou.parse_inputs(text)
        for value in invalid:
            print("忽略无有效 vid 的链接：{}".format(value))
        if not vids:
            print("未找到有效链接，请提供包含数字 vid 参数的链接或 vid 编号。")
            if supplied:
                return 1
            continue
        failed = batch_download(vids, args.output, args.workers)
        if supplied:
            return 1 if failed else 0


if __name__ == "__main__":
    # Frozen applications do not honor PYTHONIOENCODING; keep Chinese console
    # text and redirected output consistent with the Windows launcher.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n已退出。")
        sys.exit(130)
    except (OSError, ValueError) as error:
        print("错误：{}".format(error), file=sys.stderr)
        sys.exit(1)
