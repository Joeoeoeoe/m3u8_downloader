# python库
import json
import os
import re
import sys
import threading
import time
from datetime import datetime

# ui
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThread, QProcess
from PyQt5.QtGui import QTextCursor, QIcon
from PyQt5.QtWidgets import QApplication, QFileDialog, QLineEdit, QMainWindow

from UI.MainWindow import Ui_MainWindow
from UI.ConfigTabWindow import Ui_ConfigWindow

# 自定义
from JsonProcessor import ConfigJson, DownloadJson, ReadDownloadJson
from MonitorM3U8 import MonitorM3U8
from DownloadM3U8 import DownloadM3U8
from SimpleUrlParser import SimpleUrlParser


FILE_EXT_OPTIONS = [".mp4", ".mov", ".avi", ".m4a", ".flv", ".mkv"]
DOWNLOAD_MODE_OPTIONS = ["不下载", "下载首个", "下载前5个", "下载所有"]
STOP_MODE_OPTIONS = ["阶段停止", "强制重启", "强制退出"]
DEFAULT_PROXY_ADDRESS = "127.0.0.1"
DEFAULT_PROXY_PORT = "7897"
PRESET_DIR = os.path.join(os.getcwd(), "config", "preset")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def default_config():
    return {
        "folder": os.path.join(os.getcwd(), "m3u8"),
        "filename": "output",
        "fileExt": 0,
        "fileExtText": FILE_EXT_OPTIONS[0],
        "recursionEnabled": False,
        "recursionDepth": 2,
        "monitorTryEnabled": True,
        "monitorTries": 2,
        "monitorInteraction": True,
        "downloadList": True,
        "downloadMode": 1,
        "downloadModeText": DOWNLOAD_MODE_OPTIONS[1],
        "stopMode": 0,
        "stopModeText": STOP_MODE_OPTIONS[0],
        "listMode": 0,
        "listModeText": "下载未完成",
        "proxyEnabled": False,
        "proxyAddress": DEFAULT_PROXY_ADDRESS,
        "proxyPort": DEFAULT_PROXY_PORT,
        "proxyUser": "",
        "proxyPassword": "",
        "maxParallel": 100,
        "monitorHeadless": True,
        "monitorRulesPath": "config/monitor.rules.json",
    }


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return default


def _to_int(value, default, min_value=None, max_value=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if min_value is not None:
        number = max(min_value, number)
    if max_value is not None:
        number = min(max_value, number)
    return number


def _normalize_recursion_depth(value, default_depth):
    return _to_int(value, default_depth)


def _to_text(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def _normalize_path_text(path_text):
    text = _to_text(path_text, "")
    if text == "":
        return ""
    text = text.strip().strip('"').strip("'")
    text = os.path.expandvars(os.path.expanduser(text))
    if text == "":
        return ""
    text = text.replace("\\", os.sep).replace("/", os.sep)
    text = os.path.normpath(text)
    if not os.path.isabs(text):
        text = os.path.abspath(text)
    return text


def _sanitize_filesystem_path(path_text):
    path = _to_text(path_text, "")
    if path == "":
        return ""
    if os.name != "nt":
        return path.replace("\x00", "_")

    rebuilt = []
    for idx, ch in enumerate(path):
        if ch == ":":
            if idx == 1 and path[0].isalpha():
                rebuilt.append(ch)
            else:
                rebuilt.append("_")
            continue
        if ch in '<>"|?*' or ord(ch) < 32:
            rebuilt.append("_")
            continue
        rebuilt.append(ch)
    path = "".join(rebuilt).replace("/", "\\")

    drive, tail = os.path.splitdrive(path)
    leading_sep = tail.startswith("\\")
    parts = [p for p in re.split(r"[\\/]+", tail) if p not in {"", "."}]
    sanitized_parts = []
    for part in parts:
        if part == "..":
            sanitized_parts.append(part)
            continue
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", part).rstrip(" .")
        if cleaned == "":
            cleaned = "_"
        if cleaned.upper() in WINDOWS_RESERVED_NAMES:
            cleaned = f"_{cleaned}_"
        sanitized_parts.append(cleaned)

    sep = "\\"
    prefix = ""
    if drive != "":
        prefix = drive + (sep if leading_sep else "")
    elif path.startswith("\\\\"):
        prefix = "\\\\"
    elif leading_sep:
        prefix = sep
    body = sep.join(sanitized_parts)

    if body == "":
        if drive != "" and not leading_sep:
            return prefix.rstrip(sep)
        return prefix if prefix != "" else path
    return prefix + body


def _is_structurally_valid_path(path_text):
    path = _to_text(path_text, "")
    if path == "" or "\x00" in path:
        return False
    if os.name != "nt":
        return True
    return _sanitize_filesystem_path(path) == path


def _try_prepare_directory(path, create=False):
    try:
        if os.path.isfile(path):
            return False, "path points to an existing file"
        if create:
            os.makedirs(path, exist_ok=True)
        if os.path.isdir(path):
            return True, ""
        if not create:
            return True, ""
        return False, "directory is not available"
    except Exception as exc:
        return False, str(exc)


def _resolve_directory_input(path_text, fallback_dir="", create=False, allow_fallback=False):
    normalized_fallback = _normalize_path_text(fallback_dir)
    source = _normalize_path_text(path_text)
    if source == "":
        source = normalized_fallback
    if source == "":
        return {"ok": False, "path": "", "reason": "directory is empty", "changed": False}

    if os.path.isfile(source):
        source = os.path.dirname(source)

    candidates = []
    candidate_inputs = [
        source,
        _normalize_path_text(_sanitize_filesystem_path(source)),
    ]
    if allow_fallback and normalized_fallback != "":
        candidate_inputs.append(normalized_fallback)

    for item in candidate_inputs:
        if item != "" and item not in candidates:
            candidates.append(item)

    last_reason = "directory is invalid"
    for candidate in candidates:
        if os.path.isfile(candidate):
            candidate = os.path.dirname(candidate)
        if not _is_structurally_valid_path(candidate):
            last_reason = "directory contains invalid characters"
            continue
        ok, reason = _try_prepare_directory(candidate, create=create)
        if ok:
            return {
                "ok": True,
                "path": candidate,
                "reason": "",
                "changed": candidate != source,
            }
        if reason != "":
            last_reason = reason
    return {"ok": False, "path": source, "reason": last_reason, "changed": False}


def _normalize_filename_input(filename, fallback):
    default_name = _to_text(fallback, "output")
    if default_name == "":
        default_name = "output"

    text = _to_text(filename, "")
    text = text.strip().strip('"').strip("'")
    if text != "":
        text = text.replace("\\", os.sep).replace("/", os.sep)
        text = os.path.basename(text)
        text = os.path.splitext(text)[0].strip()
    if text == "":
        text = default_name

    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).rstrip(" .").strip()
    if text == "":
        text = default_name
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).rstrip(" .").strip()
    if text == "":
        text = "output"
    if os.name == "nt" and text.upper() in WINDOWS_RESERVED_NAMES:
        text = f"_{text}_"
    return text


def _build_proxy_config(data):
    proxy_enabled = _to_bool(data.get("proxyEnabled", False))
    proxy_address = _to_text(data.get("proxyAddress", DEFAULT_PROXY_ADDRESS), DEFAULT_PROXY_ADDRESS)
    proxy_port = _to_text(data.get("proxyPort", DEFAULT_PROXY_PORT), DEFAULT_PROXY_PORT)
    proxy_user = _to_text(data.get("proxyUser", ""))
    proxy_password = _to_text(data.get("proxyPassword", ""))
    if proxy_port and not proxy_port.isdigit():
        proxy_port = DEFAULT_PROXY_PORT
    if proxy_enabled and (proxy_address == "" or proxy_port == ""):
        proxy_enabled = False
    return {
        "enabled": proxy_enabled,
        "address": proxy_address,
        "port": proxy_port,
        "username": proxy_user,
        "password": proxy_password,
    }


def _config_schema_keys():
    return set(default_config().keys())


def _backup_broken_config(file_path):
    if not file_path or not os.path.exists(file_path):
        return ""
    broken_path = f"{file_path}.broken-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    os.replace(file_path, broken_path)
    return broken_path


