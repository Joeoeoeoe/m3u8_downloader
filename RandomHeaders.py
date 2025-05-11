import requests
import random
from fake_useragent import UserAgent

from ConnectionTest import ConnectionTest
class RandomHeaders:
    # 使用组合类，内部类
    class Config:
        ua = UserAgent()  # 初始化 fake_useragent
        ct = ConnectionTest()  # 初始化 连接测试
        refererExample = [
            "https://www.baidu.com",
            "https://kimi.moonshot.cn/?ref=aihub.cn",
            "https://github.com/",
            "https://www.example.com"
        ]

    def __init__(self, num=1, check=False):
        self.headersList = []
        self.refererList = []
        self.check = check
        self.resize(num)

    def __len__(self):
        return len(self.headersList)

    def __getitem__(self, index):
        return self.headersList[index]

    def __setitem__(self, index, value):
        self.headersList[index] = value

    def __delitem__(self, index):
        del self.headersList[index]

    def __str__(self):
        return str(self.headersList)

    @staticmethod
    # 根据 User-Agent 解析出浏览器、版本、平台等信息
    def __ua2sec(user_agent):
        browser, version = "Unknown", "0"
        platform = "Unknown"

        # 判断浏览器类型及版本号
        if "Chrome" in user_agent and "Chromium" not in user_agent:
            browser = "Google Chrome"
            version = user_agent.split("Chrome/")[1].split(" ")[0]
        elif "Chromium" in user_agent:
            browser = "Chromium"
            version = user_agent.split("Chromium/")[1].split(" ")[0]
        elif "Firefox" in user_agent:
            browser = "Firefox"
            version = user_agent.split("Firefox/")[1]
        elif "Safari" in user_agent and "Version/" in user_agent:
            browser = "Safari"
            version = user_agent.split("Version/")[1].split(" ")[0]
        elif "Opera" in user_agent or "OPR" in user_agent:
            browser = "Opera"
            version = (
                user_agent.split("OPR/")[1].split(" ")[0]
                if "OPR/" in user_agent
                else user_agent.split("Opera/")[1].split(" ")[0]
            )
        elif "Edge" in user_agent:
            browser = "Microsoft Edge"
            version = user_agent.split("Edge/")[1].split(" ")[0]
        elif "Trident" in user_agent:  # IE 11
            browser = "Internet Explorer"
            version = user_agent.split("rv:")[1].split(")")[0]

        # 判断平台类型
        if "Windows NT" in user_agent:
            platform = "Windows"
        elif "Macintosh" in user_agent or "Mac OS X" in user_agent:
            platform = "macOS"
        elif "Linux" in user_agent and "Android" not in user_agent:
            platform = "Linux"
        elif "Android" in user_agent:
            platform = "Android"
        elif "iPhone" in user_agent or "iPad" in user_agent:
            platform = "iOS"

        # 构造 sec 字段
        sec_fields = {
            "sec-ch-ua": f'"{browser}";v="{version.split(".")[0]}", "Not_A Brand";v="99"',
            "sec-ch-ua-mobile": "?1" if "Mobile" in user_agent else "?0",
            "sec-ch-ua-platform": f'"{platform}"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin"
        }
        return sec_fields

    @staticmethod
    # 生成完整的随机请求头；只返回一个头部列表，不会更改对象的存储内容
    def GenHeadersList(num=1, rList=Config.refererExample):
        """
        :param num: 默认只生成一个请求头
        :param rList: 传入None或[]则表示请求头不使用referer字段，默认使用示例referer
        """
        retList = []

        for i in range(num):
            user_agent = RandomHeaders.Config.ua.random  # 随机生成 User-Agent

            sec_fields = RandomHeaders.__ua2sec(user_agent)
            referer = random.choice(rList) if rList and isinstance(rList, list) else None

            headers = {
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9",
                "referer": referer,
                "user-agent": user_agent,
                **sec_fields,  # 添加解析生成的 sec 字段
            }
            retList.append(headers)

        return retList

    def __check200AndDelete(self):
        """
        检查连接，并删除无效连接
        :return: True连接正确，False有删除的连接
        """
        rightconnection = True
        for index, headers in enumerate(self.headersList):
            if not self.Config.ct.connectionTest200(headers=headers):
                flag = False  # temporory rightconnection
                retries = 10
                for attempt in range(retries):
                    rL = self.refererList if len(self.refererList) > 0 else RandomHeaders.Config.refererExample
                    headers = RandomHeaders.GenHeadersList(rList=rL)
                    if self.Config.ct.connectionTest200(headers=headers):
                        self.headersList[index] = headers
                        flag = True
                        break
                if not flag:
                    rightconnection = False
                    del self.headersList[index]

        return rightconnection

    def modifyHeadersList(self, clear=False, addList=[]):
        """
        按参数顺序操作headersList
        :param clear: 是否清空headers列表
        :param addList: None或[]则不再改变headersList，否则将列表中的元素逐个加入headersList
        :return: bool True正确加入；False有连接错误，addList无法全部加入headersList
        """
        if clear:
            self.headersList.clear()
        elif isinstance(addList, list):
            self.headersList.extend(addList)

        # 连接测试
        return True if not self.check else self.__check200AndDelete()

    def resize(self, newsize):
        # 增加/删除headers的列表
        # 返回是否正确增加/删除
        # 会检查连接
        if newsize < 0:
            return False
        delta = newsize - len(self.headersList)
        if delta < 0:
            self.headersList = self.headersList[:newsize]
        else:
            rL = self.refererList if len(self.refererList) > 0 else RandomHeaders.Config.refererExample
            self.headersList.extend(RandomHeaders.GenHeadersList(delta, rL))

        # 连接测试
        return True if not self.check else self.__check200AndDelete()

    def regenerate(self):
        # 重新生成长度不变的请求头部
        # 返回是否正确增加/删除
        self.headersList.clear()
        delta = newsize = len(self.refererList)
        if newsize == 0:
            return False

        rL = self.refererList if len(self.refererList) > 0 else RandomHeaders.Config.refererExample
        self.headersList.extend(RandomHeaders.GenHeadersList(delta, rL))

        # 连接测试
        return True if not self.check else self.__check200AndDelete()

