import re
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import threading


from RandomHeaders import RandomHeaders
from TimerTimer import TimerTimer

class MonitorM3U8:
    def __init__(self, URL, deep=True, depth=2):
        self.timer = TimerTimer(1, self.TimerPrint)
        self.URL = URL
        self.possible = set()
        self.predicted = set()
        self.lock = threading.Lock()
        self.depth = 0 if not deep else depth # 获取深度
        self.depth = self.depth if 0 <= self.depth <= 3 else 0 # 深度范围在[0,3]

    @staticmethod
    def decode(url):
        try:
            # 包含 Unicode 转义序列的 URL
            url = url.rstrip('\\')
            url = url.replace("\\\\", "\\")
            # 使用 unicode_escape 解码 Unicode 转义序列
            decoded_url = url.encode('utf-8').decode('unicode_escape')
            if url != decoded_url:
                return decoded_url # 解码后的url
            else:
                return ''
        except Exception as e:
            return ''



    def handle_response(self, response):
        """处理网络响应"""
        urls = [response.url] if ".m3u8" in response.url else []
        if self.depth in [2,3] and response.status == 200:
            try:
                text = response.text()
                uList = re.findall(r'https?://[^\s\'"<>()]+', text)
                uList.extend([item for item in list(map(MonitorM3U8.decode, uList)) if item != ''])
                urls.extend([url for url in uList if ".m3u8" in url]) # 原始网址

            except Exception as e:
                pass # 其他二进制资源

        for url in urls: # 均含.m3u8
            url = url.rstrip('\\')
            # 提取文件名
            parsed = urlparse(url)
            # 存储
            with self.lock:
                self.possible.add(url)
            if 'index.m3u8' not in url:
                url = re.sub(r'[^\/\.\#]+\.m3u8', 'index.m3u8', url)
                with self.lock:
                    self.predicted.add(url)
            if 'mixed.m3u8' not in url:
                url = re.sub(r'[^\/\.\#]+\.m3u8', 'mixed.m3u8', url)
                with self.lock:
                    self.predicted.add(url)



    def MonitorUrl(self):
        def __monitorSingle():
            browser = sync_playwright().start().chromium.launch(
                headless=True,
                # 启用共享上下文减少资源消耗
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context()
            headers = {k: v for k, v in RandomHeaders()[0].items() if v} # 过滤掉值为空的字段
            context.set_extra_http_headers(headers)
            page = context.new_page()
            page.on("response", self.handle_response)  # 注册回调函数

            # 加载
            # 访问目标页面
            # 等待策略说明：
            # - "domcontentloaded"：HTML解析完成立即触发
            # - "load"：所有资源加载完成（默认）
            # - "networkidle"：500ms内无网络请求（推荐）
            # 分阶段等待
            page.goto(self.URL, wait_until="domcontentloaded")  # 快速加载
            page.wait_for_load_state("networkidle", timeout=10000)  # 追加等待

            # 探测
            if self.depth in [2,3]:
                self._try_trigger_player(page)

            # 关闭
            context.close()

        print(f'\n\t****monitor started****\nURL={self.URL}')

        tries = 3 if self.depth in [0,2] else 5
        with ThreadPoolExecutor(max_workers=3) as executor:
            [executor.submit(__monitorSingle) for _ in range(tries)]

        print('\n\n\t****monitor done****')


        return [list(self.possible), list(self.predicted)]

    def _try_trigger_player(self, page):
        """智能触发视频加载的复合策略"""
        # 第一阶段：基础触发
        page.evaluate("""() => {
            // 通用视频元素处理
            const tryPlayVideo = (video) => {
                try {
                    video.play().catch(e => console.debug('play error:', e));
                    video.dispatchEvent(new Event('canplaythrough', {bubbles: true}));
                    video.dispatchEvent(new Event('loadedmetadata', {bubbles: true}));
                } catch(e) { console.debug('video error:', e) }
            };

            // 1. 现有视频元素处理
            document.querySelectorAll('video, audio').forEach(tryPlayVideo);

            // 2. 常见播放按钮触发（扩展选择器）
            const clickTargets = [
                '[aria-label="播放"]', 
                '.play-button',
                '.vjs-play-control',
                '.jw-icon-playback',
                '.html5-video-player .ytp-play-button'
            ];
            clickTargets.forEach(selector => {
                document.querySelectorAll(selector).forEach(btn => {
                    btn.click();
                    btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                });
            });

            // 3. 触发全屏请求（可能触发高质量流）
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'f', keyCode: 70}));
        }""")

        # 第二阶段：动态元素监听
        page.evaluate("""() => {
            // 4. 建立DOM监听器
            const observer = new MutationObserver(mutations => {
                mutations.forEach(mutation => {
                    mutation.addedNodes.forEach(node => {
                        if (node.tagName === 'VIDEO') {
                            tryPlayVideo(node);
                        } else if (node.querySelector) {
                            node.querySelectorAll('video').forEach(tryPlayVideo);
                        }
                    });
                });
            });

            observer.observe(document, {
                childList: true,
                subtree: true,
                attributes: false,
                characterData: false
            });

            // 10秒后自动停止监听
            setTimeout(() => observer.disconnect(), 10000);
        }""")

        # 第三阶段：模拟用户行为
        # 分阶段滚动（模拟真人浏览）
        for scroll_y in [300, 800, 1500]:
            page.mouse.move(100, 100)  # 移动鼠标
            page.evaluate(f"window.scrollTo(0, {scroll_y})")
            page.wait_for_timeout(1000)  # 每次滚动后等待

        # 模拟键盘操作
        page.keyboard.press("Space")  # 空格键可能触发播放
        page.wait_for_timeout(500)

        # 点击屏幕中央
        page.mouse.click(page.viewport_size['width'] // 2, page.viewport_size['height'] // 2)  # 点击屏幕中心
        page.wait_for_timeout(500)  # 等待一会儿，模拟用户操作的间隔

        # 第四阶段：强制加载隐藏资源
        page.evaluate("""() => {
            // 5. 强制显示和加载隐藏视频
            const style = document.createElement('style');
            style.textContent = 'video { visibility: visible !important; opacity: 1 !important; }';
            document.head.appendChild(style);

            // 6. 修改视频预加载属性
            document.querySelectorAll('video').forEach(video => {
                video.preload = 'auto';
                video.load();
            });
        }""")

        # 确保足够时间捕获请求
        page.wait_for_timeout(3000)


    def TimerPrint(self, cnt):
        print(f'\rwaiting **{cnt}** s for resources to find',end='')

    def simple(self):
        self.timer.StartTimer()
        ret = self.MonitorUrl()
        self.timer.StopTimer()
        possible, predicted = ret
        if ret == [[], []]:
            print('find no resource to download\n\n')
        else:
            [print(f'possible m3u8\t= {i}') for i in list(possible)]
            [print(f'predicted m3u8\t= {i}') for i in list(predicted)]
            print('\n\n')

        if self.depth == 3:  # 深度为3递归一层
            print('\n\n\t\t********recursion started********')
            for iURL in ret[0]+ret[1]:
                retret = MonitorM3U8(iURL).simple()
                possible.extend(retret[0])
                predicted.extend(retret[0])
            print('\n\n\t\t********retcursion done********')
            print(f'\t\t\t>> Depth = {self.depth}\n\t>> All Resources Found:')
            [print(f'possible m3u8\t= {i}') for i in list(possible)]
            [print(f'predicted m3u8\t= {i}') for i in list(predicted)]

        return [possible,predicted]