def _validate_config_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("config must be object")
    expected = _config_schema_keys()
    actual = set(payload.keys())
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise ValueError(f"config schema mismatch missing={missing} extra={extra}")

    bool_keys = {
        "recursionEnabled",
        "monitorTryEnabled",
        "monitorInteraction",
        "downloadList",
        "proxyEnabled",
        "monitorHeadless",
    }
    int_ranges = {
        "fileExt": (0, len(FILE_EXT_OPTIONS) - 1),
        "recursionDepth": (1, 6),
        "monitorTries": (1, 5),
        "downloadMode": (0, len(DOWNLOAD_MODE_OPTIONS) - 1),
        "stopMode": (0, len(STOP_MODE_OPTIONS) - 1),
        "listMode": (0, 2),
        "maxParallel": (1, 999),
    }
    str_keys = {
        "folder",
        "filename",
        "fileExtText",
        "downloadModeText",
        "stopModeText",
        "listModeText",
        "proxyAddress",
        "proxyPort",
        "proxyUser",
        "proxyPassword",
        "monitorRulesPath",
    }

    for key in bool_keys:
        if not isinstance(payload.get(key), bool):
            raise ValueError(f"config value type invalid: {key} must be bool")

    for key, (min_value, max_value) in int_ranges.items():
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"config value type invalid: {key} must be int")
        if value < min_value or value > max_value:
            raise ValueError(f"config value out of range: {key}={value}")

    for key in str_keys:
        if not isinstance(payload.get(key), str):
            raise ValueError(f"config value type invalid: {key} must be str")

    if payload["fileExtText"] not in FILE_EXT_OPTIONS:
        raise ValueError(f"config value invalid: fileExtText={payload['fileExtText']}")
    if payload["downloadModeText"] not in DOWNLOAD_MODE_OPTIONS:
        raise ValueError(f"config value invalid: downloadModeText={payload['downloadModeText']}")
    if payload["stopModeText"] not in STOP_MODE_OPTIONS:
        raise ValueError(f"config value invalid: stopModeText={payload['stopModeText']}")
    if payload["listModeText"] not in {"下载未完成", "下载全部", "重新监测URL"}:
        raise ValueError(f"config value invalid: listModeText={payload['listModeText']}")


def normalize_config_dict(data):
    defaults = default_config()
    incoming = dict(data) if isinstance(data, dict) else {}

    merged = dict(defaults)
    merged.update(incoming)

    file_ext = _to_int(merged.get("fileExt"), defaults["fileExt"], 0, len(FILE_EXT_OPTIONS) - 1)
    download_mode = _to_int(
        merged.get("downloadMode"), defaults["downloadMode"], 0, len(DOWNLOAD_MODE_OPTIONS) - 1
    )
    stop_mode = _to_int(merged.get("stopMode"), defaults["stopMode"], 0, len(STOP_MODE_OPTIONS) - 1)
    list_mode = _to_int(merged.get("listMode"), defaults["listMode"], 0, 2)
    max_parallel = _to_int(merged.get("maxParallel"), defaults["maxParallel"], 1, 999)
    recursion_enabled = _to_bool(merged.get("recursionEnabled"), defaults["recursionEnabled"])
    recursion_depth = _normalize_recursion_depth(merged.get("recursionDepth"), defaults["recursionDepth"])
    recursion_depth = _to_int(recursion_depth, defaults["recursionDepth"], 1, 6)
    monitor_try_enabled = _to_bool(merged.get("monitorTryEnabled"), defaults["monitorTryEnabled"])
    monitor_tries = _to_int(merged.get("monitorTries"), defaults["monitorTries"], 1, 5)
    monitor_interaction = _to_bool(merged.get("monitorInteraction"), defaults["monitorInteraction"])
    monitor_headless = _to_bool(merged.get("monitorHeadless"), defaults["monitorHeadless"])
    monitor_rules_path = _to_text(merged.get("monitorRulesPath"), defaults["monitorRulesPath"])
    if monitor_rules_path == "":
        monitor_rules_path = defaults["monitorRulesPath"]

    raw_folder = _normalize_path_text(merged.get("folder"))
    if raw_folder == "" or not _is_structurally_valid_path(raw_folder):
        folder = defaults["folder"]
    else:
        folder_result = _resolve_directory_input(raw_folder, defaults["folder"], create=False, allow_fallback=True)
        folder = folder_result["path"] if folder_result["ok"] else defaults["folder"]
    filename = _normalize_filename_input(merged.get("filename"), defaults["filename"])
    proxy = _build_proxy_config(merged)

    list_mode_text_map = {
        0: "下载未完成",
        1: "下载全部",
        2: "重新监测URL",
    }

    normalized = {
        "folder": folder,
        "filename": filename,
        "fileExt": file_ext,
        "fileExtText": FILE_EXT_OPTIONS[file_ext],
        "recursionEnabled": recursion_enabled,
        "recursionDepth": recursion_depth,
        "monitorTryEnabled": monitor_try_enabled,
        "monitorTries": monitor_tries,
        "monitorInteraction": monitor_interaction,
        "downloadList": True,
        "downloadMode": download_mode,
        "downloadModeText": DOWNLOAD_MODE_OPTIONS[download_mode],
        "stopMode": stop_mode,
        "stopModeText": STOP_MODE_OPTIONS[stop_mode],
        "listMode": list_mode,
        "listModeText": list_mode_text_map[list_mode],
        "proxyEnabled": proxy["enabled"],
        "proxyAddress": proxy["address"],
        "proxyPort": proxy["port"],
        "proxyUser": proxy["username"],
        "proxyPassword": proxy["password"],
        "maxParallel": max_parallel,
        "monitorHeadless": monitor_headless,
        "monitorRulesPath": monitor_rules_path,
    }
    if "URL" in incoming:
        normalized["URL"] = _to_text(incoming.get("URL"), "")
    return normalized


def ensure_normalized_config(config):
    current = config.data
    try:
        _validate_config_payload(current)
        normalized = normalize_config_dict(current)
    except Exception as exc:
        print(f"invalid config, reset to default: {exc}")
        try:
            should_backup = not (isinstance(current, dict) and len(current) == 0)
            broken_path = _backup_broken_config(getattr(config, "filePath", "")) if should_backup else ""
            if broken_path:
                print(f"config backup: {broken_path}")
        except Exception as backup_exc:
            print(f"config backup failed: {backup_exc}")
        normalized = default_config()
        config.data = normalized
        config.write()
        return normalized

    if current != normalized:
        config.data = normalized
        config.write()
    else:
        config.data = normalized
    return normalized


# 输出重定向
class EmittingStream(QObject):
    textWritten = pyqtSignal(str)
    coverLine = pyqtSignal(str)
    rawWritten = pyqtSignal(str)

    def write(self, text):
        self.rawWritten.emit(str(text))
        if "\r" in text:
            text = text.rsplit("\r", 1)[-1]  # 分割文本，获取最后一个\r后的内容
            self.coverLine.emit(str(text))
        else:
            self.textWritten.emit(str(text))
        QApplication.processEvents()  # 立即刷新UI

    def flush(self):
        pass


