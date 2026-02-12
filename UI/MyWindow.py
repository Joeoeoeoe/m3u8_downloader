# python库
import os
import sys

# ui
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThread, QProcess
from PyQt5.QtGui import QTextCursor, QIcon
from PyQt5.QtWidgets import QApplication, QMainWindow, QFileDialog, QLineEdit

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


def default_config():
    return {
        "folder": os.path.join(os.getcwd(), "m3u8"),
        "filename": "output",
        "fileExt": 0,
        "fileExtText": FILE_EXT_OPTIONS[0],
        "deep": True,
        "depth": 2,
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


def normalize_config_dict(data):
    defaults = default_config()
    incoming = dict(data) if isinstance(data, dict) else {}

    # 兼容旧字段
    if "proxyEnabled" not in incoming:
        incoming["proxyEnabled"] = incoming.get("useProxy", False)
    if "maxParallel" not in incoming:
        incoming["maxParallel"] = incoming.get("concurrentNum", defaults["maxParallel"])

    merged = dict(defaults)
    merged.update(incoming)

    file_ext = _to_int(merged.get("fileExt"), defaults["fileExt"], 0, len(FILE_EXT_OPTIONS) - 1)
    download_mode = _to_int(
        merged.get("downloadMode"), defaults["downloadMode"], 0, len(DOWNLOAD_MODE_OPTIONS) - 1
    )
    stop_mode = _to_int(merged.get("stopMode"), defaults["stopMode"], 0, len(STOP_MODE_OPTIONS) - 1)
    list_mode = _to_int(merged.get("listMode"), defaults["listMode"], 0, 2)
    max_parallel = _to_int(merged.get("maxParallel"), defaults["maxParallel"], 1, 999)
    deep_enabled = _to_bool(merged.get("deep"), defaults["deep"])
    depth = _to_int(merged.get("depth"), defaults["depth"], 1, 3)

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
        "deep": deep_enabled,
        "depth": depth,
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
    }
    if "URL" in incoming:
        normalized["URL"] = _to_text(incoming.get("URL"), "")
    return normalized


