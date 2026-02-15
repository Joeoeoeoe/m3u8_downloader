# python库
import json
import os
import sys
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


def default_config():
    return {
        "folder": os.path.join(os.getcwd(), "m3u8"),
        "filename": "output",
        "fileExt": 0,
        "fileExtText": FILE_EXT_OPTIONS[0],
        "recursionEnabled": True,
        "recursionDepth": 2,
        "monitorTryEnabled": True,
        "monitorTries": 3,
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

    folder = _to_text(merged.get("folder"), defaults["folder"]) or defaults["folder"]
    filename = _to_text(merged.get("filename"), defaults["filename"]) or defaults["filename"]
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

    def write(self, text):
        if "\r" in text:
            text = text.rsplit("\r", 1)[-1]  # 分割文本，获取最后一个\r后的内容
            self.coverLine.emit(str(text))
        else:
            self.textWritten.emit(str(text))
        QApplication.processEvents()  # 立即刷新UI

    def flush(self):
        pass


class Worker(QThread):
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
            "tries": _to_int(config.get("monitorTries", 3), 3, 1, 5),
            "interaction_enabled": _to_bool(config.get("monitorInteraction", True), True),
        }
        self.monitorTryEnabled = _to_bool(config.get("monitorTryEnabled", True), True)
        if not self.monitorTryEnabled:
            self.monitorConfig["tries"] = 1

        self.monitor = monitor  # 是否进行监测（是否使用加载的列表）
        self._is_interrupted = False  # 退出标志
        self.l = []  # 加载下载列表时使用

        if not self.monitor:
            self.downloadMode = 3  # 更新downloadMode为下载所有
            if self.listMode == 0:
                self.l.extend(config.get("uncompleted", []))
            elif self.listMode == 1:
                self.l.extend(config.get("completed", []) + config.get("uncompleted", []))
            elif self.listMode == 2:
                self.monitor = True

    def run(self):
        try:
            url_input, folder, filename = self.URL, self.folder, self.filename
            file_ext_text = self.fileExtText
            recursion_enabled = self.recursionEnabled
            recursion_depth = self.recursionDepth
            download_list = self.downloadList
            download_mode, download_mode_text = self.downloadMode, self.downloadModeText
            list_mode = self.listMode
            list_mode_text = self.listModeText
            max_parallel = self.maxParallel
            monitor_try_enabled = self.monitorTryEnabled
            proxy_config = self.proxyConfig
            monitor_config = dict(self.monitorConfig)

            # 开始下载数据
            if url_input == "":
                urls_and_strings = [("", "")]
            else:
                parser = SimpleUrlParser()
                url_template, replacements_data, placeholders = parser.parse_input_string(url_input)
                print(f"URL Template: {url_template}")
                print(f"Replacements Data: {replacements_data}")
                print(f"Placeholders: {placeholders}")
                urls_and_strings = parser.generate_urls_with_match_strings(url_template, replacements_data, placeholders)
                print("Generated URLs:")
                for url, match_str in urls_and_strings:
                    print(f"URL: {url}, Match: {match_str}")
                print("-" * 30)

            def run_url(url, this_filename):
                current_urls = []
                monitor_session_hints = {}
                if url == "" and self.monitor:
                    return

                print("\t\t\t********INPUT********")
                print(f"\t\t****Url={url}****")
                print(f"\t\t****Folder={folder}****")
                print(f"\t\t****Filename={this_filename}****")
                print(f"\t\t****FileExtention={file_ext_text}****")
                print(f"\t\t****?recursion enabled={recursion_enabled}****")
                print(f"\t\t    >> recursion depth = {recursion_depth}")
                print(f"\t\t****?save download list={download_list}")
                print(f"\t\t****Download mode={download_mode_text}****")
                print(f"\t\t****Max parallel={max_parallel}****")
                print(f"\t\t****Proxy={'ON' if proxy_config['enabled'] else 'OFF'}****")
                print(f"\t\t****Monitor headless={monitor_config['headless']}****")
                print(f"\t\t****Monitor interaction={monitor_config.get('interaction_enabled', True)}****")
                print(f"\t\t****Monitor tries={monitor_config.get('tries', 1)}****")
                if monitor_config.get("rules_path", "") != "":
                    print(f"\t\t****Monitor rules path={monitor_config['rules_path']}****")
                if proxy_config["enabled"]:
                    print(
                        f"\t\t    >> {proxy_config['address']}:{proxy_config['port']} "
                        f"user={proxy_config['username'] or '(none)'}"
                    )

                if not self.monitor:
                    print(f"\t\t****List mode={list_mode_text}****")
                    sys.stdout.flush()  # 手动刷新缓冲区
                    current_urls = list(self.l)
                else:
                    if ".m3u8" in url:
                        # 给出m3u8的地址，直接开始下载
                        print("\n\t**m3u8 address is provided; directly download**\n")
                        current_urls = [url]
                        monitor_session_hints = {
                            "source_url": url,
                            "final_url": url,
                            "user_agent": "",
                            "cookies": [],
                            "referer_map": {url: url},
                        }
                    else:
                        # 监测网址获取下载地址
                        monitor = MonitorM3U8(
                            url,
                            recursion_enabled=recursion_enabled,
                            recursion_depth=recursion_depth,
                            proxy_config=proxy_config,
                            monitor_config=monitor_config,
                        )
                        l1, l2 = monitor.simple()
                        monitor_session_hints = monitor.get_session_hints()
                        current_urls = l1 + l2

                # 去重并保持顺序，避免重复下载
                current_urls = list(dict.fromkeys(current_urls))

                # 保存下载列表
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
                }
                d = {"Config": d_config}

                # 中断检查
                if self._is_interrupted:
                    print("Interrupted Success!")
                    return

                print(f"\t\t****Detected m3u8 count={len(current_urls)}****")

                success_target_map = {0: 0, 1: 1, 2: 5}
                success_target = success_target_map.get(download_mode, None)
                successful_videos = 0
                target_reached = False

                # 所有模式都先完成探测，然后进入下载阶段；
                # 首个/前5个按“真实成功视频”数量计数，而不是按候选序号截断。
                for i, i_url in enumerate(current_urls):
                    if self._is_interrupted:
                        print("Interrupted Success!")
                        return

                    if success_target is not None and successful_videos >= success_target:
                        target_reached = True
                        if download_list:
                            d[str(i)] = {"url": i_url, "completed": False}
                        continue

                    completed = False
                    if download_mode != 0:
                        try:
                            x = DownloadM3U8(
                                folder,
                                i_url,
                                threadNum=max_parallel,
                                proxy_config=proxy_config,
                                session_hints=monitor_session_hints,
                            )
                        except ValueError as e:
                            if "m3u8 read error" not in str(e):
                                raise e
                            print(f"\n********skip invalid m3u8: {i_url}********\n")
                            x = None

                        if x is not None:
                            x.DonwloadAndWrite()

                            success_segments = len(x.fileNameList) - len(x.failedNameList)
                            if success_segments <= 0:
                                print("\n********skip ffmpeg: no downloadable segments********\n")
                            else:
                                print(f"\n********starting to generate {file_ext_text}********")
                                ffmpeg_ok = x.process_video_with_ffmpeg(this_filename, file_ext_text)
                                if ffmpeg_ok:
                                    completed = True
                                    successful_videos += 1
                                    print(f"\n********{file_ext_text} completed generating********\n\n")
                                else:
                                    print("\n********ffmpeg failed; continue next candidate********\n")

                    if download_list:
                        d[str(i)] = {"url": i_url, "completed": completed}

                if success_target is None:
                    print(f"\t\t****Downloaded success videos={successful_videos} (mode=all)****")
                elif download_mode == 0:
                    print("\t\t****Download mode=不下载, skip downloading candidates****")
                else:
                    state = "reached" if target_reached or successful_videos >= success_target else "not reached"
                    print(
                        f"\t\t****Downloaded success videos={successful_videos}/{success_target} "
                        f"target {state}****"
                    )

                # 保存下载列表
                if len(current_urls) != 0:
                    DownloadJson(d).write()

            for url, match_str in urls_and_strings:  # 地址范围中的每个url 不是一个url中识别到的所有m3u8视频
                run_url(url, f"{filename}_{match_str}" if match_str != "" else filename)

        except Exception as e:
            print(f"Error in Worker! {e}")

    def interrupt(self):
        """用于外部请求中断该线程"""
        self._is_interrupted = True


