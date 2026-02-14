import base64
import fnmatch
import json
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
    DEFAULT_RULES_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "monitor.rules.json",
    )

    @staticmethod
    def _default_user_agent():
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        )

    @staticmethod
    def _to_bool(value, default=False):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(value, (int, float)):
            return value != 0
        return default

    @staticmethod
    def _to_int(value, default=0, min_value=None, max_value=None):
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        if min_value is not None:
            number = max(min_value, number)
        if max_value is not None:
            number = min(max_value, number)
        return number

    @staticmethod
    def _to_text_list(value):
        if isinstance(value, (list, tuple, set)):
            items = []
            for item in value:
                text = str(item).strip()
                if text:
                    items.append(text)
            return items
        text = str(value).strip() if value is not None else ""
        return [text] if text else []

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
        self.monitor_rules = self._load_monitor_rules()
        self.active_interaction_rule = self._resolve_active_interaction_rule(self.URL)
        self.action_handlers = self._build_action_handlers()

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

        # 兼容老逻辑：仍允许通过环境变量覆盖无界面模式
        env_headless = str(os.getenv("M3U8_MONITOR_HEADLESS", "")).strip().lower()
        env_headless_value = None
        if env_headless != "":
            env_headless_value = env_headless not in ["0", "false", "off", "no"]

        headless = MonitorM3U8._to_bool(data.get("headless"), True)
        if env_headless_value is not None:
            headless = env_headless_value

        rules_path = str(
            data.get("rules_path")
            or data.get("rulesPath")
            or data.get("monitorRulesPath")
            or ""
        ).strip()

        return {
            "headless": headless,
            "rules_path": rules_path,
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

    def _resolve_rules_path(self, raw_path):
        path = str(raw_path or "").strip() or self.DEFAULT_RULES_PATH
        expanded = os.path.expanduser(os.path.expandvars(path))
        if os.path.isabs(expanded):
            return os.path.normpath(expanded)
        if os.path.dirname(expanded) == "":
            return os.path.normpath(
                os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    expanded,
                )
            )
        return os.path.normpath(os.path.join(os.getcwd(), expanded))

    @classmethod
    def _builtin_default_rules(cls):
        return {
            "chains": {
                "monitor_first_pass": [
                    {
                        "type": "extract",
                        "args": {},
                    },
                    {
                        "type": "play_media",
                        "args": {
                            "target": "page",
                        },
                    },
                    {
                        "type": "wait_for_candidates",
                        "args": {
                            "ms": 1400,
                        },
                    },
                    {
                        "type": "click",
                        "args": {
                            "selectors": [
                                "$player",
                            ],
                            "repeat": 1,
                            "wait_for_candidates_ms": 1800,
                        },
                    },
                    {
                        "type": "recover",
                        "args": {},
                    },
                    {
                        "type": "extract",
                        "args": {},
                    },
                ],
                "monitor_retry_pass": [
                    {
                        "type": "click",
                        "args": {
                            "selectors": [
                                "$player",
                            ],
                            "target": "all",
                            "repeat": 2,
                            "wait_for_candidates_ms": 1500,
                        },
                    },
                    {
                        "type": "scroll",
                        "args": {
                            "deltas": [240, 800, 1500],
                            "wait_for_candidates_ms": 1200,
                        },
                    },
                    {
                        "type": "mouse_click",
                        "args": {
                            "position": {
                                "x": "center",
                                "y": "center",
                            }
                        },
                    },
                    {
                        "type": "press",
                        "args": {
                            "key": "Space",
                        },
                    },
                    {
                        "type": "wait_for_candidates",
                        "args": {
                            "ms": 1200,
                        },
                    },
                    {
                        "type": "recover",
                        "args": {},
                    },
                    {
                        "type": "extract",
                        "args": {},
                    },
                ],
            },
            "global": {
                "actions": [
                    {
                        "type": "chain",
                        "args": {
                            "name": "monitor_first_pass",
                        },
                        "when": "first",
                    },
                    {
                        "type": "chain",
                        "args": {
                            "name": "monitor_retry_pass",
                        },
                        "when": "retry",
                    },
                ],
            },
            "sites": [],
        }

    @staticmethod
    def _safe_write_json(file_path, data):
        folder = os.path.dirname(file_path)
        if folder != "":
            os.makedirs(folder, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def _ensure_rules_file(self, rules_path):
        if os.path.exists(rules_path):
            return
        default_payload = self._builtin_default_rules()
        try:
            self._safe_write_json(rules_path, default_payload)
            print(f"\tmonitor rules created: {rules_path}")
        except Exception as exc:
            print(f"\tmonitor rules create failed: {exc}")

    def _repair_rules_file(self, rules_path):
        default_payload = self._builtin_default_rules()
        try:
            if os.path.exists(rules_path):
                backup_path = f"{rules_path}.broken-{time.strftime('%Y%m%d-%H%M%S')}"
                os.replace(rules_path, backup_path)
                print(f"\tmonitor rules backup: {backup_path}")
        except Exception as exc:
            print(f"\tmonitor rules backup skipped: {exc}")

        try:
            self._safe_write_json(rules_path, default_payload)
            print(f"\tmonitor rules reset: {rules_path}")
        except Exception as exc:
            print(f"\tmonitor rules reset failed: {exc}")
        return default_payload

    def _normalize_action_list(self, actions):
        if not isinstance(actions, list):
            return []
        normalized = []
        for item in actions:
            if isinstance(item, str):
                action_type = item.strip().lower()
                if action_type:
                    normalized.append({"type": action_type, "args": {}})
                continue
            if not isinstance(item, dict):
                continue
            action = dict(item)
            action_type = str(action.get("type", "")).strip().lower()
            if action_type == "":
                continue
            action["type"] = action_type
            args = action.get("args")
            if not isinstance(args, dict):
                args = {}
            # 兼容旧风格：将顶层参数自动归并到 args，统一后续处理方式
            for key in list(action.keys()):
                if key in self._action_meta_keys():
                    continue
                if key not in args:
                    args[key] = action[key]
                action.pop(key, None)
            action["args"] = args
            normalized.append(action)
        return normalized

    @staticmethod
    def _action_meta_keys():
        return {"type", "when", "args"}

    def _action_has_arg(self, action, key):
        if not isinstance(action, dict):
            return False
        args = action.get("args", {})
        if isinstance(args, dict) and key in args:
            return True
        return key in action

    def _action_arg(self, action, key, default=None):
        if not isinstance(action, dict):
            return default
        args = action.get("args", {})
        if isinstance(args, dict) and key in args:
            return args.get(key)
        if key in action:
            return action.get(key)
        return default

    def _normalize_chain_map(self, chains):
        normalized = {}
        if not isinstance(chains, dict):
            return normalized
        for raw_name, raw_actions in chains.items():
            name = str(raw_name).strip()
            if name == "":
                continue
            normalized[name] = self._normalize_action_list(raw_actions)
        return normalized

    def _expand_action_chains(self, actions, chains, trace=None, depth=0):
        if depth > 10:
            print("\tmonitor rules action chain depth exceeded")
            return []

        if trace is None:
            trace = []
        expanded = []

        for raw_action in actions:
            if not isinstance(raw_action, dict):
                continue
            action = dict(raw_action)
            action_type = str(action.get("type", "")).strip().lower()
            if action_type != "chain":
                expanded.append(action)
                continue

            args = action.get("args", {})
            chain_name = str((args.get("name") if isinstance(args, dict) else None) or action.get("name", "")).strip()
            if chain_name == "":
                print("\tmonitor rules chain action skipped: missing name")
                continue
            if chain_name in trace:
                print(
                    f"\tmonitor rules chain skipped: circular reference "
                    f"{' -> '.join(trace + [chain_name])}"
                )
                continue
            if chain_name not in chains:
                print(f"\tmonitor rules chain skipped: not found '{chain_name}'")
                continue

            nested_actions = self._expand_action_chains(
                chains[chain_name],
                chains,
                trace=trace + [chain_name],
                depth=depth + 1,
            )
            override_when = str(
                action.get("when", (args.get("when") if isinstance(args, dict) else ""))
            ).strip().lower()
            if override_when == "":
                expanded.extend(nested_actions)
                continue
            for nested_action in nested_actions:
                updated = dict(nested_action)
                if "when" not in updated:
                    updated["when"] = override_when
                expanded.append(updated)

        return expanded

    def _normalize_rule_entry(self, source, default_name):
        if not isinstance(source, dict):
            return None
        if not self._to_bool(source.get("enabled", True), True):
            return None

        match = source.get("match", {})
        if not isinstance(match, dict):
            match = {}

        host_value = match.get("host", source.get("host", source.get("hosts", [])))
        contains_value = match.get(
            "url_contains",
            source.get("url_contains", source.get("urlContains", [])),
        )
        url_regex = str(
            match.get(
                "url_regex",
                source.get("url_regex", source.get("urlRegex", "")),
            )
            or ""
        ).strip()

        actions = self._normalize_action_list(source.get("actions", []))
        chains = self._normalize_chain_map(source.get("chains", {}))

        return {
            "name": str(source.get("name", default_name)).strip() or default_name,
            "host_patterns": [item.lower() for item in self._to_text_list(host_value)],
            "url_contains": [item.lower() for item in self._to_text_list(contains_value)],
            "url_regex": url_regex,
            "actions": actions,
            "chains": chains,
        }

    def _load_monitor_rules(self):
        rules_path = self._resolve_rules_path(self.monitor_config.get("rules_path", ""))
        self._ensure_rules_file(rules_path)

        payload = {}
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                raise ValueError("root must be object")
        except Exception as exc:
            print(f"\tmonitor rules load failed: {exc}")
            payload = self._repair_rules_file(rules_path)

        default_global = {
            "name": "global",
            "host_patterns": [],
            "url_contains": [],
            "url_regex": "",
            "actions": [],
            "chains": {},
        }
        normalized = {
            "source": rules_path,
            "chains": self._normalize_chain_map(payload.get("chains", {})),
            "global": default_global,
            "sites": [],
        }

        if not isinstance(payload, dict):
            return normalized

        global_source = payload.get("global")
        if not isinstance(global_source, dict):
            global_source = {}
        if "actions" in payload and "actions" not in global_source:
            global_source["actions"] = payload["actions"]
        if "chains" in payload and "chains" not in global_source:
            global_source["chains"] = payload["chains"]

        global_rule = self._normalize_rule_entry(global_source, "global")
        if global_rule is not None:
            global_rule["host_patterns"] = []
            global_rule["url_contains"] = []
            global_rule["url_regex"] = ""
            merged_global_chains = dict(normalized["chains"])
            merged_global_chains.update(global_rule.get("chains", {}))
            global_rule["chains"] = merged_global_chains
            global_rule["actions"] = self._expand_action_chains(
                global_rule.get("actions", []),
                merged_global_chains,
            )
            normalized["chains"] = merged_global_chains
            normalized["global"] = global_rule

        sites = payload.get("sites", [])
        if isinstance(sites, list):
            for index, site in enumerate(sites):
                normalized_site = self._normalize_rule_entry(site, f"site_{index + 1}")
                if normalized_site is not None:
                    merged_site_chains = dict(normalized["chains"])
                    merged_site_chains.update(normalized_site.get("chains", {}))
                    normalized_site["chains"] = merged_site_chains
                    normalized_site["actions"] = self._expand_action_chains(
                        normalized_site.get("actions", []),
                        merged_site_chains,
                    )
                    normalized["sites"].append(normalized_site)

        return normalized

    def _rule_matches_url(self, rule, url):
        if not isinstance(rule, dict):
            return False

        normalized_url = self._normalize_url(url) or str(url or "")
        lowered_url = normalized_url.lower()
        parsed = urlparse(normalized_url)
        host = (parsed.hostname or "").lower()

        host_patterns = rule.get("host_patterns", [])
        if host_patterns and not any(fnmatch.fnmatch(host, pattern) for pattern in host_patterns):
            return False

        url_contains = rule.get("url_contains", [])
        if url_contains and not any(keyword in lowered_url for keyword in url_contains):
            return False

        url_regex = str(rule.get("url_regex", "")).strip()
        if url_regex != "":
            try:
                if re.search(url_regex, normalized_url, flags=re.IGNORECASE) is None:
                    return False
            except re.error:
                return False

        return True

    def _resolve_active_interaction_rule(self, target_url):
        global_rule = self.monitor_rules.get("global", {})
        active = {
            "name": str(global_rule.get("name", "global")).strip() or "global",
            "source": self.monitor_rules.get("source", ""),
            "matched_sites": [],
            "actions": list(global_rule.get("actions", [])),
        }

        for site_rule in self.monitor_rules.get("sites", []):
            if not self._rule_matches_url(site_rule, target_url):
                continue
            active["matched_sites"].append(site_rule.get("name", "site"))
            active["actions"].extend(site_rule.get("actions", []))

        if active["matched_sites"]:
            active["name"] = ",".join(active["matched_sites"])

        return active

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

    def _try_click_selectors(
        self,
        target,
        selectors,
        page_for_recover=None,
        stable_url="",
        max_per_selector=2,
        visible_timeout_ms=600,
        click_timeout_ms=1400,
        wait_after_click_ms=250,
    ):
        for selector in selectors:
            try:
                locator = target.locator(selector)
                count = min(locator.count(), max_per_selector)
            except Exception:
                continue

            for i in range(count):
                try:
                    element = locator.nth(i)
                    if not element.is_visible(timeout=visible_timeout_ms):
                        continue
                    element.click(timeout=click_timeout_ms)
                    if page_for_recover is not None:
                        page_for_recover.wait_for_timeout(wait_after_click_ms)
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

    @staticmethod
    def _action_enabled_for_interaction_stage(action, interaction_stage):
        if not isinstance(action, dict):
            return False
        args = action.get("args", {})
        args_when = args.get("when", "all") if isinstance(args, dict) else "all"
        when = str(action.get("when", args_when)).strip().lower()
        if when in {"", "all"}:
            return True
        if when == "first":
            return interaction_stage <= 1
        if when == "retry":
            return interaction_stage > 1
        return True

    def _build_action_handlers(self):
        return {
            "extract": self._action_extract,
            "recover": self._action_recover,
            "wait": self._action_wait,
            "wait_for_candidates": self._action_wait_for_candidates,
            "play_media": self._action_play_media,
            "click": self._action_click,
            "hover": self._action_hover,
            "fill": self._action_fill,
            "wait_for_selector": self._action_wait_for_selector,
            "wait_for_load_state": self._action_wait_for_load_state,
            "goto": self._action_goto,
            "evaluate": self._action_evaluate,
            "scroll": self._action_scroll,
            "mouse_click": self._action_mouse_click,
            "press": self._action_press,
            "log": self._action_log,
        }

    def _iter_action_targets(self, page, target_mode):
        mode = str(target_mode or "page").strip().lower()
        frames = [frame for frame in page.frames if frame != page.main_frame]
        if mode in {"frame", "frames"}:
            return frames
        if mode in {"all", "page_and_frames"}:
            return [page] + frames
        return [page]

    def _resolve_action_selectors(self, action):
        selectors = []
        raw_selectors = self._action_arg(action, "selectors", [])
        if raw_selectors:
            selectors.extend(self._to_text_list(raw_selectors))
        raw_selector = self._action_arg(action, "selector", "")
        if raw_selector:
            selectors.extend(self._to_text_list(raw_selector))

        resolved = []
        for selector in selectors:
            key = selector.strip().lower()
            if key == "$player":
                resolved.extend(self.PLAYER_SELECTORS)
            else:
                resolved.append(selector)
        return list(dict.fromkeys(resolved))

    @staticmethod
    def _wait_until_value(raw_value, default="domcontentloaded"):
        value = str(raw_value or "").strip().lower()
        if value in {"domcontentloaded", "load", "networkidle", "commit"}:
            return value
        return default

    def _resolve_mouse_coordinate(self, raw_value, fallback):
        if isinstance(raw_value, str) and raw_value.strip().lower() in {"center", "middle"}:
            return fallback
        return self._to_int(raw_value, fallback)

    def _action_extract(self, page, action, stable_url, before_count):
        self._extract_candidates_from_page(page)

    def _action_recover(self, page, action, stable_url, before_count):
        self._recover_page_if_needed(page, stable_url)

    def _action_wait(self, page, action, stable_url, before_count):
        wait_ms = self._to_int(self._action_arg(action, "ms", 300), 300, 0, 30000)
        if wait_ms > 0:
            page.wait_for_timeout(wait_ms)

    def _action_wait_for_candidates(self, page, action, stable_url, before_count):
        timeout_ms = self._to_int(self._action_arg(action, "ms", 1500), 1500, 0, 30000)
        self._wait_for_new_candidates(page, before_count, timeout_ms=timeout_ms)

    def _action_play_media(self, page, action, stable_url, before_count):
        for target in self._iter_action_targets(page, self._action_arg(action, "target", "page")):
            self._try_play_media_elements(target)

    def _action_click(self, page, action, stable_url, before_count):
        selectors = self._resolve_action_selectors(action)
        if len(selectors) == 0:
            return

        repeat = self._to_int(self._action_arg(action, "repeat", 1), 1, 1, 20)
        wait_ms = self._to_int(self._action_arg(action, "wait_ms", 300), 300, 0, 30000)
        max_per_selector = self._to_int(self._action_arg(action, "max_per_selector", 2), 2, 1, 20)
        visible_timeout_ms = self._to_int(self._action_arg(action, "visible_timeout_ms", 600), 600, 100, 10000)
        click_timeout_ms = self._to_int(self._action_arg(action, "click_timeout_ms", 1400), 1400, 100, 20000)
        wait_after_click_ms = self._to_int(self._action_arg(action, "wait_after_click_ms", 250), 250, 0, 10000)
        wait_for_candidates_ms = self._to_int(
            self._action_arg(action, "wait_for_candidates_ms", 0),
            0,
            0,
            30000,
        )

        for _ in range(repeat):
            for target in self._iter_action_targets(page, self._action_arg(action, "target", "page")):
                self._try_click_selectors(
                    target,
                    selectors,
                    page_for_recover=page,
                    stable_url=stable_url,
                    max_per_selector=max_per_selector,
                    visible_timeout_ms=visible_timeout_ms,
                    click_timeout_ms=click_timeout_ms,
                    wait_after_click_ms=wait_after_click_ms,
                )
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            if wait_for_candidates_ms > 0:
                self._wait_for_new_candidates(page, before_count, timeout_ms=wait_for_candidates_ms)

    def _action_hover(self, page, action, stable_url, before_count):
        selectors = self._resolve_action_selectors(action)
        if len(selectors) == 0:
            return

        repeat = self._to_int(self._action_arg(action, "repeat", 1), 1, 1, 20)
        max_per_selector = self._to_int(self._action_arg(action, "max_per_selector", 1), 1, 1, 20)
        visible_timeout_ms = self._to_int(self._action_arg(action, "visible_timeout_ms", 600), 600, 100, 10000)
        hover_timeout_ms = self._to_int(self._action_arg(action, "hover_timeout_ms", 1200), 1200, 100, 20000)
        wait_ms = self._to_int(self._action_arg(action, "wait_ms", 200), 200, 0, 30000)
        wait_for_candidates_ms = self._to_int(self._action_arg(action, "wait_for_candidates_ms", 0), 0, 0, 30000)

        for _ in range(repeat):
            for target in self._iter_action_targets(page, self._action_arg(action, "target", "page")):
                for selector in selectors:
                    try:
                        locator = target.locator(selector)
                        count = min(locator.count(), max_per_selector)
                    except Exception:
                        continue

                    for index in range(count):
                        try:
                            element = locator.nth(index)
                            if not element.is_visible(timeout=visible_timeout_ms):
                                continue
                            element.hover(timeout=hover_timeout_ms)
                            break
                        except Exception:
                            continue

            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            if wait_for_candidates_ms > 0:
                self._wait_for_new_candidates(page, before_count, timeout_ms=wait_for_candidates_ms)

    def _action_fill(self, page, action, stable_url, before_count):
        selectors = self._resolve_action_selectors(action)
        if len(selectors) == 0:
            return

        value = str(self._action_arg(action, "value", ""))
        index = self._to_int(self._action_arg(action, "index", 0), 0, 0)
        fill_timeout_ms = self._to_int(self._action_arg(action, "fill_timeout_ms", 2500), 2500, 100, 30000)
        visible_timeout_ms = self._to_int(self._action_arg(action, "visible_timeout_ms", 600), 600, 100, 10000)
        require_visible = self._to_bool(self._action_arg(action, "require_visible", True), True)
        submit_key = str(self._action_arg(action, "submit_key", "")).strip()

        for target in self._iter_action_targets(page, self._action_arg(action, "target", "page")):
            for selector in selectors:
                try:
                    locator = target.locator(selector)
                    count = locator.count()
                except Exception:
                    continue
                if count <= 0:
                    continue
                current_index = min(index, count - 1)
                try:
                    element = locator.nth(current_index)
                    if require_visible and not element.is_visible(timeout=visible_timeout_ms):
                        continue
                    element.fill(value, timeout=fill_timeout_ms)
                    if submit_key != "":
                        element.press(submit_key, timeout=fill_timeout_ms)
                    return
                except Exception:
                    continue

    def _action_wait_for_selector(self, page, action, stable_url, before_count):
        selector = str(self._action_arg(action, "selector", "")).strip()
        if selector == "":
            return
        timeout_ms = self._to_int(self._action_arg(action, "timeout_ms", 5000), 5000, 100, 60000)
        state = str(self._action_arg(action, "state", "visible")).strip().lower() or "visible"

        for target in self._iter_action_targets(page, self._action_arg(action, "target", "page")):
            try:
                target.wait_for_selector(selector, state=state, timeout=timeout_ms)
                return
            except Exception:
                continue

    def _action_wait_for_load_state(self, page, action, stable_url, before_count):
        state = self._wait_until_value(self._action_arg(action, "state"), default="networkidle")
        timeout_ms = self._to_int(self._action_arg(action, "timeout_ms", 8000), 8000, 100, 60000)
        page.wait_for_load_state(state, timeout=timeout_ms)

    def _action_goto(self, page, action, stable_url, before_count):
        raw_url = str(self._action_arg(action, "url", "")).strip()
        if raw_url == "":
            return
        target_url = self._normalize_url(raw_url, stable_url)
        if target_url == "":
            return
        wait_until = self._wait_until_value(self._action_arg(action, "wait_until"), default="domcontentloaded")
        timeout_ms = self._to_int(self._action_arg(action, "timeout_ms", 18000), 18000, 100, 120000)
        page.goto(target_url, wait_until=wait_until, timeout=timeout_ms)

    def _action_evaluate(self, page, action, stable_url, before_count):
        script = self._action_arg(action, "script")
        if not isinstance(script, str) or script.strip() == "":
            return
        script = script.strip()
        selector = str(self._action_arg(action, "selector", "")).strip()
        arg = self._action_arg(action, "arg")

        if selector == "":
            if self._action_has_arg(action, "arg"):
                page.evaluate(script, arg)
            else:
                page.evaluate(script)
            return

        for target in self._iter_action_targets(page, self._action_arg(action, "target", "page")):
            try:
                if self._action_has_arg(action, "arg"):
                    target.eval_on_selector_all(selector, script, arg)
                else:
                    target.eval_on_selector_all(selector, script)
                return
            except Exception:
                continue

    def _action_scroll(self, page, action, stable_url, before_count):
        deltas = self._action_arg(action, "deltas", [])
        if not isinstance(deltas, list) or len(deltas) == 0:
            deltas = [self._action_arg(action, "y", 240)]
        wheel_x = self._to_int(self._action_arg(action, "x", 0), 0)
        wait_after_scroll_ms = self._to_int(self._action_arg(action, "wait_after_scroll_ms", 250), 250, 0, 30000)
        wait_for_candidates_ms = self._to_int(
            self._action_arg(action, "wait_for_candidates_ms", 1200),
            1200,
            0,
            30000,
        )
        recover_after_scroll = self._to_bool(self._action_arg(action, "recover_after_scroll", True), True)

        for delta in deltas:
            try:
                page.mouse.wheel(wheel_x, self._to_int(delta, 0))
            except Exception:
                pass
            if wait_after_scroll_ms > 0:
                page.wait_for_timeout(wait_after_scroll_ms)
            if wait_for_candidates_ms > 0:
                self._wait_for_new_candidates(page, before_count, timeout_ms=wait_for_candidates_ms)
            if recover_after_scroll:
                self._recover_page_if_needed(page, stable_url)

    def _action_mouse_click(self, page, action, stable_url, before_count):
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        position = self._action_arg(action, "position", {})
        if not isinstance(position, dict):
            position = {}
        x = self._resolve_mouse_coordinate(
            position.get("x", self._action_arg(action, "x", viewport["width"] // 2)),
            viewport["width"] // 2,
        )
        y = self._resolve_mouse_coordinate(
            position.get("y", self._action_arg(action, "y", viewport["height"] // 2)),
            viewport["height"] // 2,
        )
        button = str(self._action_arg(action, "button", "left")).strip().lower() or "left"
        click_count = self._to_int(self._action_arg(action, "click_count", 1), 1, 1, 3)
        delay_ms = self._to_int(self._action_arg(action, "delay_ms", 0), 0, 0, 3000)
        page.mouse.click(x, y, button=button, click_count=click_count, delay=delay_ms)

    def _action_press(self, page, action, stable_url, before_count):
        key = str(self._action_arg(action, "key", "")).strip()
        if key == "":
            return
        page.keyboard.press(key)

    def _action_log(self, page, action, stable_url, before_count):
        message = str(self._action_arg(action, "message", "")).strip()
        if message:
            print(f"\tmonitor rule action: {message}")

    def _run_configured_interaction_action(self, page, action, stable_url):
        if not isinstance(action, dict):
            return

        action_type = str(action.get("type", "")).strip().lower()
        if action_type == "":
            return
        handler = self.action_handlers.get(action_type)
        if handler is None:
            print(f"\tmonitor rule action skipped: unknown type={action_type}")
            return

        before = len(self.possible)
        try:
            handler(page, action, stable_url, before)
        except Exception as exc:
            print(f"\tmonitor rule action failed ({action_type}): {exc}")

    def _try_trigger_player(self, page, interaction_stage=1):
        stable_url = self._normalize_url(page.url) or self.URL
        actions = list(self.active_interaction_rule.get("actions", []))
        if len(actions) == 0:
            return
        for action in actions:
            if not self._action_enabled_for_interaction_stage(action, interaction_stage):
                continue
            self._run_configured_interaction_action(page, action, stable_url)
        self._extract_candidates_from_page(page)
        self._recover_page_if_needed(page, stable_url)

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
        configured_actions_count = len(self.active_interaction_rule.get("actions", []))
        if configured_actions_count > 0:
            print(
                f"\tinteraction rules active="
                f"{self.active_interaction_rule.get('name', 'rule')} "
                f"actions={configured_actions_count}"
            )
        elif self.active_interaction_rule.get("source", "") != "":
            print(
                f"\tinteraction rules loaded from {self.active_interaction_rule['source']}, "
                f"but no matching actions for this URL"
            )
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
