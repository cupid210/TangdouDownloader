"""Bounded parallel byte ranges with strict validation before publishing a file."""

from concurrent.futures import ThreadPoolExecutor
import re
import threading

import requests

from headers import headers
from network import TIMEOUT, create_session

MIN_SIZE = 8 * 1024 * 1024
CONNECTIONS = 4


class RangeUnsupported(ValueError):
    pass


def try_segmented(url, target, partial, session, stop=None):
    """Return (size, skipped) when handled, or None to use one connection."""
    header = headers(url).buildHeader()
    header.update({"Range": "bytes=0-0", "Accept-Encoding": "identity"})
    with session.get(url, headers=header, stream=True, timeout=TIMEOUT) as response:
        if response.status_code in (400, 403, 405, 416):
            return None
        response.raise_for_status()
        if response.status_code != 206:
            return None
        match = re.fullmatch(r"bytes 0-0/(\d+)", response.headers.get("Content-Range", ""))
        if not match:
            return None
        total = int(match.group(1))
        validator = response.headers.get("ETag") or response.headers.get("Last-Modified")
        if validator and validator.startswith("W/"):
            validator = response.headers.get("Last-Modified")
        if total < MIN_SIZE:
            return None
    if target.is_file() and target.stat().st_size == total:
        print("已存在，跳过：{}".format(target.name), flush=True)
        return total, True

    cancelled = threading.Event()

    def check_cancelled():
        if cancelled.is_set() or (stop and stop.is_set()):
            raise InterruptedError("下载已取消")

    def transfer(begin, end):
        request_headers = header.copy()
        request_headers["Range"] = "bytes={}-{}".format(begin, end)
        if validator:
            request_headers["If-Range"] = validator
        with create_session() as connection:
            for attempt in range(3):
                check_cancelled()
                try:
                    with connection.get(url, headers=request_headers, stream=True,
                                        timeout=TIMEOUT) as response:
                        response.raise_for_status()
                        expected_range = "bytes {}-{}/{}".format(begin, end, total)
                        if response.status_code != 206 or response.headers.get("Content-Range") != expected_range:
                            raise RangeUnsupported("服务器未正确返回分段数据")
                        content_type = response.headers.get("Content-Type", "").lower()
                        if "text/" in content_type or "json" in content_type:
                            raise RangeUnsupported("分段请求返回了非视频内容")
                        size = 0
                        with partial.open("r+b") as output:
                            output.seek(begin)
                            for chunk in response.iter_content(256 * 1024):
                                check_cancelled()
                                if size + len(chunk) > end - begin + 1:
                                    raise RangeUnsupported("分段长度超出请求范围")
                                output.write(chunk)
                                size += len(chunk)
                        if size != end - begin + 1:
                            raise requests.exceptions.ChunkedEncodingError("分段长度不完整")
                    return
                except requests.RequestException as error:
                    retryable = error.response is None or error.response.status_code in (429, 500, 502, 503, 504)
                    if attempt == 2 or not retryable:
                        raise
                    cancelled.wait(0.5 * (attempt + 1))

    print("正在下载（4 路分段）：{}".format(target.name), flush=True)
    with partial.open("wb") as output:
        output.truncate(total)
    try:
        with ThreadPoolExecutor(max_workers=CONNECTIONS) as executor:
            futures = []
            for index in range(CONNECTIONS):
                begin = total * index // CONNECTIONS
                end = total * (index + 1) // CONNECTIONS - 1
                futures.append(executor.submit(transfer, begin, end))
            try:
                for future in futures:
                    future.result()
            except BaseException:
                cancelled.set()
                raise
    except RangeUnsupported:
        print("分段请求不受支持，切换为普通下载：{}".format(target.name), flush=True)
        return None
    return total, False
