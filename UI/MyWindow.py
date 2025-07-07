# python库
import sys
import os

# ui
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThread, QProcess
from PyQt5.QtGui import QTextCursor,QIcon
from PyQt5.QtWidgets import QApplication, QMainWindow, QFileDialog
from UI.MainWindow import Ui_MainWindow
from UI.ConfigWindow import Ui_ConfigWindow

# 自定义
from JsonProcessor import ConfigJson, DownloadJson, ReadDownloadJson
from MonitorM3U8 import MonitorM3U8
from DownloadM3U8 import DownloadM3U8
from SimpleUrlParser import SimpleUrlParser


# 输出重定向
class EmittingStream(QObject):
    textWritten = pyqtSignal(str)
    coverLine = pyqtSignal(str)

    def write(self, text):
        if '\r' in text:
            text = text.rsplit('\r', 1)[-1] # 分割文本，获取最后一个\r后的内容

            self.coverLine.emit(str(text))
        else:
            self.textWritten.emit(str(text))
        QApplication.processEvents()  # 立即刷新UI

    def flush(self):
        pass


class Worker(QThread):
    def __init__(self, Config, monitor=True):
        super().__init__()
        self.URL = Config['URL']
        self.folder = Config['folder']
        self.filename = Config['filename']
        self.fileExtText = Config['fileExtText']
        self.deep = Config['deep']
        self.depth = Config['depth']
        self.downloadList = Config['downloadList']
        self.downloadMode = Config['downloadMode']
        self.downloadModeText = Config['downloadModeText']
        self.listMode = Config['listMode']
        self.listModeText = Config['listModeText']

        self.monitor = monitor # 是否进行监测（是否使用加载的列表）
        self._is_interrupted = False  # 退出标志
        self.l = [] # 总的下载列表

        if not self.monitor:
            self.downloadMode = 3  # 更新downloadMode为下载所有
            if self.listMode == 0:
                self.l.extend(Config['uncompleted'])
                self.downloadList = False  # 不再重新保存下载列表
            elif self.listMode == 1:
                self.l.extend(Config['completed'] + Config['uncompleted'])
                self.downloadList = False
            elif self.listMode == 2:
                self.monitor = True
                self.downloadList = True


    def run(self):
        try:
            URL, folder, filename = self.URL, self.folder, self.filename
            fileExtText, deep, depth, downloadList = self.fileExtText, self.deep, self.depth, self.downloadList
            downloadMode, downloadModeText = self.downloadMode, self.downloadModeText
            listMode = self.listMode
            listModeText = self.listModeText

            # 开始下载数据
            parser = SimpleUrlParser()
            url_template, replacements_data, placeholders = parser.parse_input_string(URL)
            print(f"URL Template: {url_template}")
            print(f"Replacements Data: {replacements_data}")
            print(f"Placeholders: {placeholders}")
            urls = parser.generate_urls(url_template, replacements_data, placeholders)
            print("Generated URLs:")
            for url in urls:
                print(url)
            print("-" * 30)

            def run_url(URL):
                if URL == '':
                    return
                else:
                    print(f'\t\t\t********INPUT********')
                    print(f'\t\t****Url={URL}****')
                    print(f'\t\t****Folder={folder}****')
                    print(f'\t\t****Filename={filename}****')
                    print(f'\t\t****FileExtention={fileExtText}****')
                    print(f'\t\t****?deep finding={deep}****')
                    if deep:
                        print(f'\t\t    >> Depth = {depth}')
                    print(f'\t\t****?save download list={downloadList}')
                    print(f'\t\t****Download mode={downloadModeText}****')
                    if not self.monitor:
                        print(f'\t\t****List mode={listModeText}****')
                        sys.stdout.flush()  # 手动刷新缓冲区
                    else:
                        if '.m3u8' in URL:
                            # 给出m3u8的地址，直接开始下载
                            print('\n\t**m3u8 address is provided; directly download**\n')
                            self.l = [URL]
                        else:
                            # 监测网址获取下载地址
                            l1,l2 = MonitorM3U8(URL, deep, depth).simple()
                            self.l.extend(l1 + l2)

                    # 保存下载列表
                    d_Config = \
                        {'URL': URL, 'folder': folder, 'filename':filename,
                         'fileExtText':fileExtText, 'deep':deep,
                         'depth':depth, 'downloadList':downloadList,
                         'downloadMode':downloadMode, 'downloadModeText':downloadModeText,
                         'listMode':listMode, 'listModeText':listModeText}
                    d = {'Config':d_Config}
                    # 中断检查
                    if self._is_interrupted:
                        print('Interrupted Success!')
                        return
                    # 开始遍历：
                    for i, iURL in enumerate(self.l):
                        # 中断检查
                        if self._is_interrupted:
                            print('Interrupted Success!')
                            return
                        if downloadMode == 0 or (downloadMode == 1 and i > 0) or (downloadMode == 2 and i > 5):
                            if downloadList:
                                d[str(i)] = {'url': iURL, 'completed': False}
                        else:
                            try:
                                x = DownloadM3U8(folder, iURL)
                            except ValueError as e:
                                if 'm3u8 read error' in str(e):
                                    break
                                else:
                                    raise e
                            except Exception as e:
                                raise e

                            x.DonwloadAndWrite()
                            x.writeVideoBat(f'{filename}', fileExtText) if i == 0 else x.writeVideoBat(f'{filename}-{i}', fileExtText)
                            print(f'\n********starting to generate {fileExtText}********')
                            os.system(os.path.join(folder, 'combine.bat'))
                            print(f'\n********{fileExtText} completed generating********\n\n')
                            if downloadList:
                                d[str(i)] = {'url': iURL, 'completed': True}
                    # 保存下载列表
                    if len(self.l) != 0:
                        DownloadJson(d).write()

            for x in urls:
                run_url(x)

        except Exception as e:
            print(f'Error in Worker! {e}')

    def interrupt(self):
        """用于外部请求中断该线程"""
        self._is_interrupted = True


class MyConfigWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 初始化界面
        self.ui = Ui_ConfigWindow()
        self.ui.setupUi(self)

        # 信号槽
        self.ui.openFolderButton.clicked.disconnect()
        self.ui.resetButton.clicked.disconnect()
        self.ui.confirmButton.clicked.disconnect()
        self.ui.applyButton.clicked.disconnect()
        self.ui.openFolderButton.clicked.connect(self.on_openFolderButton_clicked)
        self.ui.resetButton.clicked.connect(self.on_resetButton_clicked)
        self.ui.confirmButton.clicked.connect(self.on_confirmButton_clicked)
        self.ui.applyButton.clicked.connect(self.on_applyButton_clicked)

        # 数据
        self.Config = ConfigJson()
        if self.Config.data == {}: # 被重置
            print('failed to load config: config is set to default!')
            self.on_resetButton_clicked()
            self.on_applyButton_clicked()
        self.loadConfig()


    def loadConfig(self):
        try:
            self.ui.folderEdit.setText(self.Config['folder'])
            self.ui.filenameEdit.setText(self.Config['filename'])
            self.ui.fileExtCombo.setCurrentIndex(self.Config['fileExt'])
            self.ui.deepCheck.setChecked(self.Config['deep'])
            self.ui.deepSpin.setValue(self.Config['depth'])
            self.ui.downloadListCheck.setChecked(self.Config['downloadList'])
            self.ui.downloadModeCombo.setCurrentIndex(self.Config['downloadMode'])
            self.ui.stopModeCombo.setCurrentIndex(self.Config['stopMode'])
            self.ui.listModeCombo.setCurrentIndex((self.Config['listMode']))
        except Exception as e:
            print('failed to load config: config is set to default!')
            self.on_resetButton_clicked()
            self.on_applyButton_clicked()

    def on_openFolderButton_clicked(self):
        # 打开文件夹选择对话框
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", self.Config['folder'])
        if path:
            self.ui.folderEdit.setText(path)

    def on_resetButton_clicked(self):
        self.ui.folderEdit.setText(os.path.join(os.getcwd(),'m3u8'))
        self.ui.filenameEdit.setText('output')
        self.ui.fileExtCombo.setCurrentIndex(0)
        self.ui.deepCheck.setChecked(True)
        self.ui.deepSpin.setValue(2)
        self.ui.downloadListCheck.setChecked(True)
        self.ui.downloadModeCombo.setCurrentIndex(1)
        self.ui.stopModeCombo.setCurrentIndex(0)
        self.ui.listModeCombo.setCurrentIndex(0)


    def on_applyButton_clicked(self):
        self.Config['folder'] = self.ui.folderEdit.text()
        self.Config['filename'] = self.ui.filenameEdit.text()
        self.Config['fileExt'] = self.ui.fileExtCombo.currentIndex()
        self.Config['fileExtText'] = self.ui.fileExtCombo.currentText()
        self.Config['deep'] = self.ui.deepCheck.isChecked()
        self.Config['depth'] = self.ui.deepSpin.value()
        self.Config['downloadList'] = self.ui.downloadListCheck.isChecked()
        self.Config['downloadMode'] = self.ui.downloadModeCombo.currentIndex()
        self.Config['downloadModeText'] = self.ui.downloadModeCombo.currentText()
        self.Config['stopMode'] = self.ui.stopModeCombo.currentIndex()
        self.Config['stopModeText'] = self.ui.stopModeCombo.currentText()
        self.Config['listMode'] = self.ui.listModeCombo.currentIndex()
        self.Config['listModeText'] = self.ui.listModeCombo.currentText()
        self.Config.write()


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

        # 配置窗口
        self.configWindow = MyConfigWindow() # 对config文件进行检查，避免后续读取错误
        self.configWindow.close() # 关闭窗口，避免内存泄露
        self.configWindow = None

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
        jsonDir = os.path.join(os.getcwd(), 'Data')
        if not os.path.exists(jsonDir):
            print(f'making dir: {jsonDir}')
            os.makedirs(jsonDir)
        jsonPath, _ = QFileDialog.getOpenFileName(self, "选择json文件", jsonDir, "json Files (*.json);;Text Files (*.txt)")
        jsonPath = jsonPath.strip()
        if jsonPath == '':
            print(f'don not select a json file')
            return
        else:
            print(f'open json file: {jsonPath}')

        try:
            Config = ReadDownloadJson(jsonPath)
        except ValueError as e:
            if 'read error' in e:
                print(f'please check your file: {jsonPath}')
                return
            else:
                print(e)
                raise e

        print('\n\t****Config=****')
        for item in Config.data.items():
            print(f'{item[0]}:{item[1]}')

        try:
            # 进行下载时真实使用的设置，而非Config中的配置
            passing_dict = \
            {'URL':Config['Config']['URL'], 'folder':Config['Config']['folder'], 'filename':Config['Config']['filename'],
             'fileExtText':Config['Config']['fileExtText'], 'deep':Config['Config']['deep'],
             'depth':Config['Config']['depth'], 'downloadList':Config['Config']['downloadList'],
             'downloadMode':Config['Config']['downloadMode'], 'downloadModeText': Config['Config']['downloadModeText'],
             'listMode':Config['Config']['listMode'], 'listModeText':Config['Config']['listModeText'],
             'completed':Config.completed, 'uncompleted':Config.uncompleted}

            self.worker = Worker(passing_dict, False)
            self.worker.started.connect(self.on_worker_started)
            self.worker.finished.connect(self.on_worker_finished)
            self.worker.start() # 线程开始
        except Exception as e:
            print(f'\nerror: passing_dict/worker: {e}')


    def on_startButton_clicked(self):

        # input: URL folder filename
        Config = ConfigJson()
        URL = self.ui.urlEdit.text().strip()  # 读取 QLineEdit 的内容
        folder = Config['folder']
        filename = self.ui.filenameEdit.text().strip()
        filename = filename if filename!='' else Config['filename']
        filename = filename.split('.')[0]
        fileExtText = Config['fileExtText']
        deep, depth = Config['deep'], Config['depth']
        downloadList = Config['downloadList']
        downloadMode = Config['downloadMode']
        downloadModeText = Config['downloadModeText']
        listMode = Config['listMode']
        listModeText = Config['listModeText']

        # 进行下载时真实使用的设置，而非Config中的配置
        passing_dict = {'URL':URL, 'folder':folder, 'filename':filename,
                        'fileExtText':fileExtText, 'deep':deep,
                        'depth':depth, 'downloadList':downloadList,
                        'downloadMode':downloadMode, 'downloadModeText': downloadModeText,
                        'listMode':listMode, 'listModeText':listModeText}


        self.worker = Worker(passing_dict)
        self.worker.started.connect(self.on_worker_started)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start() # 线程开始

    def on_worker_started(self):
        # 控件状态
        self.ui.startButton.setEnabled(False) # 设置为不可用状态
        self.ui.openFileButton.setEnabled(False)
        self.ui.urlEdit.setReadOnly(True) # 只读模式
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
        if ConfigJson()['stopMode'] == 1: # 强制重启
            # 启动一个新的程序实例
            QProcess.startDetached(sys.executable, sys.argv)
            # 退出当前应用程序
            # QApplication.quit()
            # 强制终止进程
            os._exit(1)
        else:
            if hasattr(self, 'worker') and self.worker is not None and self.worker.isRunning():
                # self.worker.interrupt() # 请求线程安全中断
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
        scrollBar = self.ui.textBrowser.verticalScrollBar()
        # 判断当前是否处于底部
        autoScroll = (scrollBar.value() == scrollBar.maximum())

        # 插入文本
        self.ui.textBrowser.insertPlainText(text)

        # 如果之前在底部，则自动滚动到最新的内容
        if autoScroll:
            scrollBar.setValue(scrollBar.maximum())

    def printInSameLine(self, text):
        cursor = self.ui.textBrowser.textCursor()
        cursor.movePosition(QTextCursor.End)  # 移动光标到末尾
        cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor) # 移动到当前行首
        cursor.removeSelectedText() # 删除整行
        cursor.insertText(text)




# if __name__ == "__main__":
#     QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)  # 启用高 DPI 缩放
#     app = QApplication(sys.argv)
#     mainWindow = MyWindow() # 会重定向输出到textBrowser中
#     mainWindow.show()
#     sys.exit(app.exec_())