def ensure_normalized_config(config):
    current = config.data if isinstance(config.data, dict) else {}
    normalized = normalize_config_dict(current)
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
        self.deep = config["deep"]
        self.depth = config["depth"]
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
            file_ext_text, deep, depth, download_list = self.fileExtText, self.deep, self.depth, self.downloadList
            download_mode, download_mode_text = self.downloadMode, self.downloadModeText
            list_mode = self.listMode
            list_mode_text = self.listModeText
            max_parallel = self.maxParallel
            proxy_config = self.proxyConfig

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
                if url == "" and self.monitor:
                    return

                print("\t\t\t********INPUT********")
                print(f"\t\t****Url={url}****")
                print(f"\t\t****Folder={folder}****")
                print(f"\t\t****Filename={this_filename}****")
                print(f"\t\t****FileExtention={file_ext_text}****")
                print(f"\t\t****?deep finding={deep}****")
                if deep:
                    print(f"\t\t    >> Depth = {depth}")
                print(f"\t\t****?save download list={download_list}")
                print(f"\t\t****Download mode={download_mode_text}****")
                print(f"\t\t****Max parallel={max_parallel}****")
                print(f"\t\t****Proxy={'ON' if proxy_config['enabled'] else 'OFF'}****")
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
                    else:
                        # 监测网址获取下载地址
                        l1, l2 = MonitorM3U8(url, deep, depth, proxy_config).simple()
                        current_urls = l1 + l2

                # 去重并保持顺序，避免重复下载
                current_urls = list(dict.fromkeys(current_urls))

                # 保存下载列表
                d_config = {
                    "URL": url,
                    "folder": folder,
                    "filename": this_filename,
                    "fileExtText": file_ext_text,
                    "deep": deep,
                    "depth": depth,
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
                }
                d = {"Config": d_config}

                # 中断检查
                if self._is_interrupted:
                    print("Interrupted Success!")
                    return

                # 开始遍历：
                for i, i_url in enumerate(current_urls):
                    if self._is_interrupted:
                        print("Interrupted Success!")
                        return

                    if download_mode == 0 or (download_mode == 1 and i > 0) or (download_mode == 2 and i >= 5):
                        if download_list:
                            d[str(i)] = {"url": i_url, "completed": False}
                    else:
                        try:
                            x = DownloadM3U8(folder, i_url, threadNum=max_parallel, proxy_config=proxy_config)
                        except ValueError as e:
                            if "m3u8 read error" in str(e):
                                continue  # 继续剩余识别到的m3u8的下载
                            raise e

                        x.DonwloadAndWrite()

                        # 程序中调用ffmpeg.exe的逻辑（非dll接口）
                        print(f"\n********starting to generate {file_ext_text}********")
                        x.process_video_with_ffmpeg(this_filename, file_ext_text)
                        print(f"\n********{file_ext_text} completed generating********\n\n")

                        if download_list:
                            d[str(i)] = {"url": i_url, "completed": True}

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

        # 信号槽
        self._safe_reconnect(self.ui.openFolderButton.clicked, self.on_openFolderButton_clicked)
        self._safe_reconnect(self.ui.resetButton.clicked, self.on_resetButton_clicked)
        self._safe_reconnect(self.ui.confirmButton.clicked, self.on_confirmButton_clicked)
        self._safe_reconnect(self.ui.applyButton.clicked, self.on_applyButton_clicked)
        self._safe_reconnect(self.ui.deepCheckBox.toggled, self.on_deepCheckBox_toggled)
        self._safe_reconnect(self.ui.proxyCheckBox.toggled, self.on_proxyCheckBox_toggled)

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

    def on_deepCheckBox_toggled(self, checked):
        self.ui.deepSpinBox.setEnabled(checked)

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

    def loadConfig(self):
        try:
            config = ensure_normalized_config(self.Config)
            self.ui.folderEdit.setText(config["folder"])
            self.ui.filenameEdit.setText(config["filename"])
            self.ui.fileExtCombo.setCurrentIndex(config["fileExt"])
            self.ui.deepCheckBox.setChecked(config["deep"])
            self.ui.deepSpinBox.setValue(config["depth"])
            self.ui.downloadModeCombo.setCurrentIndex(config["downloadMode"])
            self.ui.stopModeCombo.setCurrentIndex(config["stopMode"])
            self.ui.proxyCheckBox.setChecked(config["proxyEnabled"])
            self.ui.proxyAddressEdit.setText(config["proxyAddress"])
            self.ui.proxyPortEdit.setText(config["proxyPort"])
            self.ui.proxyUserEdit.setText(config["proxyUser"])
            self.ui.proxyPasswordEdit.setText(config["proxyPassword"])
            self.ui.concurrentSpinBox.setValue(config["maxParallel"])
            self.on_deepCheckBox_toggled(config["deep"])
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
        self.ui.deepCheckBox.setChecked(config["deep"])
        self.ui.deepSpinBox.setValue(config["depth"])
        self.ui.downloadModeCombo.setCurrentIndex(config["downloadMode"])
        self.ui.stopModeCombo.setCurrentIndex(config["stopMode"])
        self.ui.proxyCheckBox.setChecked(config["proxyEnabled"])
        self.ui.proxyAddressEdit.setText(config["proxyAddress"])
        self.ui.proxyPortEdit.setText(config["proxyPort"])
        self.ui.proxyUserEdit.setText(config["proxyUser"])
        self.ui.proxyPasswordEdit.setText(config["proxyPassword"])
        self.ui.concurrentSpinBox.setValue(config["maxParallel"])
        self.on_deepCheckBox_toggled(config["deep"])
        self.on_proxyCheckBox_toggled(config["proxyEnabled"])

    def on_applyButton_clicked(self):
        updated = dict(self.Config.data if isinstance(self.Config.data, dict) else {})
        updated["folder"] = self.ui.folderEdit.text().strip()
        updated["filename"] = self.ui.filenameEdit.text().strip()
        updated["fileExt"] = self.ui.fileExtCombo.currentIndex()
        updated["fileExtText"] = self.ui.fileExtCombo.currentText().strip()
        updated["deep"] = self.ui.deepCheckBox.isChecked()
        updated["depth"] = self.ui.deepSpinBox.value()
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
        self.Config.data = normalize_config_dict(updated)
        self.Config.write()
        self.loadConfig()

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
                "deep": run_config["deep"],
                "depth": run_config["depth"],
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
            "deep": config["deep"],
            "depth": config["depth"],
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