class MyConfigWindow(QMainWindow):
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

    def on_recursionCheckBox_toggled(self, checked):
        self.ui.deepSpinBox.setEnabled(checked)
        if not checked:
            self.ui.deepSpinBox.setValue(1)

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
        self.ui.attemptSpinBox.setValue(normalized.get("monitorTries", 3))
        self.ui.interactionCheckBox.setChecked(normalized.get("monitorInteraction", True))
        self.ui.headlessCheckBox.setChecked(normalized["monitorHeadless"])
        self.ui.downloadModeCombo.setCurrentIndex(normalized["downloadMode"])
        self.ui.concurrentSpinBox.setValue(normalized["maxParallel"])
        self.on_recursionCheckBox_toggled(normalized["recursionEnabled"])
        self.on_attemptCheckBox_toggled(normalized.get("monitorTryEnabled", True))

    def loadConfig(self):
        try:
            config = ensure_normalized_config(self.Config)
            self.ui.folderEdit.setText(config["folder"])
            self.ui.filenameEdit.setText(config["filename"])
            self.ui.fileExtCombo.setCurrentIndex(config["fileExt"])
            self.ui.recursionCheckBox.setChecked(config["recursionEnabled"])
            self.ui.deepSpinBox.setValue(config["recursionDepth"])
            self.ui.attemptCheckBox.setChecked(config.get("monitorTryEnabled", True))
            self.ui.attemptSpinBox.setValue(config.get("monitorTries", 3))
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
        except Exception:
            print("failed to load config: config is set to default!")
            self.on_resetButton_clicked()
            self.on_applyButton_clicked()

    def on_openFolderButton_clicked(self):
        # 打开文件夹选择对话框
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", self.ui.folderEdit.text())
        if path:
            self.ui.folderEdit.setText(path)

    def on_resetButton_clicked(self):
        config = default_config()
        self.ui.folderEdit.setText(config["folder"])
        self.ui.filenameEdit.setText(config["filename"])
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

    def on_applyButton_clicked(self):
        updated = dict(self.Config.data if isinstance(self.Config.data, dict) else {})
        updated["folder"] = self.ui.folderEdit.text().strip()
        updated["filename"] = self.ui.filenameEdit.text().strip()
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
        self.Config.data = normalize_config_dict(updated)
        self.Config.write()
        self.loadConfig()

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
        self.on_applyButton_clicked()
        self.close()  # 退出配置窗口


class MyWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 初始化界面
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # 图标
        self.setWindowIcon(QIcon("src/downloader.ico"))

        # 重定向
        sys.stdout = EmittingStream()
        sys.stdout.textWritten.connect(self.printInTextBrowser)
        sys.stdout.coverLine.connect(self.printInSameLine)

        # 避免第一次启动时读取到不完整配置
        ensure_normalized_config(ConfigJson())

        # 配置窗口
        self.configWindow = MyConfigWindow()  # 对config文件进行检查，避免后续读取错误
        self.configWindow.close()  # 关闭窗口，避免内存泄露
        self.configWindow = None
        self.worker = None

        # 自定义槽函数
        # disconnect 避免默认绑定导致槽函数多次执行
        self.ui.openFileButton.clicked.disconnect()
        self.ui.startButton.clicked.disconnect()
        self.ui.stopButton.clicked.disconnect()
        self.ui.configButton.clicked.disconnect()
        self.ui.clearButton.clicked.disconnect()
        self.ui.openFileButton.clicked.connect(self.on_openFileButton_clicked)
        self.ui.startButton.clicked.connect(self.on_startButton_clicked)
        self.ui.stopButton.clicked.connect(self.on_stopButton_clicked)
        self.ui.configButton.clicked.connect(self.on_configButton_clicked)
        self.ui.clearButton.clicked.connect(self.on_clearButton_clicked)

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

        print("\n\t****Config=****")
        for item in config.data.items():
            print(f"{item[0]}:{item[1]}")

        try:
            run_config = config.data.get("Config", {})
            run_config = normalize_config_dict(run_config)

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
                "monitorTries": run_config.get("monitorTries", 3),
                "monitorInteraction": run_config.get("monitorInteraction", True),
                "monitorHeadless": run_config["monitorHeadless"],
                "monitorRulesPath": run_config.get("monitorRulesPath", ""),
                "completed": config.completed,
                "uncompleted": config.uncompleted,
            }

            self.worker = Worker(passing_dict, False)
            self.worker.started.connect(self.on_worker_started)
            self.worker.finished.connect(self.on_worker_finished)
            self.worker.start()  # 线程开始
        except Exception as e:
            print(f"\nerror: passing_dict/worker: {e}")

    def on_startButton_clicked(self):
        config = ensure_normalized_config(ConfigJson())

        url = self.ui.urlEdit.text().strip()  # 读取 QLineEdit 的内容
        folder = config["folder"]
        filename = self.ui.filenameEdit.text().strip()
        filename = filename if filename != "" else config["filename"]
        filename = filename.split(".")[0]

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
            "monitorTries": config.get("monitorTries", 3),
            "monitorInteraction": config.get("monitorInteraction", True),
            "monitorHeadless": config["monitorHeadless"],
            "monitorRulesPath": config.get("monitorRulesPath", ""),
        }

        self.worker = Worker(passing_dict)
        self.worker.started.connect(self.on_worker_started)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()  # 线程开始

    def on_worker_started(self):
        # 控件状态
        self.ui.startButton.setEnabled(False)  # 设置为不可用状态
        self.ui.openFileButton.setEnabled(False)
        self.ui.urlEdit.setReadOnly(True)  # 只读模式
        self.ui.filenameEdit.setReadOnly(True)  # 只读模式

    def on_worker_finished(self):
        # 控件状态
        self.ui.startButton.setEnabled(True)
        self.ui.openFileButton.setEnabled(True)
        self.ui.urlEdit.setReadOnly(False)
        self.ui.filenameEdit.setReadOnly(False)
        # 释放 worker 引用，方便下次启动新任务
        self.worker = None

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
            self.worker.interrupt()  # 请求线程安全中断
            print("\n...is interrupting")
        else:
            print("no running task now")

    def on_configButton_clicked(self):
        # 直接重新创建一个新的窗口
        self.configWindow = MyConfigWindow()
        self.configWindow.show()

    def on_clearButton_clicked(self):
        self.ui.textBrowser.clear()

    def printInTextBrowser(self, text):
        # 获取文本浏览器的垂直滚动条
        scroll_bar = self.ui.textBrowser.verticalScrollBar()
        # 判断当前是否处于底部
        auto_scroll = scroll_bar.value() == scroll_bar.maximum()

        # 插入文本
        self.ui.textBrowser.insertPlainText(text)

        # 如果之前在底部，则自动滚动到最新的内容
        if auto_scroll:
            scroll_bar.setValue(scroll_bar.maximum())

    def printInSameLine(self, text):
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
