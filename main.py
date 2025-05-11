import requests
import re
import os

from RandomHeaders import RandomHeaders
from DownloadM3U8 import DownloadM3U8
from XmlEncoderDecoder import XmlEncoderDecoder
from MonitorM3U8 import MonitorM3U8

import sys
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMainWindow, QFileDialog
from UI.MyWindow import MyWindow



if __name__ == "__main__":
    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)  # 启用高 DPI 缩放
        app = QApplication(sys.argv)
        mainWindow = MyWindow() # 会重定向输出到textBrowser中
        mainWindow.show()
        sys.exit(app.exec_())
    except Exception as e:
        print(f'unexpected error!!\n{e}')


