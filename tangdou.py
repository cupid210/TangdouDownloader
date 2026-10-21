"""Resolve Tangdou links and select the highest available video quality."""

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlparse

import requests
from headers import headers
from network import TIMEOUT, create_session


def get_vid(value):
    value = unescape(str(value)).strip().rstrip(".,;，；。)）]}")
    if value.isdigit():
        return value
    try:
        values = parse_qs(urlparse(value).query).get("vid", [])
    except ValueError:
        return None
    return values[0] if values and values[0].isdigit() else None


def parse_inputs(text):
    """Extract links/IDs from pasted text, preserving order and removing duplicates."""
    tokens = re.findall(r'https?://[^\s<>"，；,;]+|(?<![\w=])\d+(?!\w)', text)
    vids, invalid = [], []
    seen = set()
    for token in tokens:
        vid = get_vid(token)
        if vid is None:
            invalid.append(token)
        elif vid not in seen:
            seen.add(vid)
            vids.append(vid)
    return vids, invalid


def quality_candidates(url):
    """Try higher resolutions first; prefer the source orientation at equal height."""
    match = re.search(r"_([HV])(\d+)P", url)
    if not match:
        return [("unknown", url)]
    orientation, resolution = match.groups()
    resolutions = sorted({1080, 720, 540, 360, int(resolution)}, reverse=True)
    candidates = []
    for height in resolutions:
        for direction in (orientation, "V" if orientation == "H" else "H"):
            clarity = "{}{}P".format(direction, height)
            candidates.append((clarity, re.sub(r"_[HV]\d+P", "_" + clarity, url)))
    return candidates


class VideoAPI:
    def __init__(self, session=None):
        self.url = "https://api-h5.tangdou.com/sample/share/main?vid="
        self.session = session or create_session()
        self._owns_session = session is None

    def close(self):
        if self._owns_session:
            self.session.close()

    def get_api_info(self, vid):
        vid = str(vid)
        if not vid.isdigit():
            raise ValueError("vid 必须为数字")
        with self.session.get(self.url + vid, headers=headers(self.url).buildHeader(),
                              timeout=TIMEOUT) as response:
            response.raise_for_status()
            return response.json()

    def get_video_info(self, value):
        vid = get_vid(value)
        if vid is None:
            raise ValueError("链接中找不到有效的 vid")
        data = self.get_api_info(vid).get("data")
        if not isinstance(data, dict) or not data.get("video_url"):
            raise ValueError("vid {} 没有可下载的视频地址".format(vid))
        source = data["video_url"]
        if not isinstance(source, str) or urlparse(source).scheme not in ("http", "https"):
            raise ValueError("视频地址格式无效")
        for clarity, url in quality_candidates(source):
            header = headers(url).buildHeader()
            header.update({"Range": "bytes=0-0", "Accept-Encoding": "identity"})
            available = False
            for use_range in (True, False):
                if not use_range:
                    header.pop("Range", None)
                with self.session.get(url, headers=header, stream=True,
                                      timeout=TIMEOUT) as response:
                    if use_range and response.status_code in (400, 403, 405, 416):
                        continue
                    if response.status_code in (404, 410):
                        break
                    response.raise_for_status()
                    if response.status_code not in (200, 206):
                        raise ValueError("无法确认视频画质：HTTP {}".format(response.status_code))
                    content_type = response.headers.get("Content-Type", "").lower()
                    if "text/" in content_type or "json" in content_type:
                        raise ValueError("视频服务器返回了非视频内容")
                    available = True
                    break
            if not available:
                continue
            return {"name": data.get("title") or vid, "urls": {clarity: url}}
        raise ValueError("vid {} 没有可用的清晰度".format(vid))

class HTML(object):
    '''
    Obtain the original video address by visiting the parsing HTML page 
    and looking for the video tag
    '''

    def __init__(self):
        self.url = 'http://share.tangdou.com/splay.php?vid='

    def get_video_url(self, url):
        '''parse the webpage to get the video address
        :param url: tangdou video url that containing vid or just vid 
        :param return: 
        '''
        vid = get_vid(url)
        if vid is None:
            raise ValueError("can not find 'vid' parameter from '{}'".format(url))

        header = headers(self.url).buildHeader()
        resp = requests.get(self.url + vid, headers=header)
        resp.encoding = resp.apparent_encoding
        if resp.status_code == 200:
            from bs4 import BeautifulSoup
            page = BeautifulSoup(resp.text, 'html.parser')
            return (page.find('video')).get('src')  # original video address
        else:
            raise RuntimeError('request error, error code:', resp.status_code)

class AudioAPI(object):

    def __init__(self):
        self.url = 'https://api-h5.tangdou.com/sample/share/recommend?page_num=1&vid='

    def get_api_info(self, vid):
        '''use vid to get video information through api interface
        :param vid: audio id in the url, must be a string of numbers
        :param return: return a dict that include the audio information 
        if the request is successful, otherwise throw an RuntimeError
        '''
        vid = str(vid)
        if not vid.isdigit():  # vid must a string of numbers
            raise TypeError('vid should be a string of numbers not', 
                            type(vid), vid)

        header = headers(self.url).buildHeader()
        resp = requests.get(self.url + vid, headers=header)
        if resp.status_code == 200:
            return json.loads(resp.text)
        else:
            raise RuntimeError('request error, error code:', resp.status_code)

    def get_audio_info(self, url):
        '''use vid to get audio spicific information
        :param url: tangdou audio url that containing vid or just vid 
        :param return: return a dict that include audio title and audio original address
        '''
        vid = get_vid(url)
        if vid is None:
            raise ValueError("can not find 'vid' parameter from '{}'".format(url))
        
        api_dict = self.get_api_info(vid)

        audio_info = dict()
        audio_info['name'] = api_dict['data'][1]['title']
        audio_info['url'] = api_dict['data'][1]['mp3url']

        return audio_info
