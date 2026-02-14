import base64
import os
import re
import threading
import time
from urllib.parse import parse_qs, unquote, urldefrag, urljoin, urlparse

import requests
from playwright.sync_api import sync_playwright

from TimerTimer import TimerTimer


class MonitorM3U8:
    PLAYER_SELECTORS = [
        "video",
        "audio",
        "button[aria-label*='play' i]",
        "button[title*='play' i]",
        ".play-button",
        ".vjs-play-control",
        ".jw-icon-playback",
        ".dplayer-play-icon",
        ".art-control-play",
        ".xgplayer-play",
        ".ckplayer .ck-play",
        ".player .play",
        "[data-testid*='play' i]",
    ]

    @staticmethod
    def _default_user_agent():
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        )

    def __init__(self, URL, deep=True, depth=2, proxy_config=None, monitor_config=None):
        self.timer = TimerTimer(1, self.TimerPrint)
        self.URL = URL
        self.possible = set()
        self.predicted = set()
        self.page_candidates = set()
        self.url_hints = {}
        self.lock = threading.Lock()
        self.depth = 0 if not deep else depth
        self.depth = self.depth if 0 <= self.depth <= 3 else 0
        self.proxy_config = self._normalize_proxy_config(proxy_config)
        self.monitor_config = self._normalize_monitor_config(monitor_config)
        self.headless = self.monitor_config["headless"]
        self.monitor_headers = self._build_monitor_headers()
        self.last_monitor_error = ""
        self.last_blocked_by_client = False
        self.session_hints = {
            "source_url": self.URL,
            "final_url": self.URL,
            "user_agent": self.monitor_headers.get("user-agent", ""),
            "cookies": [],
            "referer_map": {},
        }

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
    def _normalize_monitor_config(monitor_config):
        data = monitor_config if isinstance(monitor_config, dict) else {}

        def _to_bool(value, default):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ["1", "true", "on", "yes"]
            if isinstance(value, (int, float)):
                return value != 0
            return default

        # 兼容老逻辑：仍允许通过环境变量覆盖无界面模式
        env_headless = str(os.getenv("M3U8_MONITOR_HEADLESS", "")).strip().lower()
        env_headless_value = None
        if env_headless != "":
            env_headless_value = env_headless not in ["0", "false", "off", "no"]

        headless = _to_bool(data.get("headless"), True)
        if env_headless_value is not None:
            headless = env_headless_value

        return {
            "headless": headless,
        }

    def _playwright_proxy(self):
        if not self.proxy_config["enabled"]:
            return None
        proxy = {
            "server": f"http://{self.proxy_config['address']}:{self.proxy_config['port']}"
        }
        if self.proxy_config["username"] != "":
            proxy["username"] = self.proxy_config["username"]
        if self.proxy_config["password"] != "":
            proxy["password"] = self.proxy_config["password"]
        return proxy

    def _build_monitor_headers(self):
        headers = {
            "user-agent": self._default_user_agent(),
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "referer": self.URL,
        }
        return {k: v for k, v in headers.items() if v}

    @staticmethod
    def _domain_key(url):
        host = (urlparse(url).hostname or "").lower().strip(".")
        if host == "":
            return ""
        items = host.split(".")
        if len(items) < 2:
            return host
        return ".".join(items[-2:])

    def _same_site(self, url_a, url_b):
        key_a = self._domain_key(url_a)
        key_b = self._domain_key(url_b)
        return key_a != "" and key_a == key_b

    @staticmethod
    def decode(url):
        try:
            raw = str(url).rstrip("\\")
            raw = raw.replace("\\\\", "\\")
            decoded = raw.encode("utf-8").decode("unicode_escape")
            return decoded if decoded != raw else ""
        except Exception:
            return ""

    def _normalize_url(self, url, base_url=""):
        if not isinstance(url, str):
            return ""

        normalized = url.strip().strip("\"'").rstrip("\\")
        if normalized == "":
            return ""

        normalized = normalized.replace("\\/", "/")
        decoded = self.decode(normalized)
        if decoded != "":
            normalized = decoded

        if base_url != "":
            normalized = urljoin(base_url, normalized)

        try:
            normalized, _ = urldefrag(normalized)
            parsed = urlparse(normalized)
        except Exception:
            return ""

        if parsed.scheme not in ["http", "https"]:
            return ""

        return normalized

    @staticmethod
    def _is_m3u8_url(url):
        return ".m3u8" in str(url).lower()

    @staticmethod
    def _predict_variant(url, target_name):
        updated = re.sub(r"[^\/\?\#]+\.m3u8", target_name, url, flags=re.IGNORECASE)
        return updated if updated != url else ""

    def _is_page_candidate(self, url):
        if url == "" or self._is_m3u8_url(url):
            return False

        parsed = urlparse(url)
        path = parsed.path.lower()
        ext_match = re.search(r"\.([a-z0-9]{1,8})$", path)
        if ext_match:
            ext = ext_match.group(1)
            blocked = {
                "ts",
                "m4s",
                "mp4",
                "mp3",
                "mkv",
                "jpg",
                "jpeg",
                "png",
                "gif",
                "svg",
                "webp",
                "css",
                "js",
                "json",
                "xml",
                "txt",
                "ico",
                "woff",
                "woff2",
            }
            if ext in blocked:
                return False
        return True

    def _add_page_candidate(self, raw_url, base_url=""):
        candidate = self._normalize_url(raw_url, base_url)
        if candidate == "" or not self._is_page_candidate(candidate):
            return
        with self.lock:
            self.page_candidates.add(candidate)

    def _add_m3u8_candidate(self, raw_url, referer=""):
        candidate = self._normalize_url(raw_url)
        if candidate == "" or not self._is_m3u8_url(candidate):
            return

        with self.lock:
            self.possible.add(candidate)
            if referer:
                self.url_hints[candidate] = referer
                self.session_hints["referer_map"][candidate] = referer

            guessed_index = self._predict_variant(candidate, "index.m3u8")
            if guessed_index:
                self.predicted.add(guessed_index)
                if referer:
                    self.session_hints["referer_map"][guessed_index] = referer

            guessed_mixed = self._predict_variant(candidate, "mixed.m3u8")
            if guessed_mixed:
                self.predicted.add(guessed_mixed)
                if referer:
                    self.session_hints["referer_map"][guessed_mixed] = referer

        # 兼容解析页格式：?url=https://real.cdn/xx/index.m3u8
        self._extract_nested_m3u8_from_wrapper(candidate, referer=referer)

    def _extract_nested_m3u8_from_wrapper(self, wrapper_url, referer=""):
        try:
            parsed = urlparse(wrapper_url)
            query = parse_qs(parsed.query, keep_blank_values=False)
        except Exception:
            return

        for key in ["url", "v", "source", "src"]:
            values = query.get(key, [])
            for raw in values:
                decoded = unquote(str(raw))
                nested = self._normalize_url(decoded)
                if nested == "" or not self._is_m3u8_url(nested):
                    continue
                with self.lock:
                    self.possible.add(nested)
                    if referer:
                        self.url_hints[nested] = referer
                        self.session_hints["referer_map"][nested] = referer

    def _extract_urls_from_text(self, text, base_url):
        found = set()
        if not isinstance(text, str) or text == "":
            return found

        absolute_urls = re.findall(r"https?://[^\s\'\"<>()]+", text, flags=re.IGNORECASE)
        escaped_urls = re.findall(r"https?:\\\\/\\\\/[^\s\'\"<>()]+", text, flags=re.IGNORECASE)
        relative_m3u8 = re.findall(r"[\"']([^\"'\s]+?\.m3u8[^\"'\s]*)[\"']", text, flags=re.IGNORECASE)

        for raw_url in absolute_urls + escaped_urls:
            normalized = self._normalize_url(raw_url.replace("\\/", "/"), base_url)
            if normalized != "":
                found.add(normalized)

        for raw_url in relative_m3u8:
            normalized = self._normalize_url(raw_url, base_url)
            if normalized != "":
                found.add(normalized)

        return found

    @staticmethod
    def _decode_player_url(raw_url, encrypt_value="0"):
        value = str(raw_url).strip()
        if value == "":
            return ""
        encrypt = str(encrypt_value).strip()
        try:
            if encrypt == "1":
                return unquote(value)
            if encrypt == "2":
                b64_source = value
                if "%" in value:
                    b64_source = unquote(value)
                padded = b64_source + "=" * ((4 - len(b64_source) % 4) % 4)
                decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
                return unquote(decoded)
        except Exception:
            return value
        return value

    def _extract_player_config_candidates(self, text, base_url):
        found = set()
        if not isinstance(text, str) or text == "":
            return found

        # 常见 MacCMS/XGPlayer 参数块：player_xxx = {...}
        for block in re.findall(r"player_[a-z0-9_]+\s*=\s*(\{.*?\})\s*;", text, flags=re.IGNORECASE | re.DOTALL):
            encrypt_match = re.search(
                r"[\"']encrypt[\"']\s*:\s*[\"']?([0-9]+)[\"']?",
                block,
                flags=re.IGNORECASE,
            )
            encrypt = encrypt_match.group(1) if encrypt_match else "0"

            for url_match in re.findall(
                r"[\"']url[\"']\s*:\s*[\"']([^\"']+)[\"']",
                block,
                flags=re.IGNORECASE,
            ):
                decoded = self._decode_player_url(url_match, encrypt)
                normalized = self._normalize_url(decoded, base_url)
                if normalized != "":
                    found.add(normalized)

            for parse_match in re.findall(
                r"[\"']parse[\"']\s*:\s*[\"']([^\"']+)[\"']",
                block,
                flags=re.IGNORECASE,
            ):
                normalized = self._normalize_url(parse_match, base_url)
                if normalized != "":
                    found.add(normalized)

        # 兜底：只要出现 url/source/src 变量，尝试解析
        for field_match in re.findall(
            r"(?:url|source|src)\s*[:=]\s*[\"']([^\"']+?)[\"']",
            text,
            flags=re.IGNORECASE,
        ):
            if ".m3u8" not in field_match.lower() and "http" not in field_match.lower():
                continue
            normalized = self._normalize_url(field_match, base_url)
            if normalized != "":
                found.add(normalized)

        return found

    def _extract_candidate_urls_from_text(self, text, base_url):
        found = set(self._extract_urls_from_text(text, base_url))
        found.update(self._extract_player_config_candidates(text, base_url))
        return found

    def _extract_script_sources(self, text, base_url):
        found = []
        if not isinstance(text, str) or text == "":
            return found
        for raw_src in re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", text, flags=re.IGNORECASE):
            normalized = self._normalize_url(raw_src, base_url)
            if normalized != "":
                found.append(normalized)
        return list(dict.fromkeys(found))

    def _fallback_probe_with_requests(self):
        if len(self.possible) > 0:
            return
        print("\trequests fallback probe started")
        script_probe_limit = 10
        try:
            session = requests.Session()
            session.trust_env = False
            if self.proxy_config["enabled"]:
                auth = ""
                if self.proxy_config["username"] or self.proxy_config["password"]:
                    auth = f"{self.proxy_config['username']}:{self.proxy_config['password']}@"
                proxy_url = f"http://{auth}{self.proxy_config['address']}:{self.proxy_config['port']}"
                session.proxies.update({"http": proxy_url, "https": proxy_url})

            headers = dict(self.monitor_headers)
            headers["referer"] = self.URL
            response = session.get(self.URL, headers=headers, timeout=(8, 12))
            response.raise_for_status()
            body = response.text

            for found_url in self._extract_candidate_urls_from_text(body, self.URL):
                if self._is_m3u8_url(found_url):
                    self._add_m3u8_candidate(found_url, referer=self.URL)
                else:
                    self._add_page_candidate(found_url)

            for script_url in self._extract_script_sources(body, self.URL)[:script_probe_limit]:
                try:
                    script_headers = dict(headers)
                    script_headers["referer"] = self.URL
                    js_resp = session.get(script_url, headers=script_headers, timeout=(6, 10))
                    js_resp.raise_for_status()
                    js_body = js_resp.text
                except Exception:
                    continue
                for found_url in self._extract_candidate_urls_from_text(js_body, script_url):
                    if self._is_m3u8_url(found_url):
                        self._add_m3u8_candidate(found_url, referer=script_url)
                    else:
                        self._add_page_candidate(found_url)
        except Exception as exc:
            print(f"\trequests fallback probe failed: {exc}")
        finally:
            print("\trequests fallback probe done")

    @staticmethod
    def _is_wrapper_candidate(url):
        lowered = str(url).lower()
        return "?url=http" in lowered or "&url=http" in lowered

    def _m3u8_priority(self, url):
        score = 0
        lowered = str(url).lower()
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        if self._is_m3u8_url(url):
            score += 5
        if ".m3u8" in parsed.path.lower():
            score += 5
        if "index.m3u8" in lowered:
            score += 2
        if "mixed.m3u8" in lowered:
            score -= 1
        if self._is_wrapper_candidate(url):
            score -= 6
        if host.startswith("vip.") and self._is_wrapper_candidate(url):
            score -= 2
        if self._same_site(url, self.URL):
            score += 1
        if "token=" in lowered or "auth=" in lowered or "sign=" in lowered:
            score += 1

        return score

    def _ordered_m3u8_lists(self):
        possible = list(self.possible)
        predicted = list(self.predicted)
        possible.sort(key=lambda u: (-self._m3u8_priority(u), u))
        predicted.sort(key=lambda u: (-self._m3u8_priority(u), u))
        return possible, predicted

    def _has_strong_candidate(self):
        for item in self.possible:
            if self._m3u8_priority(item) >= 6:
                return True
        return False

    @staticmethod
    def _looks_like_blocked_url(url):
        lowered = str(url).strip().lower()
        return lowered.startswith("chrome-error://") or "err_blocked_by_client" in lowered

    def _is_blocked_page(self, page):
        if self._looks_like_blocked_url(getattr(page, "url", "")):
            return True
        try:
            for frame in page.frames:
                if self._looks_like_blocked_url(getattr(frame, "url", "")):
                    return True
        except Exception:
            pass
        try:
            return page.locator("text=ERR_BLOCKED_BY_CLIENT").count() > 0
        except Exception:
            return False

    def _extract_candidates_from_page(self, page):
        try:
            page_url = self._normalize_url(page.url) or self.URL
            body = page.content()
        except Exception:
            return

        for found_url in self._extract_candidate_urls_from_text(body, page_url):
            if self._is_m3u8_url(found_url):
                self._add_m3u8_candidate(found_url, referer=page_url)
            elif self.depth == 3:
                self._add_page_candidate(found_url)

    def _recover_page_if_needed(self, page, stable_url):
        try:
            raw_page_url = str(getattr(page, "url", ""))
        except Exception:
            raw_page_url = ""

        stable = self._normalize_url(stable_url) or self.URL
        current = self._normalize_url(raw_page_url)

        if self._looks_like_blocked_url(raw_page_url):
            self.last_blocked_by_client = True
            try:
                page.goto(stable, wait_until="domcontentloaded", timeout=12000)
            except Exception:
                pass
            return

        if current == "":
            return
        if self._is_m3u8_url(current):
            self._add_m3u8_candidate(current, referer=stable)
            return
        if self._same_site(current, stable):
            return

        # 点击后发生跨站跳转时优先回到原页面，避免被广告页“带跑偏”
        self._add_page_candidate(current)
        try:
            page.go_back(wait_until="domcontentloaded", timeout=5000)
        except Exception:
            try:
                page.goto(stable, wait_until="domcontentloaded", timeout=12000)
            except Exception:
                pass

    def handle_response(self, response):
        response_url = self._normalize_url(getattr(response, "url", ""))
        referer = ""
        status = getattr(response, "status", 0)
        headers = {}

        try:
            headers = response.headers or {}
        except Exception:
            headers = {}

        try:
            referer = response.request.headers.get("referer", "")
        except Exception:
            referer = ""

        if self._is_m3u8_url(response_url):
            self._add_m3u8_candidate(response_url, referer=referer or self.URL)

        if self.depth not in [2, 3]:
            return

        if self.depth == 3 and status in [301, 302, 303, 307, 308]:
            redirect_to = headers.get("location", "")
            self._add_page_candidate(redirect_to, base_url=response_url)

        if status != 200:
            return

        content_type = str(headers.get("content-type", "")).lower()
        text_like = (
            content_type == ""
            or "text" in content_type
            or "json" in content_type
            or "javascript" in content_type
            or "xml" in content_type
            or "mpegurl" in content_type
        )
        if not text_like:
            return

        try:
            body_text = response.text()
        except Exception:
            return

        for found_url in self._extract_candidate_urls_from_text(body_text, response_url):
            if self._is_m3u8_url(found_url):
                self._add_m3u8_candidate(found_url, referer=referer or response_url or self.URL)
            elif self.depth == 3:
                self._add_page_candidate(found_url)

    def handle_request(self, request):
        req_url = self._normalize_url(getattr(request, "url", ""))
        if req_url == "":
            return
        referer = ""
        try:
            referer = request.headers.get("referer", "")
        except Exception:
            referer = ""
        if self._is_m3u8_url(req_url):
            self._add_m3u8_candidate(req_url, referer=referer or self.URL)

    def handle_request_failed(self, request):
        req_url = str(getattr(request, "url", ""))
        if "ERR_BLOCKED_BY_CLIENT" in req_url.upper():
            self.last_blocked_by_client = True
        failure_text = ""
        try:
            failure = request.failure or {}
            failure_text = str(failure.get("errorText", "")).upper()
        except Exception:
            failure_text = ""
        if "BLOCKED_BY_CLIENT" in failure_text:
            self.last_blocked_by_client = True

    def _wait_for_new_candidates(self, page, before_count, timeout_ms=3000):
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            if len(self.possible) > before_count:
                return True
            page.wait_for_timeout(250)
        return False

    def _try_click_selectors(self, target, selectors, page_for_recover=None, stable_url=""):
        for selector in selectors:
            try:
                locator = target.locator(selector)
                count = min(locator.count(), 2)
            except Exception:
                continue

            for i in range(count):
                try:
                    element = locator.nth(i)
                    if not element.is_visible(timeout=600):
                        continue
                    element.click(timeout=1400)
                    if page_for_recover is not None:
                        page_for_recover.wait_for_timeout(250)
                        self._recover_page_if_needed(page_for_recover, stable_url or self.URL)
                    return True
                except Exception:
                    continue
        return False

    def _try_play_media_elements(self, target):
        script = """
            (elements) => {
                let played = 0;
                elements.forEach((el) => {
                    try {
                        el.muted = true;
                        el.preload = 'auto';
                        if (typeof el.play === 'function') {
                            el.play().catch(() => {});
                            played += 1;
                        }
                    } catch (e) {}
                });
                return played;
            }
        """
        try:
            return target.eval_on_selector_all("video, audio", script)
        except Exception:
            return 0

    def _collect_recursive_candidates(self, page):
        self._add_page_candidate(page.url)

        try:
            hrefs = page.eval_on_selector_all(
                "a[href]",
                "els => els.slice(0, 120).map(el => el.href).filter(Boolean)",
            )
        except Exception:
            hrefs = []

        for href in hrefs:
            self._add_page_candidate(href)

        for frame in page.frames:
            frame_url = self._normalize_url(getattr(frame, "url", ""))
            if frame_url != "":
                self._add_page_candidate(frame_url)

    @staticmethod
    def _merge_cookies(existing, incoming):
        merged = []
        seen = set()
        for cookie in (existing or []) + (incoming or []):
            if not isinstance(cookie, dict):
                continue
            key = (
                cookie.get("name", ""),
                cookie.get("domain", ""),
                cookie.get("path", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(cookie)
        return merged

    def _update_session_hints(self, context, page):
        self.session_hints["final_url"] = self._normalize_url(page.url) or self.URL
        merged_referer = dict(self.session_hints.get("referer_map", {}))
        merged_referer.update(self.url_hints)
        self.session_hints["referer_map"] = merged_referer
        try:
            cookies = context.cookies()
        except Exception:
            cookies = []
        self.session_hints["cookies"] = self._merge_cookies(self.session_hints.get("cookies", []), cookies)

    def _try_trigger_player(self, page, interaction_stage=1):
        stable_url = self._normalize_url(page.url) or self.URL

        before = len(self.possible)
        self._extract_candidates_from_page(page)
        self._try_play_media_elements(page)
        self._wait_for_new_candidates(page, before, timeout_ms=1400)
        self._recover_page_if_needed(page, stable_url)

        if interaction_stage <= 0:
            return

        click_rounds = 1 if interaction_stage == 1 else 2
        for _ in range(click_rounds):
            before = len(self.possible)
            self._try_click_selectors(
                page,
                self.PLAYER_SELECTORS,
                page_for_recover=page,
                stable_url=stable_url,
            )
            page.wait_for_timeout(300)
            if self._wait_for_new_candidates(page, before, timeout_ms=1800):
                break

        if interaction_stage <= 1:
            self._extract_candidates_from_page(page)
            return

        for scroll_y in [240, 800, 1500]:
            before = len(self.possible)
            try:
                page.mouse.wheel(0, scroll_y)
            except Exception:
                pass
            page.wait_for_timeout(250)
            self._wait_for_new_candidates(page, before, timeout_ms=1200)
            self._recover_page_if_needed(page, stable_url)

        for frame in page.frames:
            if frame == page.main_frame:
                continue
            before = len(self.possible)
            self._try_play_media_elements(frame)
            self._try_click_selectors(
                frame,
                self.PLAYER_SELECTORS,
                page_for_recover=page,
                stable_url=stable_url,
            )
            self._wait_for_new_candidates(page, before, timeout_ms=1400)
            self._recover_page_if_needed(page, stable_url)

        viewport = page.viewport_size or {"width": 1280, "height": 720}
        before = len(self.possible)
        try:
            page.mouse.click(viewport["width"] // 2, viewport["height"] // 2)
            page.keyboard.press("Space")
        except Exception:
            pass
        page.wait_for_timeout(500)
        self._wait_for_new_candidates(page, before, timeout_ms=1200)
        self._recover_page_if_needed(page, stable_url)
        self._extract_candidates_from_page(page)

    def MonitorUrl(self):
        def __launch_browser(playwright_driver, launch_kwargs):
            last_error = None
            try:
                browser = playwright_driver.chromium.launch(**launch_kwargs)
                return browser
            except Exception as exc:
                last_error = exc
            if last_error is not None:
                raise last_error
            raise RuntimeError("failed to launch browser")

        def __monitor_single(playwright_driver, interaction_stage=1):
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ]
            try:
                print(f"\tplaywright chromium executable={playwright_driver.chromium.executable_path}")
            except Exception:
                pass
            launch_kwargs = {
                "headless": self.headless,
                "args": launch_args,
            }

            proxy = self._playwright_proxy()
            if proxy is not None:
                launch_kwargs["proxy"] = proxy
            else:
                # 未显式配置代理时，固定关闭环境代理，保证行为可预测
                launch_args.extend(["--no-proxy-server", "--proxy-bypass-list=*"])

            browser = None
            context = None
            try:
                browser = __launch_browser(playwright_driver, launch_kwargs)
                print("\tlaunch browser actual=chromium")
                context = browser.new_context(
                    user_agent=self.monitor_headers.get("user-agent", self._default_user_agent()),
                    locale="zh-CN",
                    viewport={"width": 1366, "height": 768},
                    ignore_https_errors=True,
                )
                context.add_init_script(
                    """
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                    """
                )
                extra_headers = {k: v for k, v in self.monitor_headers.items() if k != "user-agent"}
                if extra_headers:
                    context.set_extra_http_headers(extra_headers)

                page = context.new_page()
                page.set_default_timeout(12000)
                page.on("response", self.handle_response)
                page.on("request", self.handle_request)
                page.on("requestfailed", self.handle_request_failed)

                def _on_popup(popup):
                    popup.on("response", self.handle_response)
                    popup.on("request", self.handle_request)
                    popup.on("requestfailed", self.handle_request_failed)
                    try:
                        popup.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                    popup_url = self._normalize_url(getattr(popup, "url", ""))
                    if popup_url != "":
                        self._add_page_candidate(popup_url)
                    try:
                        popup.close()
                    except Exception:
                        pass
                    self._recover_page_if_needed(page, self.URL)

                context.on("page", _on_popup)

                page.goto(self.URL, wait_until="domcontentloaded", timeout=18000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

                self._extract_candidates_from_page(page)
                if self.depth in [2, 3]:
                    self._try_trigger_player(page, interaction_stage=interaction_stage)

                if self.depth == 3:
                    self._collect_recursive_candidates(page)

                if self._is_blocked_page(page):
                    self.last_blocked_by_client = True
                    self.last_monitor_error = "ERR_BLOCKED_BY_CLIENT"
                    self._recover_page_if_needed(page, self.URL)
                    self._extract_candidates_from_page(page)

                self._update_session_hints(context, page)
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass

        print(f"\n\t****monitor started****\nURL={self.URL}")
        print(f"\theadless={self.headless}; depth={self.depth}")
        if self.proxy_config["enabled"]:
            print(
                f"\t****proxy****\n"
                f"server={self.proxy_config['address']}:{self.proxy_config['port']}\n"
                f"user={self.proxy_config['username'] or '(none)'}"
            )

        tries = 3 if self.depth in [0, 2] else 5
        with sync_playwright() as p:
            for attempt in range(tries):
                before = len(self.possible)
                interaction_stage = 0
                if self.depth in [2, 3]:
                    interaction_stage = 1 if attempt == 0 else 2

                print(
                    f"\tmonitor attempt {attempt + 1}/{tries} "
                    f"interaction={interaction_stage} "
                    f"channel=chromium-only"
                )
                try:
                    __monitor_single(
                        p,
                        interaction_stage=interaction_stage,
                    )
                except Exception as exc:
                    self.last_monitor_error = str(exc)
                    print(f"\tmonitor attempt {attempt + 1}/{tries} failed: {exc}")
                    continue

                # depth=2时，只有拿到“高置信候选”才提前结束，避免首轮命中解析页后直接停下
                if len(self.possible) > before and self.depth != 3 and self._has_strong_candidate():
                    break
                if len(self.possible) > before and self.depth == 3 and len(self.page_candidates) > 0:
                    break

                if self.last_blocked_by_client:
                    print("\tblocked-by-client detected, next attempt will switch strategy")

        if len(self.possible) == 0 and self.last_monitor_error != "":
            print(f"\tmonitor ended with last error: {self.last_monitor_error}")
        if len(self.possible) == 0:
            self._fallback_probe_with_requests()

        print("\n\n\t****monitor done****")
        return list(self._ordered_m3u8_lists())

    def _rank_recursive_candidates(self):
        same_site = []
        cross_site = []
        for url in list(self.page_candidates):
            if self._same_site(url, self.URL):
                same_site.append(url)
            else:
                cross_site.append(url)
        same_site.sort()
        cross_site.sort()
        return same_site + cross_site

    def _run_controlled_recursion(self, possible, predicted):
        ranked = self._rank_recursive_candidates()
        if len(ranked) == 0:
            return possible, predicted

        max_nodes = 8
        max_cross_site_nodes = 2
        cross_site_used = 0
        visited = {self._normalize_url(self.URL)}
        processed_nodes = 0

        print("\n\n\t\t********controlled recursion started********")
        for target_url in ranked:
            if processed_nodes >= max_nodes:
                break

            target = self._normalize_url(target_url)
            if target == "" or target in visited:
                continue
            visited.add(target)

            if not self._same_site(target, self.URL):
                if cross_site_used >= max_cross_site_nodes:
                    continue
                cross_site_used += 1

            print(f"\t\t>> recurse page: {target}")
            child = MonitorM3U8(
                target,
                True,
                2,
                self.proxy_config,
                monitor_config=self.monitor_config,
            )
            child_possible, child_predicted = child.simple()
            possible.extend(child_possible)
            predicted.extend(child_predicted)
            processed_nodes += 1

            child_hints = child.get_session_hints()
            self.session_hints["cookies"] = self._merge_cookies(
                self.session_hints.get("cookies", []),
                child_hints.get("cookies", []),
            )
            self.session_hints["referer_map"].update(child_hints.get("referer_map", {}))

        print("\n\n\t\t********controlled recursion done********")
        return possible, predicted

    def TimerPrint(self, cnt):
        print(f"\rwaiting **{cnt}** s for resources to find", end="")

    def get_session_hints(self):
        return {
            "source_url": self.session_hints.get("source_url", self.URL),
            "final_url": self.session_hints.get("final_url", self.URL),
            "user_agent": self.session_hints.get("user_agent", ""),
            "cookies": [dict(item) for item in self.session_hints.get("cookies", [])],
            "referer_map": dict(self.session_hints.get("referer_map", {})),
        }

    def simple(self):
        self.timer.StartTimer()
        possible, predicted = self.MonitorUrl()
        self.timer.StopTimer()

        if possible == [] and predicted == []:
            print("find no resource to download\n\n")
        else:
            [print(f"possible m3u8\t= {i}") for i in list(possible)]
            [print(f"predicted m3u8\t= {i}") for i in list(predicted)]
            print("\n\n")

        if self.depth == 3:
            possible, predicted = self._run_controlled_recursion(possible, predicted)
            possible = list(dict.fromkeys(possible))
            predicted = list(dict.fromkeys(predicted))
            print(f"\t\t\t>> Depth = {self.depth}\n\t>> All Resources Found:")
            [print(f"possible m3u8\t= {i}") for i in list(possible)]
            [print(f"predicted m3u8\t= {i}") for i in list(predicted)]

        return [possible, predicted]