class Worker(QThread):
    monitorProgressChanged = pyqtSignal(int)
    downloadProgressChanged = pyqtSignal(int)
    generalProgressChanged = pyqtSignal(int)
    logFileReady = pyqtSignal(str)
    runCompleted = pyqtSignal(bool)

    def __init__(self, config, monitor=True):
        super().__init__()
        self.URL = config["URL"]
        self.folder = config["folder"]
        self.filename = config["filename"]
        self.fileExtText = config["fileExtText"]
        self.recursionEnabled = config["recursionEnabled"]
        self.recursionDepth = config["recursionDepth"]
        if not self.recursionEnabled:
            self.recursionDepth = 1
        self.downloadList = True
        self.downloadMode = config["downloadMode"]
        self.downloadModeText = config["downloadModeText"]
        self.listMode = config.get("listMode", 0)
        self.listModeText = config.get("listModeText", "下载未完成")
        self.maxParallel = _to_int(config.get("maxParallel", 100), 100, 1, 999)
        self.proxyConfig = _build_proxy_config(
            {
                "proxyEnabled": config.get("proxyEnabled", False),
                "proxyAddress": config.get("proxyAddress", DEFAULT_PROXY_ADDRESS),
                "proxyPort": config.get("proxyPort", DEFAULT_PROXY_PORT),
                "proxyUser": config.get("proxyUser", ""),
                "proxyPassword": config.get("proxyPassword", ""),
            }
        )
        if isinstance(config.get("proxyConfig"), dict):
            self.proxyConfig = _build_proxy_config(
                {
                    "proxyEnabled": config["proxyConfig"].get("enabled", False),
                    "proxyAddress": config["proxyConfig"].get("address", DEFAULT_PROXY_ADDRESS),
                    "proxyPort": config["proxyConfig"].get("port", DEFAULT_PROXY_PORT),
                    "proxyUser": config["proxyConfig"].get("username", ""),
                    "proxyPassword": config["proxyConfig"].get("password", ""),
                }
            )
        self.monitorConfig = {
            "headless": _to_bool(config.get("monitorHeadless", True), True),
            "rules_path": _to_text(config.get("monitorRulesPath", "")),
            "tries": _to_int(config.get("monitorTries", 2), 2, 1, 5),
            "interaction_enabled": _to_bool(config.get("monitorInteraction", True), True),
        }
        self.monitorTryEnabled = _to_bool(config.get("monitorTryEnabled", True), True)
        if not self.monitorTryEnabled:
            self.monitorConfig["tries"] = 1

        self.monitor = monitor  # 是否进行监测（是否使用加载的列表）
        self._is_interrupted = False  # 退出标志
        self._active_downloader = None
        self._active_downloader_lock = threading.Lock()
        self._run_completed = False
        self.l = []  # 加载下载列表时使用

        if not self.monitor:
            self.downloadMode = 3  # 更新downloadMode为下载所有
            if self.listMode == 0:
                self.l.extend(config.get("uncompleted", []))
            elif self.listMode == 1:
                self.l.extend(config.get("completed", []) + config.get("uncompleted", []))
            elif self.listMode == 2:
                self.monitor = True

    @staticmethod
    def _clamp_percent(value):
        return max(0, min(100, int(value)))

    def _stop_requested(self):
        return bool(self._is_interrupted)

    def _set_active_downloader(self, downloader):
        with self._active_downloader_lock:
            self._active_downloader = downloader

    def _request_active_downloader_stop(self):
        with self._active_downloader_lock:
            downloader = self._active_downloader
        if downloader is None:
            return
        try:
            downloader.request_stop()
        except Exception:
            pass

    @staticmethod
    def _try_load_latest_config_snapshot(retries=3, retry_interval=0.05):
        config_path = os.path.join(os.getcwd(), "config", "config.json")
        for _ in range(max(1, retries)):
            try:
                with open(config_path, "r", encoding="utf-8") as file:
                    payload = json.load(file)
                if isinstance(payload, dict):
                    return normalize_config_dict(payload)
            except Exception:
                pass
            time.sleep(max(0.0, float(retry_interval)))
        return None

    def _runtime_settings_for_task(self):
        runtime = {
            "recursion_enabled": self.recursionEnabled,
            "recursion_depth": self.recursionDepth,
            "download_mode": self.downloadMode,
            "download_mode_text": self.downloadModeText,
            "max_parallel": self.maxParallel,
            "monitor_try_enabled": self.monitorTryEnabled,
            "proxy_config": dict(self.proxyConfig),
            "monitor_config": dict(self.monitorConfig),
        }
        latest = self._try_load_latest_config_snapshot()
        if latest is None:
            return runtime, False

        recursion_enabled = _to_bool(latest.get("recursionEnabled", runtime["recursion_enabled"]), True)
        recursion_depth = _to_int(latest.get("recursionDepth", runtime["recursion_depth"]), runtime["recursion_depth"], 1, 6)
        if not recursion_enabled:
            recursion_depth = 1
        monitor_try_enabled = _to_bool(latest.get("monitorTryEnabled", runtime["monitor_try_enabled"]), True)
        monitor_config = {
            "headless": _to_bool(latest.get("monitorHeadless", runtime["monitor_config"]["headless"]), True),
            "rules_path": _to_text(latest.get("monitorRulesPath", runtime["monitor_config"].get("rules_path", ""))),
            "tries": _to_int(latest.get("monitorTries", runtime["monitor_config"].get("tries", 2)), 2, 1, 5),
            "interaction_enabled": _to_bool(
                latest.get("monitorInteraction", runtime["monitor_config"].get("interaction_enabled", True)),
                True,
            ),
        }
        if not monitor_try_enabled:
            monitor_config["tries"] = 1

        runtime["recursion_enabled"] = recursion_enabled
        runtime["recursion_depth"] = recursion_depth
        runtime["download_mode"] = _to_int(
            latest.get("downloadMode", runtime["download_mode"]),
            runtime["download_mode"],
            0,
            len(DOWNLOAD_MODE_OPTIONS) - 1,
        )
        runtime["download_mode_text"] = DOWNLOAD_MODE_OPTIONS[runtime["download_mode"]]
        runtime["max_parallel"] = _to_int(latest.get("maxParallel", runtime["max_parallel"]), runtime["max_parallel"], 1, 999)
        runtime["monitor_try_enabled"] = monitor_try_enabled
        runtime["monitor_config"] = monitor_config
        runtime["proxy_config"] = _build_proxy_config(
            {
                "proxyEnabled": latest.get("proxyEnabled", runtime["proxy_config"]["enabled"]),
                "proxyAddress": latest.get("proxyAddress", runtime["proxy_config"]["address"]),
                "proxyPort": latest.get("proxyPort", runtime["proxy_config"]["port"]),
                "proxyUser": latest.get("proxyUser", runtime["proxy_config"]["username"]),
                "proxyPassword": latest.get("proxyPassword", runtime["proxy_config"]["password"]),
            }
        )
        return runtime, True

    def _emit_general_progress(self, task_index, task_total, task_progress):
        if task_total <= 0:
            self.generalProgressChanged.emit(0)
            return
        clamped = max(0.0, min(1.0, float(task_progress)))
        percent = int(round(((task_index - 1) + clamped) * 100 / task_total))
        self.generalProgressChanged.emit(self._clamp_percent(percent))

    @staticmethod
    def _task_output_paths():
        data_dir = os.path.join(os.getcwd(), "Data")
        os.makedirs(data_dir, exist_ok=True)
        base_name = datetime.now().strftime("day-%y.%m.%d;time-%H.%M.%S")
        suffix = 0
        while True:
            if suffix == 0:
                final_name = base_name
            else:
                final_name = f"{base_name}-{suffix}"
            json_path = os.path.join(data_dir, f"{final_name}.json")
            log_path = os.path.join(data_dir, f"{final_name}.log")
            if not os.path.exists(json_path) and not os.path.exists(log_path):
                return json_path, log_path
            suffix += 1

    def run(self):
        self._run_completed = False
        self.monitorProgressChanged.emit(0)
        self.downloadProgressChanged.emit(0)
        self.generalProgressChanged.emit(0)
        try:
            url_input, folder, filename = self.URL, self.folder, self.filename
            file_ext_text = self.fileExtText
            recursion_enabled = self.recursionEnabled
            recursion_depth = self.recursionDepth
            download_list = self.downloadList
            list_mode = self.listMode
            list_mode_text = self.listModeText

            # 开始下载数据
            if url_input == "":
                urls_and_strings = [("", "")]
            else:
                parser = SimpleUrlParser()
                url_template, replacements_data, placeholders = parser.parse_input_string(url_input)
                urls_and_strings = parser.generate_urls_with_match_strings(url_template, replacements_data, placeholders)

            total_tasks = len(urls_and_strings)
            if total_tasks <= 0:
                urls_and_strings = [("", "")]
                total_tasks = 1

            if url_input != "":
                total_targets = len(urls_and_strings)
                print(f"[task] parsed input: targets={total_targets}")
                preview_count = min(5, total_targets)
                for idx, (url, match_str) in enumerate(urls_and_strings[:preview_count], start=1):
                    suffix = f" match={match_str}" if match_str != "" else ""
                    print(f"[task] target {idx}/{total_targets}: {url}{suffix}")
                if total_targets > preview_count:
                    print(f"[task] ... {total_targets - preview_count} more targets")

            def run_url(url, this_filename, task_index, task_total, task_runtime):
                recursion_enabled = task_runtime["recursion_enabled"]
                recursion_depth = task_runtime["recursion_depth"]
                download_mode = task_runtime["download_mode"]
                download_mode_text = task_runtime["download_mode_text"]
                max_parallel = task_runtime["max_parallel"]
                monitor_try_enabled = task_runtime["monitor_try_enabled"]
                proxy_config = task_runtime["proxy_config"]
                monitor_config = task_runtime["monitor_config"]
                current_urls = []
                monitor_session_hints = {}
                json_path, log_path = self._task_output_paths()
                self.logFileReady.emit(log_path)

                task_phase = {
                    "monitor": 0.0,
                    "download": 0.0,
                    "total": 0.0,
                }

                def refresh_task_progress():
                    combined = task_phase["monitor"] * 0.25 + task_phase["download"] * 0.75
                    if combined < task_phase["total"]:
                        combined = task_phase["total"]
                    task_phase["total"] = combined
                    self._emit_general_progress(task_index, task_total, combined)

                def set_monitor_ratio(value):
                    clamped = max(0.0, min(1.0, float(value)))
                    if clamped < task_phase["monitor"]:
                        clamped = task_phase["monitor"]
                    task_phase["monitor"] = clamped
                    refresh_task_progress()

                def set_download_ratio(value):
                    clamped = max(0.0, min(1.0, float(value)))
                    if clamped < task_phase["download"]:
                        clamped = task_phase["download"]
                    task_phase["download"] = clamped
                    refresh_task_progress()

                if url == "" and self.monitor:
                    self.monitorProgressChanged.emit(100)
                    self.downloadProgressChanged.emit(0)
                    set_monitor_ratio(1.0)
                    set_download_ratio(1.0)
                    return

                self.monitorProgressChanged.emit(0)
                self.downloadProgressChanged.emit(0)
                set_monitor_ratio(0.0)
                set_download_ratio(0.0)
                if self._stop_requested():
                    print("[task] interrupted before task start")
                    return

                print("")
                print(f"[task {task_index}/{task_total}] start")
                print(f"[task] url={url}")
                print(f"[task] output={os.path.join(folder, this_filename + file_ext_text)}")
                print(f"[task] json={json_path}")
                print(f"[task] log={log_path}")
                print(
                    f"[task] mode={download_mode_text} recursion={recursion_enabled} "
                    f"depth={recursion_depth} save_list={download_list} parallel={max_parallel}"
                )
                print(
                    f"[task] monitor headless={monitor_config['headless']} "
                    f"interaction={monitor_config.get('interaction_enabled', True)} "
                    f"tries={monitor_config.get('tries', 1)}"
                )
                if monitor_config.get("rules_path", "") != "":
                    print(f"[task] monitor rules={monitor_config['rules_path']}")
                if proxy_config["enabled"]:
                    print(
                        f"[task] proxy=on {proxy_config['address']}:{proxy_config['port']} "
                        f"user={proxy_config['username'] or '(none)'}"
                    )
                else:
                    print("[task] proxy=off")

                if not self.monitor:
                    print(f"[task] list mode={list_mode_text}")
                    sys.stdout.flush()  # 手动刷新缓冲区
                    current_urls = list(self.l)
                    self.monitorProgressChanged.emit(100)
                    set_monitor_ratio(1.0)
                else:
                    if ".m3u8" in url:
                        # 给出m3u8的地址，直接开始下载
                        print("[task] m3u8 url provided; skip monitor")
                        current_urls = [url]
                        self.monitorProgressChanged.emit(100)
                        set_monitor_ratio(1.0)
                        monitor_session_hints = {
                            "source_url": url,
                            "final_url": url,
                            "user_agent": "",
                            "cookies": [],
                            "referer_map": {url: url},
                        }
                    else:
                        # 监测网址获取下载地址
                        monitor_percent = {"value": 0}

                        def monitor_progress(payload):
                            if self._stop_requested():
                                return
                            event = str(payload.get("event", "")).strip()
                            tries = max(
                                1,
                                _to_int(payload.get("tries", monitor_config.get("tries", 1)), 1, 1, 9999),
                            )
                            attempt = _to_int(payload.get("attempt", 1), 1, 1, tries)
                            done = _to_int(payload.get("done", 0), 0, 0, tries)
                            if event == "start":
                                percent = 1
                            elif event == "attempt_start":
                                ratio = ((attempt - 1) + 0.02) / tries
                                percent = int(round(ratio * 100))
                            elif event == "attempt_step":
                                step = _to_int(payload.get("step", 0), 0, 0, 9999)
                                steps = max(1, _to_int(payload.get("steps", 1), 1, 1, 9999))
                                ratio = ((attempt - 1) + (step / steps)) / tries
                                percent = int(round(ratio * 100))
                            elif event == "candidate":
                                percent = monitor_percent["value"] + 1
                            elif event == "attempt_done":
                                ratio = max(0.0, (done / tries) - 0.01)
                                percent = int(round(ratio * 100))
                            elif event == "done":
                                percent = 100
                            else:
                                return
                            percent = self._clamp_percent(percent)
                            if percent < monitor_percent["value"]:
                                percent = monitor_percent["value"]
                            if percent != monitor_percent["value"]:
                                monitor_percent["value"] = percent
                                self.monitorProgressChanged.emit(percent)
                                set_monitor_ratio(percent / 100.0)

                        monitor = MonitorM3U8(
                            url,
                            recursion_enabled=recursion_enabled,
                            recursion_depth=recursion_depth,
                            proxy_config=proxy_config,
                            monitor_config=monitor_config,
                            progress_callback=monitor_progress,
                        )
                        l1, l2 = monitor.simple()
                        if self._stop_requested():
                            print("[task] interrupted during monitor stage")
                            return
                        monitor_session_hints = monitor.get_session_hints()
                        current_urls = l1 + l2
                        self.monitorProgressChanged.emit(100)
                        set_monitor_ratio(1.0)

                # 去重并保持顺序，避免重复下载
                current_urls = list(dict.fromkeys(current_urls))

                # 保存下载列表
                completion_policy = {
                    "maxMissingSegments": 2,
                    "minSuccessRatio": 0.995,
                }
                d_config = {
                    "URL": url,
                    "folder": folder,
                    "filename": this_filename,
                    "fileExtText": file_ext_text,
                    "recursionEnabled": recursion_enabled,
                    "recursionDepth": recursion_depth,
                    "downloadList": download_list,
                    "downloadMode": download_mode,
                    "downloadModeText": download_mode_text,
                    "listMode": list_mode,
                    "listModeText": list_mode_text,
                    "proxyEnabled": proxy_config["enabled"],
                    "proxyAddress": proxy_config["address"],
                    "proxyPort": proxy_config["port"],
                    "proxyUser": proxy_config["username"],
                    "proxyPassword": proxy_config["password"],
                    "maxParallel": max_parallel,
                    "monitorTryEnabled": monitor_try_enabled,
                    "monitorTries": monitor_config.get("tries", 1),
                    "monitorInteraction": monitor_config.get("interaction_enabled", True),
                    "monitorHeadless": monitor_config["headless"],
                    "monitorRulesPath": monitor_config.get("rules_path", ""),
                    "segmentCompletionPolicy": completion_policy,
                }
                d = {"Config": d_config}

                def build_candidate_log_item(candidate_url):
                    return {
                        "url": candidate_url,
                        "completed": False,
                        "completedByTolerance": False,
                        "mergeCompleted": False,
                        "hasMissingSegments": False,
                        "segmentStats": {
                            "total": 0,
                            "downloaded": 0,
                            "failed": 0,
                        },
                        "failedSegments": [],
                        "status": "pending",
                    }

                # 中断检查
                if self._stop_requested():
                    print("[task] interrupted")
                    return

                print(f"[task] detected m3u8 candidates={len(current_urls)}")

                candidate_count = len(current_urls)
                for index, i_url in enumerate(current_urls):
                    d[str(index)] = build_candidate_log_item(i_url)
                DownloadJson(d, filePath=json_path).write()

                if candidate_count <= 0:
                    print("[task] no candidates, mark current target as completed")
                    set_download_ratio(1.0)
                    return

                success_target_map = {0: 0, 1: 1, 2: 5}
                success_target = success_target_map.get(download_mode, None)
                successful_videos = 0
                target_reached = False
                candidate_progress = [0.0] * candidate_count
                progress_lock = threading.Lock()
                download_ratio_state = {"value": 0.0}

                if download_mode == 0:
                    planned_successes = 0
                elif success_target is None:
                    planned_successes = candidate_count
                else:
                    planned_successes = max(1, min(success_target, candidate_count))

                def push_download_ratio(value):
                    clamped = max(0.0, min(1.0, float(value)))
                    with progress_lock:
                        if clamped < download_ratio_state["value"]:
                            clamped = download_ratio_state["value"]
                        if clamped == download_ratio_state["value"]:
                            return
                        download_ratio_state["value"] = clamped
                    set_download_ratio(clamped)

                def update_candidate_progress(index, ratio):
                    if candidate_count <= 0:
                        return
                    with progress_lock:
                        if index < 0 or index >= candidate_count:
                            return
                        clamped = max(0.0, min(1.0, float(ratio)))
                        if clamped < candidate_progress[index]:
                            clamped = candidate_progress[index]
                        candidate_progress[index] = clamped
                        aggregate = sum(candidate_progress) / candidate_count
                    push_download_ratio(aggregate)

                def update_quota_progress(current_ratio):
                    if planned_successes <= 0:
                        push_download_ratio(1.0)
                        return
                    clamped = max(0.0, min(1.0, float(current_ratio)))
                    ratio = (successful_videos + clamped) / planned_successes
                    push_download_ratio(ratio)

                if download_mode == 0:
                    push_download_ratio(1.0)

                # 所有模式都先完成探测，然后进入下载阶段；
                # 首个/前5个按“真实成功视频”数量计数，而不是按候选序号截断。
                processed_index = -1
                for i, i_url in enumerate(current_urls):
                    if self._stop_requested():
                        print("[task] interrupted")
                        DownloadJson(d, filePath=json_path).write()
                        return

                    if success_target is not None and successful_videos >= planned_successes:
                        target_reached = True
                        break

                    completed = False
                    merge_completed = False
                    has_missing_segments = False
                    completed_by_tolerance = False
                    failed_segments_for_log = []
                    total_segments = 0
                    downloaded_segments = 0
                    success_ratio = 0.0
                    missing_ratio = 0.0
                    status = "pending"
                    if download_mode == 0:
                        status = "skipped_no_download"
                        update_candidate_progress(i, 1.0)
                    else:
                        self.downloadProgressChanged.emit(0)
                        candidate_download_percent = {"value": 0}

                        def on_download_progress(payload):
                            if self._stop_requested():
                                return
                            event = str(payload.get("event", "")).strip()
                            total = _to_int(payload.get("total", 0), 0, 0, 10**9)
                            if total <= 0:
                                percent = 0
                            else:
                                done = _to_int(payload.get("done", 0), 0, 0, total)
                                percent = int(round(done * 100 / total))
                            percent = self._clamp_percent(percent)
                            if percent < candidate_download_percent["value"]:
                                percent = candidate_download_percent["value"]
                            if percent != candidate_download_percent["value"]:
                                candidate_download_percent["value"] = percent
                                self.downloadProgressChanged.emit(percent)
                                if success_target is None:
                                    update_candidate_progress(i, percent / 100.0)
                                else:
                                    update_quota_progress(percent / 100.0)

                        print(f"[download] candidate {i + 1}/{len(current_urls)}")
                        try:
                            x = DownloadM3U8(
                                folder,
                                i_url,
                                threadNum=max_parallel,
                                proxy_config=proxy_config,
                                session_hints=monitor_session_hints,
                                progress_callback=on_download_progress,
                                stop_checker=self._stop_requested,
                            )
                        except ValueError as e:
                            if "m3u8 read error" not in str(e):
                                raise e
                            print(f"[warn] skip invalid m3u8: {i_url}")
                            x = None

                        if x is not None:
                            self._set_active_downloader(x)
                            try:
                                x.DonwloadAndWrite()
                            finally:
                                self._set_active_downloader(None)

                            total_segments = len(x.fileNameList)
                            failed_segments_for_log = x.get_failed_segments()
                            failed_segments_count = len(failed_segments_for_log)
                            downloaded_segments = max(0, total_segments - failed_segments_count)
                            has_missing_segments = failed_segments_count > 0
                            if total_segments > 0:
                                success_ratio = downloaded_segments / total_segments
                                missing_ratio = failed_segments_count / total_segments
                            if x.was_interrupted():
                                status = "interrupted_during_segment_download"
                                self._is_interrupted = True
                                total_segments = 0
                                downloaded_segments = 0
                                failed_segments_for_log = []
                                has_missing_segments = False
                                success_ratio = 0.0
                                missing_ratio = 0.0
                                print(
                                    "[task] interrupted during segment download, stop at video boundary"
                                )
                            else:
                                success_segments = downloaded_segments
                                if success_segments <= 0:
                                    status = "no_downloadable_segments"
                                    print("[warn][ffmpeg] skip: no downloadable segments")
                                else:
                                    print(f"[ffmpeg] start generating {file_ext_text}")
                                    ffmpeg_ok = x.process_video_with_ffmpeg(this_filename, file_ext_text)
                                    merge_completed = ffmpeg_ok
                                    if ffmpeg_ok:
                                        if has_missing_segments:
                                            tolerable_missing = (
                                                failed_segments_count <= completion_policy["maxMissingSegments"]
                                                or success_ratio >= completion_policy["minSuccessRatio"]
                                            )
                                            if tolerable_missing:
                                                completed = True
                                                completed_by_tolerance = True
                                                successful_videos += 1
                                                status = "completed_with_tolerated_missing_segments"
                                                print("[task] merged with tolerated missing segments; mark as completed")
                                                print(
                                                    f"[download] tolerated missing segments={failed_segments_count}, "
                                                    f"success_ratio={success_ratio:.4f}, log={json_path}"
                                                )
                                            else:
                                                status = "merged_with_missing_segments"
                                                print(
                                                    "[warn] merged but missing segments exceed tolerance; "
                                                    "keep task as uncompleted"
                                                )
                                                print(
                                                    f"[download] remaining failed segments={failed_segments_count}, "
                                                    f"success_ratio={success_ratio:.4f}, log={json_path}"
                                                )
                                        else:
                                            completed = True
                                            successful_videos += 1
                                            status = "completed"
                                            print(f"[success][ffmpeg] {file_ext_text} completed generating")
                                    else:
                                        status = "ffmpeg_failed"
                                        print("[warn][ffmpeg] failed; continue next candidate")
                        else:
                            status = "invalid_m3u8"
                            if success_target is not None:
                                update_quota_progress(0.0)

                        if not self._stop_requested():
                            if success_target is None:
                                update_candidate_progress(i, 1.0)
                            elif completed:
                                push_download_ratio(successful_videos / planned_successes)

                    if download_list:
                        d[str(i)] = {
                            "url": i_url,
                            "completed": completed,
                            "completedByTolerance": completed_by_tolerance,
                            "mergeCompleted": merge_completed,
                            "hasMissingSegments": has_missing_segments,
                            "segmentStats": {
                                "total": total_segments,
                                "downloaded": downloaded_segments,
                                "failed": len(failed_segments_for_log),
                            },
                            "completionMetrics": {
                                "successRatio": round(success_ratio, 6),
                                "missingRatio": round(missing_ratio, 6),
                            },
                            "failedSegments": failed_segments_for_log,
                            "status": status,
                        }
                    DownloadJson(d, filePath=json_path).write()
                    processed_index = i
                    if self._stop_requested():
                        print("[task] interrupted, mark remaining candidates as skipped")
                        for idx in range(processed_index + 1, candidate_count):
                            key = str(idx)
                            if key in d and isinstance(d[key], dict):
                                d[key]["status"] = "skipped_interrupted"
                        DownloadJson(d, filePath=json_path).write()
                        return

                if target_reached and processed_index + 1 < candidate_count:
                    print(
                        f"[task] success target reached ({successful_videos}/{planned_successes}), "
                        f"skip remaining downloads={candidate_count - (processed_index + 1)}"
                    )
                    for idx in range(processed_index + 1, candidate_count):
                        key = str(idx)
                        if key in d and isinstance(d[key], dict):
                            d[key]["status"] = "skipped_target_reached"
                    DownloadJson(d, filePath=json_path).write()
                    push_download_ratio(1.0)

                if success_target is None:
                    print(f"[task] downloaded success videos={successful_videos} (mode=all)")
                elif download_mode == 0:
                    print("[task] download mode=不下载, skip downloading candidates")
                else:
                    state = "reached" if target_reached or successful_videos >= planned_successes else "not reached"
                    print(
                        f"[task] downloaded success videos={successful_videos}/{planned_successes} "
                        f"target {state}"
                    )

            for task_index, (url, match_str) in enumerate(urls_and_strings, start=1):
                # 地址范围中的每个url 不是一个url中识别到的所有m3u8视频
                task_runtime, used_latest_config = self._runtime_settings_for_task()
                if used_latest_config:
                    print(
                        f"[task {task_index}/{total_tasks}] runtime config refreshed: "
                        f"mode={task_runtime['download_mode_text']} "
                        f"depth={task_runtime['recursion_depth']} "
                        f"parallel={task_runtime['max_parallel']}"
                    )
                else:
                    print(
                        f"[task {task_index}/{total_tasks}] runtime config refresh failed, "
                        "continue with worker snapshot"
                    )
                run_url(
                    url,
                    f"{filename}_{match_str}" if match_str != "" else filename,
                    task_index,
                    total_tasks,
                    task_runtime,
                )
                if self._stop_requested():
                    break

            if not self._stop_requested():
                self._run_completed = True

        except Exception as e:
            print(f"[error][worker] {e}")
        finally:
            self.runCompleted.emit(self._run_completed)

    def interrupt(self):
        """用于外部请求中断该线程"""
        self._is_interrupted = True
        self._request_active_downloader_stop()


