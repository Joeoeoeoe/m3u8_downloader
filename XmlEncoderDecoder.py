import os
import lxml
from lxml import etree

# 网站对象
class WebsiteItem:
    def __init__(self,name, url):
        self.name = name
        self.url = url
        self.func = None

    def __repr__(self):
        return f'WebsiteItem({self.name},{self.url},{self.func})'


# 编码器和解码器
class XmlEncoderDecoder:
    def __init__(self, filePath):
        self.filePath = filePath
        self.tree = None
        if not os.path.exists(self.filePath):
            self.tree = etree.ElementTree(etree.Element('M3U8')) # <class 'lxml.etree._ElementTree'>
        else:
            self.tree = etree.parse(self.filePath) # <class 'lxml.etree._ElementTree'>


    def __getitem__(self, item):
        content = self.tree.xpath(item)
        return content[0].text if content is not None and len(content)>=1 else 'No Content'

    def __setitem__(self, key, value):
        self.tree.xpath


    def prepareFile(self):
        self.configPath = os.path.join(self.fileDir, 'config.xml')
        self.webPath = os.path.join(self.fileDir, 'websites.xml')

        if not os.path.exists(self.configPath):
            etree.ElementTree(etree.Element('root'))
        self.configTree = etree.parse(self.configPath)

        if os.path.exists(self.configTree):
            self.webTree = etree.parse(self.webPath)
        else:
            pass

    def parseFile(self):
        pass

    def f(self, website):
        root = etree.Element("root") # 根节点
        webItem = etree.SubElement(_parent=root, _tag="webItem", name=f"{website.name}") # 子节点
        etree.SubElement(webItem)

        book2 = etree.SubElement(root, "book", title="数据结构与算法")
        book2.text = "一本关于数据结构和算法的书籍"

        # 创建 XML 树对象
        tree = etree.ElementTree(root)

    def add(self, web):
        if not isinstance(web, WebsiteItem):
            raise ValueError('not a instance of WebsiteItem')


if __name__=='__main__':
    x = XmlEncoderDecoder('E:\coding\py\M3U8 Downloader\Config.xml')

