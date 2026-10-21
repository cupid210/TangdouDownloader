# 糖豆视频批量下载器

一次粘贴多个包含 `vid` 的分享链接，自动下载每个视频的最高可用画质，直接保存 MP4。
不再询问清晰度、剪辑或音频转换。

## Windows EXE 直接使用

如果拿到的是打包好的 `TangdouDownloader.exe`，无需安装 Python，直接双击运行。
在打开的命令窗口里粘贴多个链接，每行一个，最后再按一次回车开始下载。
视频保存到 EXE 旁边的 `Download` 文件夹，请先把 EXE 放在有写入权限的普通文件夹中。

GitHub 的 Code → Download ZIP 下载的是源码，不会包含本地生成的 EXE。
源码使用方法见下方；开发者也可以安装 Python 后双击 `build_exe.bat`，
它会创建独立的构建环境并生成 `dist/TangdouDownloader.exe`。
旧的 `compile.bat` 也会调用同一个打包脚本。该 EXE 为 Windows 64 位控制台程序。

## 使用 Python 运行源码

安装 Python 3.8 或更新版本，在项目目录安装依赖：

```powershell
python -m pip install -r requirements.txt
```

双击 `start.bat`，粘贴多个链接，每行一个；全部粘贴完后，再按一次回车开始下载。
也支持用空格、中文或英文逗号分隔，支持直接输入 vid 编号；同一批中的重复 vid 会自动去重。

```text
请粘贴多个含 vid 的链接或 vid 编号（每行一个，也可用空格分隔）。
粘贴完再按一次回车开始下载；直接回车退出。
https://www.tangdouddn.com/h5/play?vid=20000014177453&utm_campaign=client_share&utm_source=tangdou_android
https://www.tangdoucdn.com/h5/play?vid=另一个数字编号
20000014177453

```

上面最后的空行代表开始下载。默认保存到程序目录的 `Download` 文件夹，
文件名包含标题、vid 和清晰度，避免同名视频相互覆盖。
完成一批后可以继续粘贴下一批；直接输入空行或按 Ctrl+C 退出。
失败的视频会单独列出 vid，不影响其他任务。

`start.bat` 优先使用项目的 `.venv`，其次使用旧版嵌入式 Python，最后使用系统 Python；
如果项目内存在 `.deps`，启动器会一并加载其中已安装的依赖。

## 命令行和文本文件

```powershell
python main.py "https://www.tangdouddn.com/h5/play?vid=20000014177453" "另一个含vid的链接"
python main.py --file links.txt
python main.py --file links.txt --output "D:\Videos" --workers 5
```

- `links.txt` 使用 UTF-8 编码，建议每行一个链接。
- `--output` 指定最终保存目录，不会再额外追加 Download。
- `--workers` 控制同时处理的视频数量，默认 5，可设为 1–16；通常默认值即可。
- 命令行模式下载完后退出；全部成功或已存在返回 0，有失败返回 1。
- 仍可通过 `python main.py --json-dir DownloadList` 读取旧版 JSON 下载列表，无需抓包也能直接粘贴链接。

## 自动画质与下载加速

- 根据视频地址生成已知的 1080P、720P、540P、360P 地址；若原地址标明更高分辨率，也会优先尝试。
- 从高到低检测，找到第一个可用画质就停止；相同分辨率优先尝试原视频的横竖方向。
- 无法从地址识别画质时，下载接口提供的原始视频，不进行转码。
- 默认同时处理 5 个视频，视频信息解析也并行执行。
- 对支持 HTTP Range 且大小至少为 8 MiB 的视频，自动使用 4 路分段下载。
  服务器不支持或未正确返回分段时，自动回退普通下载；小文件直接使用普通下载。
- 所有请求带超时与有限重试，并及时关闭响应；不加载 MoviePy 或 FFmpeg。
- 下载先写入 `.part` 临时文件，完整校验长度后才保存为 MP4；
  分段下载还会核对每段的范围和长度。失败或取消会清理临时文件。
- 已有文件大小与服务器声明一致时跳过。当前未实现跨次运行的断点续传。
- 下载器不执行视频剪辑或音频转换，旧的独立音频脚本不属于此流程。

速度取决于本地带宽、网络和服务器限速，增加并发数并不总能更快。
本次以 vid `20000014177453` 的 H720P 视频（44,393,362 字节）验证：
普通单连接约 0.28 MB/s，4 路分段约 1.28 MB/s，约快 4.6 倍。
两份文件的 SHA-256 相同，且 MD5 均与服务器提供的校验值一致；此结果不代表其他网络环境的保证速度。

## 开发验证

```powershell
python -m unittest discover -s tests -v
```

测试使用本地 HTTP 服务验证最高画质探测、批量去重与并发、单项失败隔离、
普通与分段下载的文件完整性、分段回退、断流重试、已存在跳过和交互输入。

项目源自 [CCBP/TangdouDownloader](https://github.com/CCBP/TangdouDownloader)，遵循 MIT 许可证。