class MyConfigWindow(QMainWindow):
    configSaved = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        # 初始化界面
        self.ui = Ui_ConfigWindow()
        self.ui.setupUi(self)
        self.ui.proxyPasswordEdit.setEchoMode(QLineEdit.Password)
        self.ui.headlessCheckBox.setToolTip("开启更快更稳；关闭可观察页面行为，便于排查。")

        # 信号槽
        self._safe_reconnect(self.ui.openFolderButton.clicked, self.on_openFolderButton_clicked)
        self._safe_reconnect(self.ui.resetButton.clicked, self.on_resetButton_clicked)
        self._safe_reconnect(self.ui.confirmButton.clicked, self.on_confirmButton_clicked)
        self._safe_reconnect(self.ui.applyButton.clicked, self.on_applyButton_clicked)
        self._safe_reconnect(self.ui.recursionCheckBox.toggled, self.on_recursionCheckBox_toggled)
        self._safe_reconnect(self.ui.attemptCheckBox.toggled, self.on_attemptCheckBox_toggled)
        self._safe_reconnect(self.ui.proxyCheckBox.toggled, self.on_proxyCheckBox_toggled)
        self._safe_reconnect(self.ui.savePresetButton.clicked, self.on_savePresetButton_clicked)
        self._safe_reconnect(self.ui.loadPresetButton.clicked, self.on_loadPresetButton_clicked)
        self._safe_reconnect(self.ui.folderEdit.textChanged, self.on_folderEdit_textChanged)
        self._safe_reconnect(self.ui.folderEdit.editingFinished, self.on_folderEdit_editingFinished)

        # 数据
        self.Config = ConfigJson()
        ensure_normalized_config(self.Config)
        self.loadConfig()

    @staticmethod
    def _safe_reconnect(signal, slot):
        try:
            signal.disconnect()
        except TypeError:
            pass
        signal.connect(slot)

    def _validate_folder_input(self, create=False, apply_fix=False):
        defaults = default_config()
        result = _resolve_directory_input(self.ui.folderEdit.text(), defaults["folder"], create=create)
        if result["ok"]:
            if apply_fix and self.ui.folderEdit.text().strip() != result["path"]:
                self.ui.folderEdit.setText(result["path"])
            self.ui.folderEdit.setStyleSheet("")
            self.ui.folderEdit.setToolTip(result["path"])
            self.ui.applyButton.setEnabled(True)
            self.ui.confirmButton.setEnabled(True)
            return result

        message = result.get("reason", "directory is invalid")
        self.ui.folderEdit.setStyleSheet("border: 1px solid #d9534f;")
        self.ui.folderEdit.setToolTip(f"无效目录: {message}")
        self.ui.applyButton.setEnabled(False)
        self.ui.confirmButton.setEnabled(False)
        return result

    def on_folderEdit_textChanged(self, _text):
        self._validate_folder_input(create=False, apply_fix=False)

    def on_folderEdit_editingFinished(self):
        self._validate_folder_input(create=False, apply_fix=True)

    def on_recursionCheckBox_toggled(self, checked):
        self.ui.deepSpinBox.setEnabled(checked)

    def on_attemptCheckBox_toggled(self, checked):
        self.ui.attemptSpinBox.setEnabled(checked)

    def on_proxyCheckBox_toggled(self, checked):
        widgets = [
            self.ui.proxyAddressLabel,
            self.ui.proxyPortLabel,
            self.ui.proxyUserLabel,
            self.ui.proxyPasswordLabel,
            self.ui.proxyAddressEdit,
            self.ui.proxyPortEdit,
            self.ui.proxyUserEdit,
            self.ui.proxyPasswordEdit,
            self.ui.colonLabel,
        ]
        for widget in widgets:
            widget.setEnabled(checked)

    @staticmethod
    def _ensure_preset_dir():
        os.makedirs(PRESET_DIR, exist_ok=True)
        return PRESET_DIR

    def _collect_download_preset(self):
        return {
            "recursionEnabled": self.ui.recursionCheckBox.isChecked(),
            "recursionDepth": self.ui.deepSpinBox.value(),
            "monitorTryEnabled": self.ui.attemptCheckBox.isChecked(),
            "monitorTries": self.ui.attemptSpinBox.value(),
            "monitorInteraction": self.ui.interactionCheckBox.isChecked(),
            "monitorHeadless": self.ui.headlessCheckBox.isChecked(),
            "downloadMode": self.ui.downloadModeCombo.currentIndex(),
            "downloadModeText": self.ui.downloadModeCombo.currentText().strip(),
            "maxParallel": self.ui.concurrentSpinBox.value(),
        }

    def _apply_download_preset(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("preset payload must be object")
        current = dict(self.Config.data if isinstance(self.Config.data, dict) else {})
        current.update(payload)
        normalized = normalize_config_dict(current)
        self.ui.recursionCheckBox.setChecked(normalized["recursionEnabled"])
        self.ui.deepSpinBox.setValue(normalized["recursionDepth"])
        self.ui.attemptCheckBox.setChecked(normalized.get("monitorTryEnabled", True))
        self.ui.attemptSpinBox.setValue(normalized.get("monitorTries", 2))
        self.ui.interactionCheckBox.setChecked(normalized.get("monitorInteraction", True))
        self.ui.headlessCheckBox.setChecked(normalized["monitorHeadless"])
        self.ui.downloadModeCombo.setCurrentIndex(normalized["downloadMode"])
        self.ui.concurrentSpinBox.setValue(normalized["maxParallel"])
        self.on_recursionCheckBox_toggled(normalized["recursionEnabled"])
        self.on_attemptCheckBox_toggled(normalized.get("monitorTryEnabled", True))

    def loadConfig(self):
        try:
            config = ensure_normalized_config(self.Config)
            defaults = default_config()
            current_folder = _normalize_path_text(config.get("folder", ""))
            if current_folder == "" or not _is_structurally_valid_path(current_folder):
                valid_folder = defaults["folder"]
            else:
                folder_result = _resolve_directory_input(
                    current_folder,
                    defaults["folder"],
                    create=False,
                    allow_fallback=True,
                )
                valid_folder = folder_result["path"] if folder_result["ok"] else defaults["folder"]
            valid_filename = _normalize_filename_input(config.get("filename", ""), defaults["filename"])
            if valid_folder != config.get("folder") or valid_filename != config.get("filename"):
                repaired = dict(config)
                repaired["folder"] = valid_folder
                repaired["filename"] = valid_filename
                self.Config.data = normalize_config_dict(repaired)
                self.Config.write()
                config = dict(self.Config.data)

            self.ui.folderEdit.setText(config["folder"])
            self.ui.filenameEdit.setText(config["filename"])
            self.ui.fileExtCombo.setCurrentIndex(config["fileExt"])
            self.ui.recursionCheckBox.setChecked(config["recursionEnabled"])
            self.ui.deepSpinBox.setValue(config["recursionDepth"])
            self.ui.attemptCheckBox.setChecked(config.get("monitorTryEnabled", True))
            self.ui.attemptSpinBox.setValue(config.get("monitorTries", 2))
            self.ui.interactionCheckBox.setChecked(config.get("monitorInteraction", True))
            self.ui.downloadModeCombo.setCurrentIndex(config["downloadMode"])
            self.ui.stopModeCombo.setCurrentIndex(config["stopMode"])
            self.ui.proxyCheckBox.setChecked(config["proxyEnabled"])
            self.ui.proxyAddressEdit.setText(config["proxyAddress"])
            self.ui.proxyPortEdit.setText(config["proxyPort"])
            self.ui.proxyUserEdit.setText(config["proxyUser"])
            self.ui.proxyPasswordEdit.setText(config["proxyPassword"])
            self.ui.concurrentSpinBox.setValue(config["maxParallel"])
            self.ui.headlessCheckBox.setChecked(config["monitorHeadless"])
            self.on_recursionCheckBox_toggled(config["recursionEnabled"])
            self.on_attemptCheckBox_toggled(config.get("monitorTryEnabled", True))
            self.on_proxyCheckBox_toggled(config["proxyEnabled"])
            self._validate_folder_input(create=False, apply_fix=False)
        except Exception:
            print("failed to load config: config is set to default!")
            self.on_resetButton_clicked()
            self.on_applyButton_clicked()

    def on_openFolderButton_clicked(self):
        # 打开文件夹选择对话框
        defaults = default_config()
        current = _resolve_directory_input(
            self.ui.folderEdit.text(),
            defaults["folder"],
            create=False,
            allow_fallback=True,
        )
        initial_dir = current["path"] if current["ok"] else defaults["folder"]
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", initial_dir)
        if path:
            self.ui.folderEdit.setText(path)
            self._validate_folder_input(create=False, apply_fix=True)

    def on_resetButton_clicked(self):
        config = default_config()
        self.ui.folderEdit.setText(config["folder"])
        self.ui.filenameEdit.setText(_normalize_filename_input(config["filename"], config["filename"]))
        self.ui.fileExtCombo.setCurrentIndex(config["fileExt"])
        self.ui.recursionCheckBox.setChecked(config["recursionEnabled"])
        self.ui.deepSpinBox.setValue(config["recursionDepth"])
        self.ui.attemptCheckBox.setChecked(config["monitorTryEnabled"])
        self.ui.attemptSpinBox.setValue(config["monitorTries"])
        self.ui.interactionCheckBox.setChecked(config["monitorInteraction"])
        self.ui.downloadModeCombo.setCurrentIndex(config["downloadMode"])
        self.ui.stopModeCombo.setCurrentIndex(config["stopMode"])
        self.ui.proxyCheckBox.setChecked(config["proxyEnabled"])
        self.ui.proxyAddressEdit.setText(config["proxyAddress"])
        self.ui.proxyPortEdit.setText(config["proxyPort"])
        self.ui.proxyUserEdit.setText(config["proxyUser"])
        self.ui.proxyPasswordEdit.setText(config["proxyPassword"])
        self.ui.concurrentSpinBox.setValue(config["maxParallel"])
        self.ui.headlessCheckBox.setChecked(config["monitorHeadless"])
        self.on_recursionCheckBox_toggled(config["recursionEnabled"])
        self.on_attemptCheckBox_toggled(config["monitorTryEnabled"])
        self.on_proxyCheckBox_toggled(config["proxyEnabled"])
        self._validate_folder_input(create=False, apply_fix=False)

    def on_applyButton_clicked(self):
        folder_result = self._validate_folder_input(create=True, apply_fix=True)
        if not folder_result["ok"]:
            print(f"invalid folder path: {folder_result.get('reason', 'unknown error')}")
            return False

        updated = dict(self.Config.data if isinstance(self.Config.data, dict) else {})
        updated["folder"] = folder_result["path"]
        updated["filename"] = _normalize_filename_input(self.ui.filenameEdit.text(), default_config()["filename"])
        updated["fileExt"] = self.ui.fileExtCombo.currentIndex()
        updated["fileExtText"] = self.ui.fileExtCombo.currentText().strip()
        updated["recursionEnabled"] = self.ui.recursionCheckBox.isChecked()
        updated["recursionDepth"] = self.ui.deepSpinBox.value()
        updated["monitorTryEnabled"] = self.ui.attemptCheckBox.isChecked()
        updated["monitorTries"] = self.ui.attemptSpinBox.value()
        updated["monitorInteraction"] = self.ui.interactionCheckBox.isChecked()
        updated["downloadList"] = True
        updated["downloadMode"] = self.ui.downloadModeCombo.currentIndex()
        updated["downloadModeText"] = self.ui.downloadModeCombo.currentText().strip()
        updated["stopMode"] = self.ui.stopModeCombo.currentIndex()
        updated["stopModeText"] = self.ui.stopModeCombo.currentText().strip()
        updated["proxyEnabled"] = self.ui.proxyCheckBox.isChecked()
        updated["proxyAddress"] = self.ui.proxyAddressEdit.text().strip()
        updated["proxyPort"] = self.ui.proxyPortEdit.text().strip()
        updated["proxyUser"] = self.ui.proxyUserEdit.text().strip()
        updated["proxyPassword"] = self.ui.proxyPasswordEdit.text().strip()
        updated["maxParallel"] = self.ui.concurrentSpinBox.value()
        updated["monitorHeadless"] = self.ui.headlessCheckBox.isChecked()

        self.ui.folderEdit.setText(updated["folder"])
        self.ui.filenameEdit.setText(updated["filename"])
        self.Config.data = normalize_config_dict(updated)
        self.Config.write()
        self.loadConfig()
        self.configSaved.emit(dict(self.Config.data))
        return True

    def on_savePresetButton_clicked(self):
        preset_dir = self._ensure_preset_dir()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        default_name = f"{timestamp}.json"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存下载预设",
            os.path.join(preset_dir, default_name),
            "JSON Files (*.json)",
        )
        file_path = file_path.strip()
        if file_path == "":
            return
        payload = self._collect_download_preset()
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)
            print(f"preset saved: {file_path}")
        except Exception as exc:
            print(f"preset save failed: {exc}")

    def on_loadPresetButton_clicked(self):
        preset_dir = self._ensure_preset_dir()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "加载下载预设",
            preset_dir,
            "JSON Files (*.json)",
        )
        file_path = file_path.strip()
        if file_path == "":
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict) and isinstance(payload.get("Config"), dict):
                payload = payload["Config"]
            self._apply_download_preset(payload)
            print(f"preset loaded: {file_path}")
        except Exception as exc:
            print(f"preset load failed: {exc}")

    def on_confirmButton_clicked(self):
        if self.on_applyButton_clicked():
            self.close()  # 退出配置窗口


class MyWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 初始化界面
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # 图标
        self.setWindowIcon(QIcon("src/downloader.ico"))
        # stdout 回调可能在配置初始化期间触发，先准备日志字段
        self._active_log_path = ""
        self._log_file_handle = None
        self._ui_line_buffer = ""

        # 重定向
        sys.stdout = EmittingStream()
        sys.stdout.textWritten.connect(self.printInTextBrowser)
        sys.stdout.coverLine.connect(self.printInSameLine)
        sys.stdout.rawWritten.connect(self.on_stdout_raw_written)

        # 避免第一次启动时读取到不完整配置
        initial_config = ensure_normalized_config(ConfigJson())
        self._default_folder = initial_config["folder"]
        self._default_filename = initial_config["filename"]

        # 配置窗口
        self.configWindow = MyConfigWindow()  # 对config文件进行检查，避免后续读取错误
        self.configWindow.close()  # 关闭窗口，避免内存泄露
        self.configWindow = None
        self.worker = None
        self._last_worker_completed = False
        self._worker_stopped_by_user = False
        self._filename_placeholder_prefix = self.ui.filenameEdit.placeholderText()
        self._folder_placeholder_prefix = self.ui.folderEdit.placeholderText()
        self._filename_tooltip_prefix = self.ui.filenameEdit.toolTip()
        self._folder_tooltip_prefix = self.ui.folderEdit.toolTip()

        # 自定义槽函数
        # disconnect 避免默认绑定导致槽函数多次执行
        self.ui.openFileButton.clicked.disconnect()
        self.ui.startButton.clicked.disconnect()
        self.ui.stopButton.clicked.disconnect()
        self.ui.configButton.clicked.disconnect()
        self.ui.clearButton.clicked.disconnect()
        self.ui.clearLogButton.clicked.disconnect()
        self.ui.openFolderButton.clicked.disconnect()
        self.ui.folderEdit.textChanged.disconnect()
        self.ui.filenameEdit.textChanged.disconnect()
        self.ui.openFileButton.clicked.connect(self.on_openFileButton_clicked)
        self.ui.startButton.clicked.connect(self.on_startButton_clicked)
        self.ui.stopButton.clicked.connect(self.on_stopButton_clicked)
        self.ui.configButton.clicked.connect(self.on_configButton_clicked)
        self.ui.clearButton.clicked.connect(self.on_clearButton_clicked)
        self.ui.clearLogButton.clicked.connect(self.on_clearLogButton_clicked)
        self.ui.openFolderButton.clicked.connect(self.on_openFolderButton_clicked)
        self.ui.folderEdit.textChanged.connect(self.on_folderEdit_textChanged)
        self.ui.filenameEdit.textChanged.connect(self.on_filenameEdit_textChanged)

        self.ui.clearButton.setVisible(True)
        self.ui.monitorProgressBar.setValue(0)
        self.ui.downloadProgressBar.setValue(0)
        self.ui.generalProgressBar.setValue(0)
        self._refresh_default_input_hints(initial_config)
        self._refresh_main_input_validation()

    def _close_log_file(self):
        if self._log_file_handle is not None:
            try:
                self._log_file_handle.flush()
                self._log_file_handle.close()
            except Exception:
                pass
        self._log_file_handle = None
        self._active_log_path = ""

    def _set_active_log_file(self, log_path):
        path = str(log_path or "").strip()
        if path == "":
            self._close_log_file()
            return
        if path == self._active_log_path and self._log_file_handle is not None:
            return
        self._close_log_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._log_file_handle = open(path, "a", encoding="utf-8")
        self._active_log_path = path

    def _attach_worker(self, worker):
        self.worker = worker
        self.worker.started.connect(self.on_worker_started)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.monitorProgressChanged.connect(self.ui.monitorProgressBar.setValue)
        self.worker.downloadProgressChanged.connect(self.ui.downloadProgressBar.setValue)
        self.worker.generalProgressChanged.connect(self.ui.generalProgressBar.setValue)
        self.worker.logFileReady.connect(self.on_worker_log_file_ready)
        self.worker.runCompleted.connect(self.on_worker_run_completed)
        self.worker.start()  # 线程开始

    @staticmethod
    def _append_hint(prefix, value):
        base = _to_text(prefix, "")
        suffix = _to_text(value, "")
        if suffix == "":
            return base
        if base == "":
            return suffix
        if base.endswith(("：", ":", " ", "\t", "\n")):
            return f"{base}{suffix}"
        return f"{base} {suffix}"

    def _refresh_default_input_hints(self, config=None):
        effective = config if isinstance(config, dict) else ensure_normalized_config(ConfigJson())
        defaults = default_config()
        folder_result = _resolve_directory_input(
            effective.get("folder", defaults["folder"]),
            defaults["folder"],
            create=False,
            allow_fallback=True,
        )
        self._default_folder = folder_result["path"] if folder_result["ok"] else defaults["folder"]
        self._default_filename = _normalize_filename_input(
            effective.get("filename", defaults["filename"]),
            defaults["filename"],
        )
        self.ui.filenameEdit.setPlaceholderText(
            self._append_hint(self._filename_placeholder_prefix, self._default_filename)
        )
        self.ui.folderEdit.setPlaceholderText(
            self._append_hint(self._folder_placeholder_prefix, self._default_folder)
        )
        self.ui.filenameEdit.setToolTip(
            self._append_hint(self._filename_tooltip_prefix, self._default_filename)
        )
        self.ui.folderEdit.setToolTip(self._append_hint(self._folder_tooltip_prefix, self._default_folder))

    def _refresh_main_input_validation(self):
        result = _resolve_directory_input(self.ui.folderEdit.text(), self._default_folder, create=False)
        if result["ok"]:
            self.ui.folderEdit.setStyleSheet("")
        else:
            self.ui.folderEdit.setStyleSheet("border: 1px solid #d9534f;")

        can_start = result["ok"] and not (self.worker is not None and self.worker.isRunning())
        self.ui.startButton.setEnabled(can_start)

    def on_folderEdit_textChanged(self, _text):
        self._refresh_main_input_validation()

    def on_filenameEdit_textChanged(self, _text):
        self._refresh_main_input_validation()

    def on_openFolderButton_clicked(self):
        current = _resolve_directory_input(
            self.ui.folderEdit.text(),
            self._default_folder,
            create=False,
            allow_fallback=True,
        )
        initial_dir = current["path"] if current["ok"] else self._default_folder
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", initial_dir)
        if path:
            self.ui.folderEdit.setText(path)
            self._refresh_main_input_validation()

    @staticmethod
    def _is_completed_line(line_text):
        text = line_text.strip()
        return (
            text.startswith("completed\t")
            or text.startswith("[segment][completed]")
        )

    def _append_to_text_browser(self, text):
        if text == "":
            return
        scroll_bar = self.ui.textBrowser.verticalScrollBar()
        auto_scroll = scroll_bar.value() == scroll_bar.maximum()
        self.ui.textBrowser.insertPlainText(text)
        if auto_scroll:
            scroll_bar.setValue(scroll_bar.maximum())

    def on_openFileButton_clicked(self):
        jsonDir = os.path.join(os.getcwd(), "Data")
        if not os.path.exists(jsonDir):
            print(f"making dir: {jsonDir}")
            os.makedirs(jsonDir)
        jsonPath, _ = QFileDialog.getOpenFileName(self, "选择json文件", jsonDir, "json Files (*.json);;Text Files (*.txt)")
        jsonPath = jsonPath.strip()
        if jsonPath == "":
            print("don not select a json file")
            return
        print(f"open json file: {jsonPath}")

        try:
            config = ReadDownloadJson(jsonPath)
        except ValueError as e:
            if "read error" in str(e):
                print(f"please check your file: {jsonPath}")
                return
            print(e)
            raise e

        print("[config] loaded from file:")
        for item in config.data.items():
            print(f"{item[0]}:{item[1]}")

        try:
            run_config = config.data.get("Config", {})
            run_config = normalize_config_dict(run_config)
            folder_result = _resolve_directory_input(run_config.get("folder", ""), self._default_folder, create=True)
            if not folder_result["ok"]:
                print(f"invalid folder path in file config: {folder_result.get('reason', 'unknown error')}")
                return
            run_config["folder"] = folder_result["path"]
            run_config["filename"] = _normalize_filename_input(run_config.get("filename", ""), self._default_filename)

            # 进行下载时真实使用的设置，而非Config中的配置
            passing_dict = {
                "URL": _to_text(run_config.get("URL"), ""),
                "folder": run_config["folder"],
                "filename": run_config["filename"],
                "fileExtText": run_config["fileExtText"],
                "recursionEnabled": run_config["recursionEnabled"],
                "recursionDepth": run_config["recursionDepth"],
                "downloadList": True,
                "downloadMode": run_config["downloadMode"],
                "downloadModeText": run_config["downloadModeText"],
                "listMode": run_config["listMode"],
                "listModeText": run_config["listModeText"],
                "proxyEnabled": run_config["proxyEnabled"],
                "proxyAddress": run_config["proxyAddress"],
                "proxyPort": run_config["proxyPort"],
                "proxyUser": run_config["proxyUser"],
                "proxyPassword": run_config["proxyPassword"],
                "maxParallel": run_config["maxParallel"],
                "monitorTryEnabled": run_config.get("monitorTryEnabled", True),
                "monitorTries": run_config.get("monitorTries", 2),
                "monitorInteraction": run_config.get("monitorInteraction", True),
                "monitorHeadless": run_config["monitorHeadless"],
                "monitorRulesPath": run_config.get("monitorRulesPath", ""),
                "completed": config.completed,
                "uncompleted": config.uncompleted,
            }

            self._attach_worker(Worker(passing_dict, False))
        except Exception as e:
            print(f"\nerror: passing_dict/worker: {e}")

    def on_startButton_clicked(self):
        config = ensure_normalized_config(ConfigJson())
        self._refresh_default_input_hints(config)

        url = self.ui.urlEdit.text().strip()  # 读取 QLineEdit 的内容
        folder_result = _resolve_directory_input(self.ui.folderEdit.text(), self._default_folder, create=True)
        if not folder_result["ok"]:
            print(f"invalid folder path: {folder_result.get('reason', 'unknown error')}")
            self._refresh_main_input_validation()
            return
        folder = folder_result["path"]
        filename = _normalize_filename_input(self.ui.filenameEdit.text(), self._default_filename)
        if self.ui.folderEdit.text().strip() != folder:
            self.ui.folderEdit.setText(folder)
        if self.ui.filenameEdit.text().strip() != filename:
            self.ui.filenameEdit.setText(filename)

        # 进行下载时真实使用的设置，而非Config中的配置
        passing_dict = {
            "URL": url,
            "folder": folder,
            "filename": filename,
            "fileExtText": config["fileExtText"],
            "recursionEnabled": config["recursionEnabled"],
            "recursionDepth": config["recursionDepth"],
            "downloadList": True,
            "downloadMode": config["downloadMode"],
            "downloadModeText": config["downloadModeText"],
            "listMode": config["listMode"],
            "listModeText": config["listModeText"],
            "proxyEnabled": config["proxyEnabled"],
            "proxyAddress": config["proxyAddress"],
            "proxyPort": config["proxyPort"],
            "proxyUser": config["proxyUser"],
            "proxyPassword": config["proxyPassword"],
            "maxParallel": config["maxParallel"],
            "monitorTryEnabled": config.get("monitorTryEnabled", True),
            "monitorTries": config.get("monitorTries", 2),
            "monitorInteraction": config.get("monitorInteraction", True),
            "monitorHeadless": config["monitorHeadless"],
            "monitorRulesPath": config.get("monitorRulesPath", ""),
        }

        self._attach_worker(Worker(passing_dict))

    def on_worker_started(self):
        # 控件状态
        self._last_worker_completed = False
        self._worker_stopped_by_user = False
        self.ui.clearButton.setVisible(False)
        self.ui.startButton.setEnabled(False)  # 设置为不可用状态
        self.ui.openFileButton.setEnabled(False)
        self.ui.urlEdit.setReadOnly(True)  # 只读模式
        self.ui.filenameEdit.setReadOnly(True)  # 只读模式
        self.ui.folderEdit.setReadOnly(True)
        self.ui.openFolderButton.setEnabled(False)
        self.ui.monitorProgressBar.setValue(0)
        self.ui.downloadProgressBar.setValue(0)
        self.ui.generalProgressBar.setValue(0)

    def on_worker_finished(self):
        # 控件状态
        self.ui.openFileButton.setEnabled(True)
        self.ui.urlEdit.setReadOnly(False)
        self.ui.filenameEdit.setReadOnly(False)
        self.ui.folderEdit.setReadOnly(False)
        self.ui.openFolderButton.setEnabled(True)
        self._close_log_file()
        self.ui.clearButton.setVisible(True)
        # 释放 worker 引用，方便下次启动新任务
        self.worker = None
        self._refresh_main_input_validation()

    def on_stopButton_clicked(self):
        stop_mode = ensure_normalized_config(ConfigJson())["stopMode"]
        if stop_mode == 1:  # 强制重启
            # 启动一个新的程序实例
            QProcess.startDetached(sys.executable, sys.argv)
            # 强制终止进程
            os._exit(1)
        if stop_mode == 2:  # 强制退出
            os._exit(1)

        if self.worker is not None and self.worker.isRunning():
            self._worker_stopped_by_user = True
            self.worker.interrupt()  # 请求线程安全中断
            print("\n...is interrupting")
        else:
            print("no running task now")

    def on_configButton_clicked(self):
        # 直接重新创建一个新的窗口
        self.configWindow = MyConfigWindow()
        self.configWindow.configSaved.connect(self.on_config_saved)
        self.configWindow.show()

    def on_config_saved(self, payload):
        self._refresh_default_input_hints(payload if isinstance(payload, dict) else None)
        self._refresh_main_input_validation()

    def on_clearButton_clicked(self):
        self.ui.urlEdit.clear()
        self.ui.filenameEdit.clear()
        self.ui.folderEdit.clear()
        self.ui.textBrowser.clear()
        self._ui_line_buffer = ""
        self.ui.monitorProgressBar.setValue(0)
        self.ui.downloadProgressBar.setValue(0)
        self.ui.generalProgressBar.setValue(0)
        self.ui.clearButton.setVisible(True)
        self._refresh_main_input_validation()

    def on_clearLogButton_clicked(self):
        self.ui.textBrowser.clear()
        self._ui_line_buffer = ""

    def on_worker_log_file_ready(self, path):
        self._set_active_log_file(path)

    def on_worker_run_completed(self, completed):
        self._last_worker_completed = bool(completed)

    def on_stdout_raw_written(self, text):
        log_file_handle = getattr(self, "_log_file_handle", None)
        if log_file_handle is None:
            return
        try:
            log_file_handle.write(text)
            log_file_handle.flush()
        except Exception:
            pass

    def printInTextBrowser(self, text):
        if text == "":
            return
        if not self.ui.cleanLogCheckBox.isChecked():
            self._append_to_text_browser(text)
            return

        self._ui_line_buffer += text
        lines = self._ui_line_buffer.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._ui_line_buffer = lines.pop()
        else:
            self._ui_line_buffer = ""

        filtered = []
        for line in lines:
            if not self._is_completed_line(line):
                filtered.append(line)
        if filtered:
            self._append_to_text_browser("".join(filtered))

    def printInSameLine(self, text):
        if self.ui.cleanLogCheckBox.isChecked() and self._is_completed_line(text):
            return
        cursor = self.ui.textBrowser.textCursor()
        cursor.movePosition(QTextCursor.End)  # 移动光标到末尾
        cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)  # 移动到当前行首
        cursor.removeSelectedText()  # 删除整行
        cursor.insertText(text)


# if __name__ == "__main__":
#     QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)  # 启用高 DPI 缩放
#     app = QApplication(sys.argv)
#     mainWindow = MyWindow() # 会重定向输出到textBrowser中
#     mainWindow.show()
#     sys.exit(app.exec_())
