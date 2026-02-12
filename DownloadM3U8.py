import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

import m3u8
import requests

from TimerTimer import TimerTimer
from RandomHeaders import RandomHeaders


class DownloadM3U8:
    def __init__(self, folder, URL, threadNum=100, proxy_config=None):
        # 文件夹
        self.fileDir = folder
        self.tempDir = os.path.join(self.fileDir, ".TEMP")
        self.prepareFolder()

        # 下载列表
        self.URL = URL.strip()
        parsed = urlparse(self.URL)
        self.origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        print("parsed:", parsed, "; ", "self.origin:", self.origin)

        self.proxy_config = self._normalize_proxy_config(proxy_config)
        self.proxy_url = self._build_proxy_url(self.proxy_config)
        if self.proxy_config["enabled"]:
            print(
                f"using proxy: {self.proxy_config['address']}:{self.proxy_config['port']} "
                f"user={self.proxy_config['username'] or '(none)'}"
            )

        self.playlist = None  # 存m3u8.load()返回的playlist
        # 两个列表，实现文件名和请求地址的一一对应
        self.fileNameList = []
        self.fileUrlList = []

        # 超时和异常
        self.connections = 0  # 总的请求次数，用于输出
        self.timeout = 4  # 初始超时时间设置为4秒
        self.maxTimeout = 25  # 最大超时时间为25秒
        self.threshold = 0.3  # 最大忍受的失败比例
        self.interval = 10  # 定时器间隔
        self.timeoutTimer = TimerTimer(self.interval, self.TimeoutAdapting)  # 初始化定时器，动态调整超时时间
        self.failedNameList = []
        self.failedUrlList = []
        try:
            self.threadNum = max(1, int(threadNum))
        except (TypeError, ValueError):
            self.threadNum = 100

        self.prepareDownload()  # 对index.m3u8初步解析，填充上面两个列表，不做任何下载

    @staticmethod
    def _normalize_proxy_config(proxy_config):
        data = proxy_config if isinstance(proxy_config, dict) else {}
        address = str(data.get("address", "")).strip()
        port = str(data.get("port", "")).strip()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", "")).strip()
        enabled = bool(data.get("enabled", False))

        if port and not port.isdigit():
            port = ""
        if enabled and (address == "" or port == ""):
            enabled = False

        return {
            "enabled": enabled,
            "address": address,
            "port": port,
            "username": username,
            "password": password,
        }

    @staticmethod
    def _build_proxy_url(proxy_config):
        if not proxy_config["enabled"]:
            return ""

        auth = ""
        username = proxy_config["username"]
        password = proxy_config["password"]
        if username != "" or password != "":
            auth = f"{quote(username)}:{quote(password)}@"

        return f"http://{auth}{proxy_config['address']}:{proxy_config['port']}"

    def _new_session(self, headers=None):
        session = requests.Session()
        # 避免被系统代理环境变量接管
        session.trust_env = False

        if self.proxy_url != "":
            session.proxies.update(
                {
                    "http": self.proxy_url,
                    "https": self.proxy_url,
                }
            )

        if headers:
            session.headers.update(headers)
        return session

    @staticmethod
    def clearFolder(folder_path):
        """
        确保指定路径存在，并处理已存在文件夹的内容：
        - 如果 .residual 文件夹已经存在，则清空它后再使用。
        - 如果文件夹存在且非空，则将内容移动到 .residual 文件夹。
        """
        folder_path = os.path.abspath(folder_path)
        residual_path = os.path.join(folder_path, ".residual")
        print(folder_path)
        print(residual_path)

        # 如果 .residual 文件夹存在，则清空
        if os.path.exists(residual_path):
            shutil.rmtree(residual_path)

        # 如果目标文件夹存在且非空
        if os.path.exists(folder_path):
            os.mkdir(residual_path)
            if os.listdir(folder_path):
                for item in os.listdir(folder_path):
                    item_path = os.path.join(folder_path, item)
                    shutil.move(item_path, residual_path)
        else:
            os.makedirs(folder_path)

    def prepareFolder(self):
        if not os.path.exists(self.fileDir):
            os.mkdir(self.fileDir)
        if not os.path.exists(self.tempDir):
            os.mkdir(self.tempDir)
        DownloadM3U8.clearFolder(self.tempDir)

    def printInfo(self, stage, filename, url=None, time_cost=None):
        # stage: 阶段 getting got downloading downloaded completed
        if stage == "completed":
            if time_cost is None:
                print(f"{stage}\t{self.connections}\t****{filename}****", end="")
            else:
                print(f"{stage}\t{self.connections}\ttime={time_cost}\t****{filename}****", end="")
        else:
            print(f"{stage}\t\t****{filename}****", end="")
        print(f"\t:{url}") if url else print()

    def printM3U8(self):
        if self.playlist:
            print(self.playlist.dumps())

    def prepareDownload(self):
        self.printInfo("getting", "*.m3u8", self.URL)
        try:
            headers = {
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
                "referer": f"{self.origin}/" if self.origin else self.URL,
                "origin": self.origin if self.origin else None,
                "accept": "*/*",
                "accept-encoding": "gzip, deflate, br, zstd",
                "accept-language": "zh,zh-CN;q=0.9",
                "cache-control": "no-cache",
                "pragma": "no-cache",
            }
            headers = {k: v for k, v in headers.items() if v}
            with self._new_session(headers=headers) as session:
                response = session.get(self.URL, timeout=(self.timeout, self.timeout))
                response.raise_for_status()
                try:
                    playlist = m3u8.loads(response.text, uri=self.URL)
                except TypeError:
                    playlist = m3u8.loads(response.text)
        except Exception as e:
            print(f"m3u8 read error! {e}\n\n")
            raise ValueError("m3u8 read error")

        self.printInfo("got", "index.m3u8", self.URL)
        self.playlist = playlist

        # 获取解密文件名和地址
        if playlist.keys and playlist.keys[0]:
            for i, key in enumerate(playlist.keys):
                self.fileUrlList.append(key.absolute_uri)
                key.uri = f"key{i}.enc"  # 强制更改名称，避免index.m3u8中出现预料之外的网址
                self.fileNameList.append(key.uri)

        # 获取ts文件名和地址
        for i, segment in enumerate(playlist.segments):
            self.fileUrlList.append(segment.absolute_uri)
            segment.uri = f"{i}.ts"
            self.fileNameList.append(segment.uri)

    def __downloadSingle(self, fileName, fileUrl):
        try:
            headers = RandomHeaders()[0]
            if self.origin:
                headers.update({"referer": f"{self.origin}/", "origin": self.origin})
            else:
                headers.update({"referer": self.URL})
            headers = {k: v for k, v in headers.items() if v}

            with self._new_session(headers=headers) as session:
                response = session.get(fileUrl, timeout=(self.timeout, self.timeout))
                response.raise_for_status()  # 检查请求状态码，非 2xx 会抛出异常

                # 保存文件
                with open(os.path.join(self.tempDir, fileName), "wb") as file:
                    file.write(response.content)

                # 打印
                self.connections = self.connections + 1
                self.printInfo("completed", fileName, fileUrl, response.elapsed.total_seconds())

        except requests.RequestException as e:
            # 捕获网络请求异常并记录
            self.failedNameList.append(fileName)
            self.failedUrlList.append(fileUrl)
            # 打印
            self.connections = self.connections + 1
            self.printInfo(f"failed: {e}", fileName, fileUrl)

    def RetryFailed(self, retries=10):
        # 失败列表最多重试10次
        # 连续5次失败列表不更新则放弃
        if retries <= 0:
            return
        failed_time = 0
        for attempt in range(retries):
            if len(self.failedNameList) == 0:
                break
            print(f"\n\t********attempt: {attempt + 1}/{retries} failed={len(self.failedNameList)}********")
            # 临时存储本次重试开始前的状况
            failedNameList = self.failedNameList.copy()
            failedUrlList = self.failedUrlList.copy()
            self.failedNameList.clear()
            self.failedUrlList.clear()
            if failed_time > 2:
                self.timeout = self.timeout + 3
            with ThreadPoolExecutor(max_workers=self.threadNum) as executor:
                for name, url in zip(failedNameList, failedUrlList):
                    executor.submit(self.__downloadSingle, name, url)
            if len(self.failedNameList) == len(failedNameList):
                failed_time = failed_time + 1
            elif len(self.failedNameList) >= len(failedNameList):
                print("\t????unexpected error: failedList increased????")
            else:
                failed_time = 1
            if failed_time >= 5:
                break

    def WriteM3U8(self):
        # 将m3u8索引文件写入本地
        self.playlist.segments[:] = [seg for seg in self.playlist.segments if seg.uri not in self.failedNameList]
        with open(os.path.join(self.tempDir, "index.m3u8"), "w", encoding="utf-8") as f:
            f.write(self.playlist.dumps())

    def DonwloadAndWrite(self, retries=10):
        # 启动定时器
        self.timeoutTimer.StartTimer()

        # 下载和写List中的文件
        print(f"\n\t********total={len(self.fileNameList)}********")
        with ThreadPoolExecutor(max_workers=self.threadNum) as executor:
            for name, url in zip(self.fileNameList, self.fileUrlList):
                executor.submit(self.__downloadSingle, name, url)

        # 重新下载和写failedList中的文件
        self.RetryFailed(retries)

        # 结束定时器
        self.timeoutTimer.StopTimer()

        # 写index.m3u8 - 删除无效文件
        self.WriteM3U8()

    def TimeoutAdapting(self):
        if self.connections < 15:
            return
        if self.timeout >= self.maxTimeout:
            print("\t********bad connection********")
            return
        if len(self.failedNameList) / self.connections > self.threshold:
            print(
                f"\t********failed% = {len(self.failedNameList) / self.connections} = {len(self.failedNameList)}/{self.connections} > {self.threshold}"
            )
            print(f"\t********changing timeout from {self.timeout} to {self.timeout + 3}********")
            self.timeout = self.timeout + 3
            time.sleep(10)

    def writeVideoBat(self, fileName="output", extension=".mp4"):
        indexPath = os.path.join(self.tempDir, "index.m3u8")
        filePath = os.path.join(self.fileDir, f"{fileName}{extension}")
        batPath = os.path.join(self.fileDir, "combine.bat")
        ffmpegPath = os.path.join(os.getcwd(), "ffmpeg.exe")
        command = f'"{ffmpegPath}" -allowed_extensions ALL -i "{indexPath}" -c copy "{filePath}"'
        with open(batPath, "w") as file:
            file.write(command)
        print(f"command = {command}")
        if os.path.exists(filePath):
            newPath = os.path.join(self.fileDir, f"origin-{fileName}{extension}")
            os.rename(filePath, newPath)

    def process_video_with_ffmpeg(self, base_filename: str, extension: str = ".mp4") -> bool:
        self._ffmpeg_exe_path = os.path.join(os.getcwd(), "ffmpeg.exe")
        if self._ffmpeg_exe_path is None:
            return False
        index_m3u8_path = os.path.join(self.tempDir, "index.m3u8")

        # 构建最终输出文件的完整路径
        proposed_output_filename = f"{base_filename}{extension}"
        final_output_path = os.path.join(self.fileDir, proposed_output_filename)
        # 处理同名文件逻辑：如果文件已存在，则在文件名后添加 (N)
        counter = 0
        while os.path.exists(final_output_path):
            counter += 1
            proposed_output_filename = f"{base_filename}({counter}){extension}"
            final_output_path = os.path.join(self.fileDir, proposed_output_filename)

        if not os.path.exists(index_m3u8_path):
            print(f"Error: Input M3U8 file not found: '{index_m3u8_path}'")
            return False
        command = [
            self._ffmpeg_exe_path,
            "-allowed_extensions",
            "ALL",
            "-i",
            index_m3u8_path,
            "-c",
            "copy",
            final_output_path,  # 使用处理后的最终输出路径
        ]
        print(
            f"\n\t********generating {extension} for {base_filename}{extension} "
            f"to {os.path.basename(final_output_path)}********"
        )
        try:
            subprocess.run(
                command,
                capture_output=True,
                check=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            print(f"\nError: FFmpeg command failed for {base_filename}. Return code: {e.returncode}")
            print("\n--- FFmpeg STDOUT (Error Context) ---")
            print(self._safe_decode(e.stdout))
            print("\n--- FFmpeg STDERR (Error Details) ---")
            print(self._safe_decode(e.stderr))
            return False
        except FileNotFoundError:
            print(f"\nError: FFmpeg executable not found at '{self._ffmpeg_exe_path}'.")
            return False
        except Exception as e:
            print(f"\nAn unexpected error occurred during processing {base_filename}: {e}")
            return False

    @staticmethod
    def _safe_decode(raw_output):
        if raw_output is None:
            return ""
        if isinstance(raw_output, str):
            return raw_output
        for enc in ("utf-8", "gbk", "cp1252"):
            try:
                return raw_output.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw_output.decode("utf-8", errors="replace")
