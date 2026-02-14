import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

import m3u8
import requests

from TimerTimer import TimerTimer
from RandomHeaders import RandomHeaders


class DownloadM3U8:
    def __init__(self, folder, URL, threadNum=100, proxy_config=None, session_hints=None):
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
        self.round_threads = self.threadNum
        self.state_lock = threading.Lock()
        self.blocking_failures = 0
        self.session_hints = self._normalize_session_hints(session_hints)
        self.identity_pool = self._build_identity_pool(pool_size=3)
        self.active_identity_index = 0
        print(f"identity pool size={len(self.identity_pool)}")

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

    @staticmethod
    def _default_user_agent():
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        )

    @staticmethod
    def _normalize_session_hints(session_hints):
        data = session_hints if isinstance(session_hints, dict) else {}
        cookies = []
        for item in data.get("cookies", []):
            if isinstance(item, dict) and item.get("name", "") != "":
                cookies.append(item)

        referer_map = {}
        raw_map = data.get("referer_map", {})
        if isinstance(raw_map, dict):
            for key, value in raw_map.items():
                k = str(key).strip()
                v = str(value).strip()
                if k != "" and v != "":
                    referer_map[k] = v

        return {
            "source_url": str(data.get("source_url", "")).strip(),
            "final_url": str(data.get("final_url", "")).strip(),
            "user_agent": str(data.get("user_agent", "")).strip(),
            "cookies": cookies,
            "referer_map": referer_map,
        }

    def _resolve_referer_for(self, target_url):
        referer_map = self.session_hints.get("referer_map", {})
        if target_url in referer_map and referer_map[target_url] != "":
            return referer_map[target_url]
        if self.URL in referer_map and referer_map[self.URL] != "":
            return referer_map[self.URL]

        final_url = self.session_hints.get("final_url", "")
        if final_url != "":
            return final_url

        source_url = self.session_hints.get("source_url", "")
        if source_url != "":
            return source_url

        if self.origin != "":
            return f"{self.origin}/"
        return self.URL

    def _sanitize_download_headers(self, raw_headers, referer, origin="", preferred_user_agent=""):
        raw = raw_headers if isinstance(raw_headers, dict) else {}
        ua = preferred_user_agent or raw.get("user-agent", "") or self._default_user_agent()
        headers = {
            "user-agent": ua,
            "accept": "*/*",
            "accept-language": raw.get("accept-language", "zh,zh-CN;q=0.9,en;q=0.8"),
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "connection": "keep-alive",
            "referer": referer,
            "origin": origin if origin else None,
        }
        return {k: v for k, v in headers.items() if v}

    def _build_identity_pool(self, pool_size=3):
        size = max(1, int(pool_size))
        referer = self._resolve_referer_for(self.URL)
        referer_candidates = [referer]
        source_url = self.session_hints.get("source_url", "")
        if source_url != "" and source_url not in referer_candidates:
            referer_candidates.append(source_url)

        generated = RandomHeaders.GenHeadersList(size, referer_candidates)
        pool = []
        for index, headers in enumerate(generated):
            preferred_ua = ""
            if index == 0:
                preferred_ua = self.session_hints.get("user_agent", "")
            pool.append(
                self._sanitize_download_headers(
                    headers,
                    referer=referer,
                    origin=self.origin,
                    preferred_user_agent=preferred_ua,
                )
            )

        if len(pool) == 0:
            pool.append(
                self._sanitize_download_headers(
                    {},
                    referer=referer,
                    origin=self.origin,
                    preferred_user_agent=self.session_hints.get("user_agent", "") or self._default_user_agent(),
                )
            )

        unique_pool = []
        seen = set()
        for headers in pool:
            key = (headers.get("user-agent", ""), headers.get("referer", ""))
            if key in seen:
                continue
            seen.add(key)
            unique_pool.append(headers)
        return unique_pool if unique_pool else pool[:1]

    def _set_active_identity(self, index):
        if len(self.identity_pool) == 0:
            self.active_identity_index = 0
            return
        self.active_identity_index = max(0, int(index)) % len(self.identity_pool)

    def _active_identity_headers(self):
        if len(self.identity_pool) == 0:
            return self._sanitize_download_headers({}, referer=self._resolve_referer_for(self.URL), origin=self.origin)
        return dict(self.identity_pool[self.active_identity_index])

    def _build_request_headers(self, target_url, for_playlist=False):
        headers = self._active_identity_headers()
        headers["referer"] = self._resolve_referer_for(target_url)
        if self.origin != "":
            headers["origin"] = self.origin
        if for_playlist:
            headers["accept"] = "*/*"
        return {k: v for k, v in headers.items() if v}

    def _apply_session_cookies(self, session):
        for cookie in self.session_hints.get("cookies", []):
            try:
                name = cookie.get("name", "")
                value = cookie.get("value", "")
                if name == "":
                    continue
                kwargs = {}
                domain = cookie.get("domain", "")
                path = cookie.get("path", "")
                if domain != "":
                    kwargs["domain"] = domain
                if path != "":
                    kwargs["path"] = path
                session.cookies.set(name, value, **kwargs)
            except Exception:
                continue

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
        self._apply_session_cookies(session)
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
            headers = self._build_request_headers(self.URL, for_playlist=True)
            with self._new_session(headers=headers) as session:
                response = session.get(self.URL, timeout=(self.timeout, self.timeout))
                response.raise_for_status()
                try:
                    playlist = m3u8.loads(response.text, uri=self.URL)
                except TypeError:
                    playlist = m3u8.loads(response.text)

                # 主播放列表场景：自动跟进到首个子播放列表，避免 total=0
                if len(playlist.segments) == 0 and len(playlist.playlists) > 0:
                    variant = playlist.playlists[0]
                    variant_url = getattr(variant, "absolute_uri", "") or ""
                    if variant_url == "" and getattr(variant, "uri", ""):
                        variant_url = variant.uri
                    if variant_url != "":
                        headers = self._build_request_headers(variant_url, for_playlist=True)
                        variant_resp = session.get(variant_url, timeout=(self.timeout, self.timeout), headers=headers)
                        variant_resp.raise_for_status()
                        try:
                            playlist = m3u8.loads(variant_resp.text, uri=variant_url)
                        except TypeError:
                            playlist = m3u8.loads(variant_resp.text)

                if len(playlist.segments) == 0:
                    raise ValueError("empty m3u8 playlist")
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
            headers = self._build_request_headers(fileUrl)

            with self._new_session(headers=headers) as session:
                response = session.get(fileUrl, timeout=(self.timeout, self.timeout))
                response.raise_for_status()  # 检查请求状态码，非 2xx 会抛出异常

                # 保存文件
                with open(os.path.join(self.tempDir, fileName), "wb") as file:
                    file.write(response.content)

                # 打印
                with self.state_lock:
                    self.connections = self.connections + 1
                self.printInfo("completed", fileName, fileUrl, response.elapsed.total_seconds())

        except requests.RequestException as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            # 捕获网络请求异常并记录
            with self.state_lock:
                self.failedNameList.append(fileName)
                self.failedUrlList.append(fileUrl)
                self.connections = self.connections + 1
                if status_code in [401, 403, 429]:
                    self.blocking_failures = self.blocking_failures + 1
            # 打印
            stage = f"failed[{status_code}]: {e}" if status_code is not None else f"failed: {e}"
            self.printInfo(stage, fileName, fileUrl)

    def RetryFailed(self, retries=10):
        # 失败列表最多重试10次
        # 连续5次失败列表不更新则放弃
        if retries <= 0:
            return
        failed_time = 0
        for attempt in range(retries):
            if len(self.failedNameList) == 0:
                break
            self._set_active_identity(attempt + 1)
            print(
                f"\n\t********attempt: {attempt + 1}/{retries} failed={len(self.failedNameList)} "
                f"identity={self.active_identity_index + 1}/{len(self.identity_pool)} "
                f"threads={self.round_threads}********"
            )
            # 临时存储本次重试开始前的状况
            failedNameList = self.failedNameList.copy()
            failedUrlList = self.failedUrlList.copy()
            self.failedNameList.clear()
            self.failedUrlList.clear()
            with self.state_lock:
                self.blocking_failures = 0
            if failed_time > 2:
                self.timeout = self.timeout + 3
            with ThreadPoolExecutor(max_workers=self.round_threads) as executor:
                for name, url in zip(failedNameList, failedUrlList):
                    executor.submit(self.__downloadSingle, name, url)
            with self.state_lock:
                blocking_failures = self.blocking_failures
            if blocking_failures > 0 and self.round_threads > 8:
                old_threads = self.round_threads
                self.round_threads = max(8, int(self.round_threads * 0.7))
                print(
                    f"\tblocking-like failures={blocking_failures}, "
                    f"reduce threads {old_threads} -> {self.round_threads}"
                )
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
        self.round_threads = self.threadNum
        self._set_active_identity(0)

        # 下载和写List中的文件
        print(f"\n\t********total={len(self.fileNameList)}********")
        with ThreadPoolExecutor(max_workers=self.round_threads) as executor:
            for name, url in zip(self.fileNameList, self.fileUrlList):
                executor.submit(self.__downloadSingle, name, url)

        # 重新下载和写failedList中的文件
        self.RetryFailed(retries)

        # 结束定时器
        self.timeoutTimer.StopTimer()

        # 写index.m3u8 - 删除无效文件
        self.WriteM3U8()

    def TimeoutAdapting(self):
        with self.state_lock:
            connections = self.connections
            failed_count = len(self.failedNameList)

        if connections < 15:
            return
        if self.timeout >= self.maxTimeout:
            print("\t********bad connection********")
            return
        failed_ratio = failed_count / connections
        if failed_ratio > self.threshold:
            print(
                f"\t********failed% = {failed_ratio} = {failed_count}/{connections} > {self.threshold}"
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
