import json
import os
from datetime import datetime

# discarded = {'discarded':True} # 定义一个用于填充，需要丢弃的默认参数

# 传入一个文件地址，一个data
# 传入data正确则用传入data 传入data不正确则用文件下的data
# 即：传入内容优先于文件内容
# 即：创建实例时，只有传了且传对了才会写文件，否则都是读文件（可能是空）
class JsonProcessor:
    def __init__(self, filePath, data=None, cover=True):
        # 文件相关
        self.filePath = filePath # 完整路径
        self.fileDir = os.path.dirname(filePath)
        self.fileName, self.fileExt = os.path.splitext(os.path.basename(filePath))
        # 是否覆盖错误文件
        self.cover = cover
        # 数据相关
        self.data = None
        if data is not None:
            # 有data就write() data错误的write中会进行处理-存空字典
            self.data = data
            self.write()
        else:
            # 无data就read() data错误的read中会进行处理-读空字典
            self.read()

    # json文件读出字典
    def read(self):
        # 'r'模式下 文件不存在会报错
        try:
            with open(self.filePath, 'r', encoding='utf-8') as file:
                self.data = json.load(file)
        except Exception as e:
            # json文件内容格式错误 / json文件路径错误
            print(f'{self.filePath}: json file error: {e}')
            if self.cover:
                if os.path.exists(self.filePath):
                    broken_path = f"{self.filePath}.broken-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                    try:
                        os.replace(self.filePath, broken_path)
                        print(f'json file backup: {broken_path}')
                    except Exception as backup_error:
                        print(f'json file backup failed: {backup_error}')
                self.data = dict()
                self.write()
                print(f'json file is set/reset to NULL')
            else:
                raise ValueError('read error')

    # 字典写入json文件
    def write(self):
        # 创建文件夹
        os.makedirs(self.fileDir, exist_ok=True)
        # 'w'模式下 文件存在会覆盖 文件不存在会新建
        with open(self.filePath, 'w', encoding='utf-8') as file:
            json.dump(self.data, file, ensure_ascii=False, indent=4)

    def __getitem__(self, item):
        if item not in list(self.data.keys()):
            self.data[item] = ''
        return self.data[item]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __delitem__(self, key):
        del self.data[key]

    def __str__(self):
        return str(self.data)

class ConfigJson(JsonProcessor):
    def __init__(self):
        config_dir = os.path.join(os.getcwd(), 'config')
        preset_dir = os.path.join(config_dir, 'preset')
        os.makedirs(preset_dir, exist_ok=True)
        legacy_path = os.path.join(os.getcwd(), 'Config.json')
        if os.path.exists(legacy_path):
            legacy_broken = f"{legacy_path}.broken-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            try:
                os.replace(legacy_path, legacy_broken)
                print(f'legacy config backup: {legacy_broken}')
            except Exception as legacy_error:
                print(f'legacy config backup failed: {legacy_error}')
        filePath = os.path.join(config_dir, 'config.json')
        super().__init__(filePath)

    def clear(self):
        self.data.clear()
        self.write()

# 初始化需要传入一个字典变量
class DownloadJson(JsonProcessor):
    def __init__(self,data, filePath=None):
        if filePath is None:
            fileDir = os.path.join(os.getcwd(), 'Data')
            formatted_time = datetime.now().strftime("day-%y.%m.%d;time-%H.%M.%S")
            filePath = os.path.join(fileDir, f"{formatted_time}.json")
        super().__init__(filePath, data)

class ReadDownloadJson(JsonProcessor):
    def __init__(self, filePath):
        super().__init__(filePath, cover=False) # 若读取报错会在外部进行处理
        self.completed = []
        self.uncompleted = []
        self.paddingList()

    def paddingList(self):
        digitIndex = [key for key in list(self.data.keys()) if key.isdigit()]
        for i in digitIndex:
            item = self.data[i]
            if not isinstance(item, dict):
                continue
            url = str(item.get('url', '')).strip()
            if url == '':
                continue
            completed = bool(item.get('completed', False))
            if completed:
                self.completed.append(url)
            else:
                self.uncompleted.append(url)

    def write(self):
        print("not allowed to use method 'write'")
        raise NotImplementedError("not allowed to use method 'write'")
