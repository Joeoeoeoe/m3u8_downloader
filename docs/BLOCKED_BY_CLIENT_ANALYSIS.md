# ERR_BLOCKED_BY_CLIENT 原因说明与本次修正

> 说明：本文基于一次具体问题排查样本（历史记录）；其中尝试次数等数值不代表当前默认配置，当前行为以源码与 `docs/MONITOR_RULES.md` 为准。

## 1. 你这次日志里发生了什么

输入：

- 页面：`https://www.nan6.com/stream/154593-3-17.html`
- 配置：`depth=2`、`headless=False`、`downloadMode=下载首个`

日志关键信息：

- 监测在当时配置下只跑了首轮（`attempt 1/N`）就结束；
- 找到了两个候选：
  - `https://vip.vipuuvip.com/?url=https://vip.lz-cdn16.com/.../index.m3u8`（解析页包裹地址）
  - `https://vip.lz-cdn16.com/.../index.m3u8`（真实 CDN 地址）
- 下载阶段选中了首个地址（因为“下载首个”），结果 `total=0`，随后 ffmpeg 失败。

结论：

1. 不是“完全没抓到 m3u8”，而是“抓到了但首个候选选错了”。
2. 监测提前结束条件过宽：首轮只要发现任意 m3u8 就 break，导致 3 次重试没有执行完。
3. 页面里“此页面已被 Chrome 屏蔽”是播放器区域内的子请求被拦截，不一定等于主页面完全失败。

## 2. Chromium 通道的实际使用方式

当前版本已固定使用 Playwright Chromium，不再提供“优先系统 Chrome”选项。
这样行为更可预测、配置更少、日志更一致。

## 3. 本次已做的代码修正

### 3.1 监测阶段（`MonitorM3U8.py`）

1. 解析页地址自动解包：
   - 对 `?url=真实m3u8` 这类候选自动提取真实 m3u8 并加入候选池。
2. 候选排序改为“置信度优先”：
   - 真实 CDN m3u8 优先；
   - `vip.xxx/?url=...` 这类包裹地址降权。
3. 提前结束条件收紧：
   - 非递归深度（`depth!=3`）不再“有候选就停”，而是“有高置信候选才停”，否则继续后续重试轮次。
4. 代理行为改回可预测：
   - 未配置代理时，强制禁用系统代理（`--no-proxy-server`）。
5. blocked 兜底探测：
   - 若页面被 `ERR_BLOCKED_BY_CLIENT` 且浏览器侧没抓到资源，改用 requests 直连页面文本再提取 m3u8。

### 3.2 下载阶段（`DownloadM3U8.py`）

1. 主/子播放列表兼容：
   - 若加载到 master playlist（无 segments，但有 playlists），自动跟进首个子 playlist。
2. 空播放列表直接判失败：
   - `segments == 0` 直接抛出 `m3u8 read error`，让上层继续尝试下一个 m3u8。

### 3.3 任务执行阶段（`UI/MyWindow.py`）

1. 若一个 m3u8 下载结果没有可用分片，跳过 ffmpeg，避免无意义报错。

## 4. 为什么你会看到“播放器中间被屏蔽”

这通常是页面中的 iframe / 播放器解析链某一跳被客户端规则拦截（`ERR_BLOCKED_BY_CLIENT`），常见来源：

- 本机广告拦截或网络过滤组件；
- 运营商/网关层过滤；
- 站点把播放按钮绑定到解析域名，解析域被屏蔽。

它不代表主站不能打开，也不代表抓包一定失败。只要真实 m3u8 在网络响应里出现，程序仍可直接下载。

## 5. 为什么“还没开始下载”也会被识别或拦截

“监测阶段像普通用户”这个直觉不完全成立，原因有三类：

1. 客户端侧拦截（你现在这个报错的直接原因）：
   - `ERR_BLOCKED_BY_CLIENT` 是浏览器/客户端决策，常见于过滤规则命中某个域名或请求模式。
2. 页面行为差异：
   - 自动化点击节奏、触发顺序、焦点状态、媒体策略与真人操作可被脚本区分。
3. 浏览器指纹差异：
   - 即使是 Chromium，自动化上下文中的指纹细节和普通用户仍可能不同。

所以“还没分片下载”并不等于“不可能被拦截”。

## 6. 建议你现在这样验证

1. 配置保持：
   - `Headless=关闭`
   - `监测无界面模式=关闭`
   - `depth=2`
2. 观察新日志是否会继续跑到 `attempt 2/3`（当首轮只拿到低置信候选时）。
3. 若仍遇到 blocked 文案，但同时已拿到真实 CDN m3u8，重点看是否能进入有效分片下载（`total>0`）。
4. 若仍是 `find no resource`，重点看是否出现：
   - `requests fallback probe started`
   - 以及 fallback 后是否新增 `possible m3u8`。
5. 记录浏览器内核路径日志：
   - `playwright chromium executable=...`
   - `launch browser actual=chromium`

## 7. 为什么 `main.py` 能跑、`test_play.py` 却提示要 `playwright install`

这是“Python 运行环境不一致”导致的常见现象：

- 你运行 `main.py` 时可能使用了已带浏览器驱动/内核的环境；
- 你运行 `temporary/test_play.py` 时可能落在另一个 Python 环境，该环境只有 `playwright` 包，但没有安装浏览器内核。

排查方式：

1. 在两个入口分别打印 `sys.executable` 对比解释器路径；
2. 在同一解释器里执行 `playwright install chromium`。
