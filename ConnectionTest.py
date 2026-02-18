import requests
# 用于连接测试
class ConnectionTest:
    def __init__(self):
        self.testUrl = 'https://www.baidu.com'
        self.testHeaders = None

    def connectionTest200(self, url=None, headers=None, printout=False):
        """
        逐一测试url和headers是否能正常工作
        :param url: 单个字符串网址 或 网址的列表
        :param headers: 单个头部 或 头部的列表
        :param printout: 是否进行输出
        :return: bool True没有连接错误
        """
        if url == None:
            url = self.testUrl
        if headers == None:
            headers = self.testHeaders

        urlList = url if isinstance(url, list) else [url]
        headersList = headers if isinstance(headers, list) else [headers]
        responseList = []
        for u in urlList:
            for h in headersList:
                try:
                    response = requests.get(u, headers=h, timeout=(5, 10))
                    responseList.append([u, response, None])
                except requests.RequestException as exc:
                    responseList.append([u, None, exc])

        if printout:
            for item in responseList:
                print(60 * '*')
                print(f'URL = \t\t{item[0]}')
                if item[1] is None:
                    print(f'error = \t\t{item[2]}')
                else:
                    print(f'status_code = \t{item[1].status_code}')
                    print(f'TEXT = \t\t\"\"\"')
                    print(item[1].text[:300])
                    print('......\"\"\"')
                print(60 * '*' + '\n')

        # 状态码不是200的所有状态码构成一个列表
        list_of_not_200 = [
            item[1].status_code if item[1] is not None else None
            for item in responseList
            if item[1] is None or item[1].status_code != 200
        ]

        # True:都是200，没有连接错误  False:不是都是200
        return len(list_of_not_200) == 0

