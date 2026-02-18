import os
import sys
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMainWindow, QFileDialog


def _configure_playwright_browsers_path():
    if not getattr(sys, "frozen", False):
        return
    if str(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")).strip() != "":
        return

    search_paths = []
    app_dir = os.path.dirname(os.path.abspath(sys.executable))
    search_paths.append(os.path.join(app_dir, "ms-playwright"))
    local_appdata = str(os.getenv("LOCALAPPDATA", "")).strip()
    if local_appdata != "":
        search_paths.append(os.path.join(local_appdata, "ms-playwright"))

    for folder in search_paths:
        if os.path.isdir(folder):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = folder
            break


_configure_playwright_browsers_path()
from UI.MyWindow import MyWindow



if __name__ == "__main__":
    try:
        # 某些系统字体驱动会反复输出 DirectWrite 警告，关闭该类噪声日志。
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts.warning=false")
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)  # 启用高 DPI 缩放
        app = QApplication(sys.argv)
        mainWindow = MyWindow() # 会重定向输出到textBrowser中
        mainWindow.show()
        sys.exit(app.exec_())
    except Exception as e:
        print(f'unexpected error!!\n{e}')


