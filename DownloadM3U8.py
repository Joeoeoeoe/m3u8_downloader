from concurrent.futures import ThreadPoolExecutor
import time
import m3u8
import os
import shutil
import requests
from urllib3 import Retry

from TimerTimer import TimerTimer
from RandomHeaders import RandomHeaders

class DownloadM3U8:
    def __init__(self, folder, URL, threadNum=100):
        # 文件夹
        self.fileDir = folder
        self.tempDir = os.path.join(self.fileDir, '.TEMP')
        self.prepareFolder()

        # 下载列表
        self.URL = URL.strip()
        self.playlist = None  # 存m3u8.load()返回的playlist
        # 两个列表，实现文件名和请求地址的一一对应
        self.fileNameList = []
        self.fileUrlList = []
        self.prepareDownload()  # 对index.m3u8初步解析，填充上面两个列表，不做任何下载

        # 超时和异常
        self.connections = 0  # 总的请求次数，用于输出
        self.timeout = 4  # 初始超时时间设置为4秒
        self.maxTimeout = 25  # 最大超时时间为25秒
        self.threshold = 0.3  # 最大忍受的失败比例
        self.interval = 10  # 定时器间隔
        self.timeoutTimer = TimerTimer(self.interval, self.TimeoutAdapting)  # 初始化定时器，动态调整超时时间
        self.failedNameList = []
        self.failedUrlList = []
        self.threadNum = threadNum


    @staticmethod
    def clearFolder(folder_path):
        """
        确保指定路径存在，并处理已存在文件夹的内容：
        - 如果 .residual 文件夹已经存在，则清空它后再使用。
        - 如果文件夹存在且非空，则将内容移动到 .residual 文件夹。
        """
        # 确保路径为绝对路径
        folder_path = os.path.abspath(folder_path)
        residual_path = os.path.join(folder_path, ".residual")
        print(folder_path)
        print(residual_path)

        # 如果 .residual 文件夹存在，则清空
        if os.path.exists(residual_path):
            shutil.rmtree(residual_path)  # 删除 .residual 文件夹及其内容

        # 如果目标文件夹存在且非空
        if os.path.exists(folder_path):
            # 创建 .residual 文件夹
            os.mkdir(residual_path)
            if os.listdir(folder_path):  # 判断文件夹是否为空
                # 挪动文件夹中的所有内容到 .residual
                for item in os.listdir(folder_path):
                    item_path = os.path.join(folder_path, item)
                    shutil.move(item_path, residual_path)  # 挪动文件/子文件夹
        else:
            # 如果文件夹不存在，则创建
            os.makedirs(folder_path)

    def prepareFolder(self):
        if not os.path.exists(self.fileDir):
            os.mkdir(self.fileDir)
        if not os.path.exists(self.tempDir):
            os.mkdir(self.tempDir)
        DownloadM3U8.clearFolder(self.tempDir)


    def printInfo(self, stage, filename, url=None, time=None):
        # stage: 阶段 getting got downloading downloaded completed
        if stage == 'completed':
            if time is None:
                print(f"{stage}\t{self.connections}\t****{filename}****", end='')
            else:
                print(f"{stage}\t{self.connections}\ttime={time}\t****{filename}****", end='')
        else:
            print(f"{stage}\t\t****{filename}****", end='')
        print(f'\t:{url}') if url else print()

    def printM3U8(self):
        if self.playlist:
            print(self.playlist.dumps())



    def prepareDownload(self):
        self.printInfo('getting', 'index.m3u8', self.URL)
        # playlist = m3u8.load(self.URL)
        playlist = m3u8.loads(requests.get(self.URL, headers=RandomHeaders()[0]))
        self.printInfo('got', 'index.m3u8', self.URL)
        self.playlist = playlist

        # 获取解密文件名和地址
        # 理论上没有解密文件的话 playlist.keys = [None]
        if playlist.keys and playlist.keys[0]:
            for i, key in enumerate(playlist.keys):
                self.fileUrlList.append(key.absolute_uri)
                key.uri = f'key{i}.enc'  # 强制更改名称，避免index.m3u8中出现预料之外的网址
                self.fileNameList.append(key.uri)

        # 获取ts文件名和地址
        for i, segment in enumerate(playlist.segments):
            self.fileUrlList.append(segment.absolute_uri)
            segment.uri = f'{i}.ts'
            self.fileNameList.append(segment.uri)

    def __downloadSingle(self, fileName, fileUrl):
        try:
            # 独立创建 session，确保线程安全
            with requests.Session() as session:
                session.headers.update(RandomHeaders()[0])  # 更新请求头
                response = session.get(fileUrl, timeout=(self.timeout, self.timeout))
                response.raise_for_status()  # 检查请求状态码，非 2xx 会抛出异常

                # 保存文件
                with open(os.path.join(self.tempDir, fileName), 'wb') as file:
                    file.write(response.content)

                # 打印
                self.connections = self.connections + 1
                self.printInfo('completed', fileName, fileUrl, response.elapsed.total_seconds())

        except requests.RequestException as e:
            # 捕获网络请求异常并记录
            self.failedNameList.append(fileName)
            self.failedUrlList.append(fileUrl)
            # 打印
            self.connections = self.connections + 1
            self.printInfo(f"failed: {e}", fileName, fileUrl)

    def RetryFailed(self, retries=10):
        # 失败列表最多重试10次
        # 连续5次失败列表不更新则放弃
        if retries <= 0:
            return
        failed_time = 0
        for attempt in range(retries):
            if len(self.failedNameList) == 0:
                break
            print(f'\n\t********attempt: {attempt + 1}/{retries} failed={len(self.failedNameList)}********')
            # 临时存储本次重试开始前的状况
            failedNameList = self.failedNameList.copy()
            failedUrlList = self.failedUrlList.copy()
            self.failedNameList.clear()
            self.failedUrlList.clear()
            if failed_time > 2:
                self.timeout = self.timeout + 3
            with ThreadPoolExecutor(max_workers=self.threadNum) as executor:
                for name, url in zip(failedNameList, failedUrlList):
                    executor.submit(self.__downloadSingle, name, url)
            if len(self.failedNameList) == len(failedNameList):
                failed_time = failed_time + 1
            elif len(self.failedNameList) >= len(failedNameList):
                print("\t????unexpected error: failedList increased????")
            else:
                failed_time = 1
            if failed_time >= 5:
                break

    def WriteM3U8(self):
        self.playlist.segments[:] = [seg for seg in self.playlist.segments if seg.uri not in self.failedNameList]
        with open(os.path.join(self.tempDir, 'index.m3u8'), "w", encoding="utf-8") as f:
            f.write(self.playlist.dumps())  # 使用 playlist.dumps() 获取 m3u8 的内容并写入文件

    def DonwloadAndWrite(self, retries=10):

        # 启动定时器
        self.timeoutTimer.StartTimer()

        # 下载和写List中的文件
        print(f'\n\t********total={len(self.fileNameList)}********')
        with ThreadPoolExecutor(max_workers=100) as executor:
            for name, url in zip(self.fileNameList, self.fileUrlList):
                executor.submit(self.__downloadSingle, name, url)

        # 重新下载和写failedList中的文件
        self.RetryFailed(retries)

        # 结束定时器
        self.timeoutTimer.StopTimer()

        # 写index.m3u8 - 删除无效文件
        self.WriteM3U8()

    def TimeoutAdapting(self):
        if self.connections < 15:
            return
        if self.timeout >= self.maxTimeout:
            print('\t********bad connection********')
            return
        if len(self.failedNameList) / self.connections > self.threshold:
            print(
                f'\t********failed% = {len(self.failedNameList) / self.connections} = {len(self.failedNameList)}/{self.connections} > {self.threshold}')
            print(f'\t********changing timeout from {self.timeout} to {self.timeout + 3}********')
            self.timeout = self.timeout + 3
            time.sleep(10)


    def writeVideoBat(self, fileName='output', extension='.mp4'):
        indexPath = os.path.join(self.tempDir, 'index.m3u8')
        filePath = os.path.join(self.fileDir, f'{fileName}{extension}')
        batPath = os.path.join(self.fileDir, 'combine.bat')
        ffmpegPath = os.path.join(os.getcwd(),'ffmpeg.exe')
        command = f'\"{ffmpegPath}\" -allowed_extensions ALL -i \"{indexPath}\" -c copy \"{filePath}\"'
        with open(batPath, 'w') as file:
            file.write(command)
        print(f'command = {command}')
        if os.path.exists(filePath):
            newPath = os.path.join(self.fileDir, f'origin-{fileName}{extension}')
            os.rename(filePath,newPath)
