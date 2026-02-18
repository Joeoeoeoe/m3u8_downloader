import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import quote, urlparse

import m3u8
import requests

from TimerTimer import TimerTimer
from RandomHeaders import RandomHeaders


class DownloadM3U8:
    def __init__(
        self,
        folder,
        URL,
        threadNum=100,
        proxy_config=None,
        session_hints=None,
        progress_callback=None,
        stop_checker=None,
    ):
        # 文件夹
        self.fileDir = folder
        self.tempDir = os.path.join(self.fileDir, ".TEMP")
        self.prepareFolder()

        # 下载列表
        self.URL = URL.strip()
        parsed = urlparse(self.URL)
        self.origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        print(f"[download][init] parsed_url={parsed.geturl()} origin={self.origin or '(none)'}")

        self.proxy_config = self._normalize_proxy_config(proxy_config)
        self.proxy_url = self._build_proxy_url(self.proxy_config)
        if self.proxy_config["enabled"]:
            print(
                f"[download][proxy] using {self.proxy_config['address']}:{self.proxy_config['port']} "
                f"user={self.proxy_config['username'] or '(none)'}"
            )

        self.playlist = None  # 存m3u8.load()返回的playlist
        # 两个列表，实现文件名和请求地址的一一对应
        self.fileNameList = []
        self.fileUrlList = []

        # 超时和异常
        self.connections = 0  # 总的请求次数，用于输出
        self.minTimeout = 4
        self.timeout = self.minTimeout  # 初始超时时间设置为4秒
        self.maxTimeout = 25  # 最大超时时间为25秒
        self.timeout_step_up = 2
        self.timeout_step_down = 1
        self.timeout_raise_threshold = 0.45
        self.timeout_recover_threshold = 0.15
        self.timeout_adjust_cooldown = 8
        self.timeout_min_observations = 20
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
        self.total_failures = 0
        self.timeout_last_eval_connections = 0
        self.timeout_last_eval_failures = 0
        self.timeout_last_adjust_ts = 0.0
        self.session_hints = self._normalize_session_hints(session_hints)
        self.identity_pool = self._build_identity_pool(pool_size=3)
        self.active_identity_index = 0
        self.progress_callback = progress_callback if callable(progress_callback) else None
        self.stop_checker = stop_checker if callable(stop_checker) else None
        self._manual_stop_requested = False
        self.download_interrupted = False
        self._stop_logged = False
        self.completedNameSet = set()
        print(f"[download][init] identity_pool_size={len(self.identity_pool)}")

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
        print(f"[file] temp_folder={folder_path}")
        print(f"[file] residual_folder={residual_path}")

        # 如果 .residual 文件夹存在，则清空
        if os.path.exists(residual_path):
            shutil.rmtree(residual_path)

        # 如果目标文件夹存在且非空
        if os.path.exists(folder_path):
            existing_items = [item for item in os.listdir(folder_path) if item != ".residual"]
            if existing_items:
                os.mkdir(residual_path)
                for item in existing_items:
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
        stage_text = str(stage or "").strip()
        stage_lower = stage_text.lower()
        if stage_lower == "completed":
            level = "completed"
        elif stage_lower.startswith("failed"):
            level = "error"
        elif stage_lower in {"getting", "got", "downloading", "downloaded"}:
            level = stage_lower
        else:
            level = stage_lower if stage_lower != "" else "info"

        fields = [f"[segment][{level}]"]
        if filename:
            fields.append(f"file={filename}")
        if stage_lower == "completed":
            fields.append(f"conn={self.connections}")
            if time_cost is not None:
                fields.append(f"time={time_cost}")
        elif stage_lower.startswith("failed"):
            fields.append(f"reason={stage_text}")
        if url:
            fields.append(f"url={url}")
        print(" ".join(fields))

    def _emit_progress(self, event, **kwargs):
        if self.progress_callback is None:
            return
        payload = {"event": event}
        payload.update(kwargs)
        try:
            self.progress_callback(payload)
        except Exception:
            pass

    def request_stop(self):
        self._manual_stop_requested = True
        self._mark_interrupted("manual")

    def was_interrupted(self):
        with self.state_lock:
            return self.download_interrupted

    def _mark_interrupted(self, reason=""):
        should_log = False
        with self.state_lock:
            self.download_interrupted = True
            if not self._stop_logged:
                self._stop_logged = True
                should_log = True
        if should_log:
            suffix = f" reason={reason}" if reason else ""
            print(f"[download][interrupt] requested{suffix}")

    def _is_stop_requested(self):
        external_requested = False
        if self.stop_checker is not None:
            try:
                external_requested = bool(self.stop_checker())
            except Exception:
                external_requested = False
        if self._manual_stop_requested or external_requested:
            self._mark_interrupted("external" if external_requested else "manual")
            return True
        return False

    def _run_download_tasks(self, download_items):
        if len(download_items) == 0:
            return
        executor = ThreadPoolExecutor(max_workers=max(1, int(self.round_threads)))
        pending_futures = set()
        try:
            for name, url in download_items:
                if self._is_stop_requested():
                    break
                pending_futures.add(executor.submit(self.__downloadSingle, name, url))

            while len(pending_futures) > 0:
                if self._is_stop_requested():
                    for future in pending_futures:
                        future.cancel()
                    break
                done, pending_futures = wait(pending_futures, timeout=0.2, return_when=FIRST_COMPLETED)
                if len(done) == 0:
                    continue
                for future in done:
                    try:
                        future.result()
                    except Exception as exc:
                        print(f"[error][segment] unexpected worker exception: {exc}")
        finally:
            executor.shutdown(wait=True)

    def _get_timeout_snapshot(self):
        with self.state_lock:
            return self.timeout

    def _adjust_timeout(self, delta, reason):
        step = int(delta)
        if step == 0:
            return False
        with self.state_lock:
            old_timeout = self.timeout
            target_timeout = old_timeout + step
            target_timeout = max(self.minTimeout, min(self.maxTimeout, target_timeout))
            if target_timeout == old_timeout:
                return False
            self.timeout = target_timeout
        print(f"[retry] timeout_adjust reason={reason} {old_timeout}->{target_timeout}")
        return True

    def _compute_retry_budget(self, retries, first_pass_failed_count):
        try:
            base_retries = int(retries)
        except (TypeError, ValueError):
            base_retries = 10
        base_retries = max(10, base_retries)
        total_segments = len(self.fileNameList)

        # 大视频和高失败首轮都允许更多重试，但仍保留硬上限防止无限重试。
        long_video_bonus = min(20, total_segments // 150)
        failed_bonus = min(40, max(0, first_pass_failed_count // 6))
        return min(120, base_retries + long_video_bonus + failed_bonus)

    def _reset_download_runtime_state(self):
        with self.state_lock:
            self.connections = 0
            self.blocking_failures = 0
            self.total_failures = 0
            self.timeout = self.minTimeout
            self.timeout_last_eval_connections = 0
            self.timeout_last_eval_failures = 0
            self.timeout_last_adjust_ts = 0.0
            self._manual_stop_requested = False
            self.download_interrupted = False
            self._stop_logged = False
            self.failedNameList.clear()
            self.failedUrlList.clear()

    def get_failed_segments(self):
        with self.state_lock:
            return [
                {"name": name, "url": url}
                for name, url in zip(self.failedNameList, self.failedUrlList)
            ]

    def printM3U8(self):
        if self.playlist:
            print(self.playlist.dumps())

    def prepareDownload(self):
        self.printInfo("getting", "*.m3u8", self.URL)
        if self._is_stop_requested():
            return
        try:
            headers = self._build_request_headers(self.URL, for_playlist=True)
            with self._new_session(headers=headers) as session:
                if self._is_stop_requested():
                    return
                timeout_seconds = self._get_timeout_snapshot()
                response = session.get(self.URL, timeout=(timeout_seconds, timeout_seconds))
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
                        if self._is_stop_requested():
                            return
                        headers = self._build_request_headers(variant_url, for_playlist=True)
                        timeout_seconds = self._get_timeout_snapshot()
                        variant_resp = session.get(
                            variant_url,
                            timeout=(timeout_seconds, timeout_seconds),
                            headers=headers,
                        )
                        variant_resp.raise_for_status()
                        try:
                            playlist = m3u8.loads(variant_resp.text, uri=variant_url)
                        except TypeError:
                            playlist = m3u8.loads(variant_resp.text)

                if len(playlist.segments) == 0:
                    raise ValueError("empty m3u8 playlist")
        except Exception as e:
            print(f"[error] m3u8 read error: {e}")
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
        if self._is_stop_requested():
            return
        file_path = os.path.join(self.tempDir, fileName)
        try:
            headers = self._build_request_headers(fileUrl)

            with self._new_session(headers=headers) as session:
                timeout_seconds = self._get_timeout_snapshot()
                with session.get(fileUrl, timeout=(timeout_seconds, timeout_seconds), stream=True) as response:
                    response.raise_for_status()  # 检查请求状态码，非 2xx 会抛出异常

                    # 分块下载，允许在分片下载中快速响应中断。
                    with open(file_path, "wb") as file:
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if self._is_stop_requested():
                                try:
                                    file.close()
                                    if os.path.exists(file_path):
                                        os.remove(file_path)
                                except OSError:
                                    pass
                                return
                            if chunk:
                                file.write(chunk)

                # 打印
                with self.state_lock:
                    self.connections = self.connections + 1
                    self.completedNameSet.add(fileName)
                    completed_count = len(self.completedNameSet)
                    total_count = len(self.fileNameList)
                self.printInfo("completed", fileName, fileUrl, response.elapsed.total_seconds())
                self._emit_progress("segment_done", done=completed_count, total=total_count, file=fileName)

        except requests.RequestException as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            # 捕获网络请求异常并记录
            with self.state_lock:
                self.failedNameList.append(fileName)
                self.failedUrlList.append(fileUrl)
                self.connections = self.connections + 1
                self.total_failures = self.total_failures + 1
                if status_code in [401, 403, 429]:
                    self.blocking_failures = self.blocking_failures + 1
            # 打印
            stage = f"failed[{status_code}]: {e}" if status_code is not None else f"failed: {e}"
            self.printInfo(stage, fileName, fileUrl)
        except Exception as e:
            with self.state_lock:
                self.failedNameList.append(fileName)
                self.failedUrlList.append(fileUrl)
                self.connections = self.connections + 1
                self.total_failures = self.total_failures + 1
            self.printInfo(f"failed: {e}", fileName, fileUrl)

    def RetryFailed(self, retries=10):
        if self._is_stop_requested():
            return
        if retries <= 0:
            return
        first_pass_failed_count = len(self.failedNameList)
        if first_pass_failed_count == 0:
            return

        max_retry_rounds = self._compute_retry_budget(retries, first_pass_failed_count)
        stagnation_rounds = 0
        strong_recovery_rounds = 0
        stagnation_limit = min(12, max(5, max_retry_rounds // 4))
        print(
            f"[retry] budget={max_retry_rounds} "
            f"first_pass_failed={first_pass_failed_count} total={len(self.fileNameList)}"
        )

        for attempt in range(max_retry_rounds):
            if self._is_stop_requested():
                print("[retry] interrupted, stop retry rounds")
                break
            with self.state_lock:
                current_failed_count = len(self.failedNameList)
            if current_failed_count == 0:
                break
            self._set_active_identity(attempt + 1)
            timeout_now = self._get_timeout_snapshot()
            print(
                f"[retry] attempt={attempt + 1}/{max_retry_rounds} failed={current_failed_count} "
                f"identity={self.active_identity_index + 1}/{len(self.identity_pool)} "
                f"threads={self.round_threads} timeout={timeout_now}s"
            )
            # 临时存储本次重试开始前的状况
            with self.state_lock:
                failedNameList = self.failedNameList.copy()
                failedUrlList = self.failedUrlList.copy()
                self.failedNameList.clear()
                self.failedUrlList.clear()
                self.blocking_failures = 0
            self._run_download_tasks(list(zip(failedNameList, failedUrlList)))
            with self.state_lock:
                blocking_failures = self.blocking_failures
                next_failed_count = len(self.failedNameList)
            if blocking_failures > 0 and self.round_threads > 8:
                old_threads = self.round_threads
                self.round_threads = max(8, int(self.round_threads * 0.7))
                print(
                    f"[retry] blocking_like_failures={blocking_failures} "
                    f"reduce_threads={old_threads}->{self.round_threads}"
                )

            if next_failed_count > current_failed_count:
                print("[warn][retry] unexpected state: failed list increased")
                stagnation_rounds = stagnation_rounds + 1
                strong_recovery_rounds = 0
            else:
                recovered_count = current_failed_count - next_failed_count
                recovered_ratio = recovered_count / max(1, current_failed_count)
                print(
                    f"[retry] round_result recovered={recovered_count}/{current_failed_count} "
                    f"remaining={next_failed_count}"
                )
                if recovered_count <= 0:
                    stagnation_rounds = stagnation_rounds + 1
                    strong_recovery_rounds = 0
                    if stagnation_rounds >= 2:
                        self._adjust_timeout(+1, f"retry stagnation x{stagnation_rounds}")
                else:
                    stagnation_rounds = 0
                    if recovered_ratio >= 0.35:
                        strong_recovery_rounds = strong_recovery_rounds + 1
                        if strong_recovery_rounds >= 3:
                            if self._adjust_timeout(-1, "retry recovery"):
                                strong_recovery_rounds = 0
                    else:
                        strong_recovery_rounds = 0

            if stagnation_rounds >= stagnation_limit:
                print(
                    f"[retry] stop_early stagnation={stagnation_rounds} "
                    f"limit={stagnation_limit} remaining={next_failed_count}"
                )
                break

    def WriteM3U8(self):
        if self.playlist is None:
            return
        # 将m3u8索引文件写入本地
        self.playlist.segments[:] = [seg for seg in self.playlist.segments if seg.uri not in self.failedNameList]
        with open(os.path.join(self.tempDir, "index.m3u8"), "w", encoding="utf-8") as f:
            f.write(self.playlist.dumps())

    def DonwloadAndWrite(self, retries=10):
        # 启动定时器
        self._reset_download_runtime_state()
        self.round_threads = self.threadNum
        self._set_active_identity(0)
        self.completedNameSet.clear()
        total_segments = len(self.fileNameList)
        self._emit_progress("start", done=0, total=total_segments)

        self.timeoutTimer.StartTimer()
        try:
            # 下载和写List中的文件
            print(f"[download] total_segments={len(self.fileNameList)}")
            self._run_download_tasks(list(zip(self.fileNameList, self.fileUrlList)))

            # 重新下载和写failedList中的文件
            if not self._is_stop_requested():
                self.RetryFailed(retries)
            else:
                print("[download] interrupted before retry stage")

            if self.was_interrupted():
                print("[download] interrupted, skip index.m3u8 writing")
            else:
                # 写index.m3u8 - 删除无效文件
                self.WriteM3U8()
        finally:
            # 结束定时器
            self.timeoutTimer.StopTimer()
            self._emit_progress(
                "done",
                done=len(self.completedNameSet),
                total=total_segments,
                failed=len(self.failedNameList),
                interrupted=self.was_interrupted(),
            )

    def TimeoutAdapting(self):
        if self._is_stop_requested():
            return
        now = time.time()
        with self.state_lock:
            connections = self.connections
            total_failures = self.total_failures
            last_eval_connections = self.timeout_last_eval_connections
            last_eval_failures = self.timeout_last_eval_failures
            last_adjust_ts = self.timeout_last_adjust_ts

        if connections < self.timeout_min_observations:
            return
        if now - last_adjust_ts < self.timeout_adjust_cooldown:
            return

        delta_connections = connections - last_eval_connections
        delta_failures = total_failures - last_eval_failures
        if delta_connections < self.timeout_min_observations:
            return

        recent_failed_ratio = delta_failures / max(1, delta_connections)
        if recent_failed_ratio >= self.timeout_raise_threshold:
            self._adjust_timeout(+self.timeout_step_up, f"recent failed ratio={recent_failed_ratio:.2f}")
        elif recent_failed_ratio <= self.timeout_recover_threshold:
            self._adjust_timeout(-self.timeout_step_down, f"recent failed ratio={recent_failed_ratio:.2f}")

        with self.state_lock:
            self.timeout_last_eval_connections = self.connections
            self.timeout_last_eval_failures = self.total_failures
            self.timeout_last_adjust_ts = now

    def writeVideoBat(self, fileName="output", extension=".mp4"):
        indexPath = os.path.join(self.tempDir, "index.m3u8")
        filePath = os.path.join(self.fileDir, f"{fileName}{extension}")
        batPath = os.path.join(self.fileDir, "combine.bat")
        ffmpegPath = os.path.join(os.getcwd(), "ffmpeg.exe")
        command = f'"{ffmpegPath}" -allowed_extensions ALL -i "{indexPath}" -c copy "{filePath}"'
        with open(batPath, "w") as file:
            file.write(command)
        print(f"[ffmpeg] command={command}")
        if os.path.exists(filePath):
            newPath = os.path.join(self.fileDir, f"origin-{fileName}{extension}")
            os.rename(filePath, newPath)

    def process_video_with_ffmpeg(self, base_filename: str, extension: str = ".mp4") -> bool:
        self._ffmpeg_exe_path = os.path.join(os.getcwd(), "ffmpeg.exe")
        if not os.path.isfile(self._ffmpeg_exe_path):
            print(f"[error][ffmpeg] executable not found: '{self._ffmpeg_exe_path}'")
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
            print(f"[error][ffmpeg] input m3u8 file not found: '{index_m3u8_path}'")
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
            f"[ffmpeg] generating {extension} for {base_filename}{extension} "
            f"-> {os.path.basename(final_output_path)}"
        )
        try:
            subprocess.run(
                command,
                capture_output=True,
                check=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            print(f"[error][ffmpeg] command failed for {base_filename}, code={e.returncode}")
            print("[error][ffmpeg] stdout:")
            print(self._safe_decode(e.stdout))
            print("[error][ffmpeg] stderr:")
            print(self._safe_decode(e.stderr))
            return False
        except FileNotFoundError:
            print(f"[error][ffmpeg] executable not found: '{self._ffmpeg_exe_path}'")
            return False
        except Exception as e:
            print(f"[error][ffmpeg] unexpected error while processing {base_filename}: {e}")
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
