# monitor.rules.json 配置说明

## 1. 文件加载与异常处理

- 默认规则文件：`monitor.rules.json`
- 默认位置：与 `MonitorM3U8.py` 同目录
- 可通过 `config/config.json` 的 `monitorRulesPath` 指定路径

路径解析规则：

- 绝对路径：直接使用
- 仅文件名：相对 `MonitorM3U8.py` 所在目录
- 相对路径：相对当前工作目录

异常处理规则（严格模式）：

- 文件不存在：自动创建默认规则文件
- JSON 结构不合法或字段不被支持：原文件重命名为 `*.broken-YYYYMMDD-HHMMSS`，随后重建默认规则

## 2. 顶层结构（固定）

根对象必须且只允许包含以下 3 个字段：

- `chains`：全局动作链定义
- `global`：全局动作入口
- `sites`：站点规则列表

```json
{
  "chains": {},
  "global": {
    "actions": [], // type + when + args for an act
    "chains": {}
  },
  "sites": []
}
```

## 3. 执行模型

### 3.1 串行执行

- `global.actions` 是顺序执行
- `sites[i].actions` 也是顺序执行
- 多个站点规则命中时：按 `sites` 中出现顺序追加到全局动作后执行

### 3.2 并行等待

- 并行语义由 `wait_group` 实现
- `wait_group.args.group_actions` 是并行等待条件集合
- `mode=any`：任一子条件满足即结束
- `mode=all`：全部子条件满足才结束

### 3.3 “串行会不会阻塞后续识别”

不会因为“元素已经出现过”而永久阻塞。  
原因：`wait_for_selector` 每次都会先检查当前页面状态；如果条件已经满足，会立即返回，不会一直等待到超时。

## 4. `when` 表达式

`when` 控制动作在第几次探测尝试中执行。  
表达式是“可计算条件”，不是固定标签枚举。

支持值：

- 数值：`1`、`2`（等价于 `=1`、`=2`）
- 字符串表达式：`=1`、`==1`、`>1`、`>=2`、`<4`、`<=3`、`=last`
- 数组（OR 关系）：`["=1", "=last"]`

说明：

- `last` 表示本次 URL 的最后一次尝试（由 `monitorTries` 决定）
- 未写 `when`：默认每次尝试都执行

示例：

```json
{ "type": "chain", "when": "=1", "args": { "name": "first_pass" } }
```

```json
{ "type": "chain", "when": [">=2", "=last"], "args": { "name": "retry_pass" } }
```

## 5. `chains`

`chains` 是对象：键为链名，值为动作数组。

```json
{
  "chains": {
    "first_pass": [
      { "type": "play_media", "args": { "target": "page" } },
      { "type": "wait", "args": { "ms": 1200 } }
    ]
  }
}
```

链引用方式：

- 使用 `type=chain`
- `args.name` 指向链名

```json
{ "type": "chain", "args": { "name": "first_pass" } }
```

## 6. `global`

`global` 字段：

- `actions`：全局动作数组（必填，可为空数组）
- `chains`：全局局部链定义（可选覆盖，与根 `chains` 合并）

```json
{
  "global": {
    "actions": [
      { "type": "chain", "when": "=1", "args": { "name": "first_pass" } },
      { "type": "chain", "when": ">=2", "args": { "name": "retry_pass" } }
    ],
    "chains": {}
  }
}
```

## 7. `sites`

`sites` 是数组。每项字段：

- `name`：规则名称，字符串
- `enabled`：是否启用，布尔值
- `match`：匹配条件对象
- `actions`：命中后附加动作数组
- `chains`：站点私有链定义

```json
{
  "sites": [
    {
      "name": "example-site",
      "enabled": true,
      "match": {
        "host": ["example.com", "*.example.com"],
        "url_contains": ["/play/"],
        "url_regex": "^https?://[^/]+/play/\\d+\\.html$"
      },
      "actions": [
        { "type": "click", "when": ">=2", "args": { "selectors": ["$player"], "target": "all" } }
      ],
      "chains": {}
    }
  ]
}
```

## 8. `match` 规则

`match` 子字段全部可选，三类条件之间是 **OR** 关系：

- `host`：字符串或数组，通配匹配（`fnmatch`）
- `url_contains`：字符串或数组，URL 子串匹配（忽略大小写）
- `url_regex`：字符串，Python `re.search` 正则匹配（忽略大小写）

判定规则：

- 只写了部分字段：只对已写字段做判定
- 任一已写字段命中即匹配成功
- 三类都没写：视为匹配所有 URL

示例 1（只按 host）：

```json
{ "match": { "host": "*.example.com" } }
```

示例 2（只按 contains）：

```json
{ "match": { "url_contains": ["/vod/", "/play/"] } }
```

示例 3（只按 regex）：

```json
{ "match": { "url_regex": "^https?://[^/]+/(play|vod)/\\d+\\.html$" } }
```

## 9. `url_regex` 语法

`url_regex` 使用 Python `re` 语法，匹配方式是 `re.search(..., flags=re.IGNORECASE)`。

常见写法：

- 开头/结尾锚点：`^...$`
- 分组：`(play|vod)`
- 数字：`\\d+`
- 字符类：`[a-z0-9_-]+`
- 可选段：`(?:/index)?`

注意：

- JSON 中反斜杠要转义，例如 `\d+` 要写成 `\\d+`
- 非法正则会被判定为规则文件错误，触发 broken+重建

## 10. Action 总览

动作统一结构：

```json
{
  "type": "click",
  "when": ">=2",
  "args": {}
}
```

字段：

- `type`：动作类型（必填）
- `when`：尝试轮次表达式（可选）
- `args`：动作参数对象（可选，取决于动作类型）

可用动作类型：

- `chain`
- `wait`
- `wait_for_selector`
- `wait_group`
- `play_media`
- `click`
- `hover`
- `fill`
- `wait_for_load_state`
- `goto`
- `evaluate`
- `scroll`
- `mouse_click`
- `press`
- `log`

不再支持作为配置动作的类型：

- `extract`
- `recover`
- `wait_for_candidates`

这三类行为已改为内置监测流程，不再通过规则文件配置。

## 11. 通用参数：`target` / `selector(s)`

### 11.1 `target`

可选值：

- `page`：仅主页面
- `frame` 或 `frames`：仅 iframe
- `all` 或 `page_and_frames`：主页面 + 所有 iframe

### 11.2 `selector` 与 `selectors`

- `selector`：单个选择器字符串
- `selectors`：选择器数组
- 两者可同时出现，程序会合并后使用
- `$player`：播放器选择器宏，展开为内置常见播放器选择器集合

## 12. Action 字段明细与示例

### 12.1 `chain`

`args` 字段：

- `name`：链名（必填，字符串）

```json
{ "type": "chain", "when": "=1", "args": { "name": "first_pass" } }
```

### 12.2 `wait`

`args` 字段：

- `ms`：等待毫秒数，`0~30000`

```json
{ "type": "wait", "when": ">=1", "args": { "ms": 1200 } }
```

### 12.3 `wait_for_selector`

`args` 字段：

- `selector` / `selectors`：至少提供一个
- `state`：`attached | detached | visible | hidden`
- `match`：`any | all`
- `target`：见第 11 节
- `timeout_ms`：`100~60000`
- `poll_ms`：`50~1000`，轮询间隔毫秒

```json
{
  "type": "wait_for_selector",
  "when": ">=1",
  "args": {
    "selectors": ["$player", ".video-wrap"],
    "state": "visible",
    "match": "any",
    "target": "all",
    "timeout_ms": 6000,
    "poll_ms": 150
  }
}
```

### 12.4 `wait_group`

`args` 字段：

- `mode`：`any | all`
- `timeout_ms`：`100~120000`
- `poll_ms`：`50~1000`
- `group_actions`：并行子动作数组（仅允许 `wait` / `wait_for_selector` / `wait_group`）

```json
{
  "type": "wait_group",
  "when": ">=1",
  "args": {
    "mode": "any",
    "timeout_ms": 7000,
    "poll_ms": 120,
    "group_actions": [
      {
        "type": "wait_for_selector",
        "args": {
          "selectors": ["video", ".player"],
          "state": "visible",
          "match": "any",
          "target": "all",
          "timeout_ms": 6500,
          "poll_ms": 150
        }
      },
      { "type": "wait", "args": { "ms": 1000 } }
    ]
  }
}
```

### 12.5 `play_media`

`args` 字段：

- `target`：见第 11 节

```json
{ "type": "play_media", "when": "=1", "args": { "target": "page" } }
```

### 12.6 `click`

`args` 字段：

- `selector` / `selectors`（至少一个）
- `target`
- `repeat`：`1~20`
- `wait_ms`：每轮点击后等待，`0~30000`
- `max_per_selector`：每个选择器最多尝试元素数，`1~20`
- `visible_timeout_ms`：元素可见判定超时，`100~10000`
- `click_timeout_ms`：点击超时，`100~20000`
- `wait_after_click_ms`：单次点击后恢复前等待，`0~10000`

```json
{
  "type": "click",
  "when": ">=2",
  "args": {
    "selectors": ["$player", ".play-btn"],
    "target": "all",
    "repeat": 2,
    "wait_ms": 1200,
    "max_per_selector": 2,
    "visible_timeout_ms": 800,
    "click_timeout_ms": 1600,
    "wait_after_click_ms": 300
  }
}
```

### 12.7 `hover`

`args` 字段：

- `selector` / `selectors`（至少一个）
- `target`
- `repeat`：`1~20`
- `max_per_selector`：`1~20`
- `visible_timeout_ms`：`100~10000`
- `hover_timeout_ms`：`100~20000`
- `wait_ms`：`0~30000`

```json
{
  "type": "hover",
  "when": ">=2",
  "args": {
    "selectors": [".player-wrap"],
    "target": "all",
    "repeat": 1,
    "max_per_selector": 1,
    "visible_timeout_ms": 700,
    "hover_timeout_ms": 1200,
    "wait_ms": 500
  }
}
```

### 12.8 `fill`

`args` 字段：

- `selector` / `selectors`（至少一个）
- `target`
- `value`：填充值，字符串
- `index`：命中元素下标，`>=0`
- `fill_timeout_ms`：`100~30000`
- `visible_timeout_ms`：`100~10000`
- `require_visible`：布尔
- `submit_key`：可选按键（如 `Enter`）

```json
{
  "type": "fill",
  "when": "=1",
  "args": {
    "selector": "input[name='wd']",
    "target": "page",
    "value": "m3u8",
    "index": 0,
    "fill_timeout_ms": 3000,
    "visible_timeout_ms": 800,
    "require_visible": true,
    "submit_key": "Enter"
  }
}
```

### 12.9 `wait_for_load_state`

`args` 字段：

- `state`：`domcontentloaded | load | networkidle | commit`
- `timeout_ms`：`100~60000`

```json
{
  "type": "wait_for_load_state",
  "when": "=1",
  "args": {
    "state": "networkidle",
    "timeout_ms": 12000
  }
}
```

### 12.10 `goto`

`args` 字段：

- `url`：目标 URL（必填，支持相对 URL）
- `wait_until`：`domcontentloaded | load | networkidle | commit`
- `timeout_ms`：`100~120000`

```json
{
  "type": "goto",
  "when": "=1",
  "args": {
    "url": "/play/12345.html",
    "wait_until": "domcontentloaded",
    "timeout_ms": 18000
  }
}
```

### 12.11 `evaluate`

`args` 字段：

- `script`：JS 脚本（必填，字符串）
- `selector`：可选；存在时执行 `eval_on_selector_all`
- `target`：可选；配合 `selector` 使用
- `arg`：可选；脚本参数

```json
{
  "type": "evaluate",
  "when": ">=2",
  "args": {
    "script": "(els) => els.forEach(el => el.click())",
    "selector": ".play-btn",
    "target": "all"
  }
}
```

### 12.12 `scroll`

`args` 字段：

- `deltas`：滚轮 Y 偏移数组（推荐）
- `y`：当 `deltas` 不存在时的单次 Y 偏移
- `x`：滚轮 X 偏移
- `wait_after_scroll_ms`：每次滚动后等待，`0~30000`

```json
{
  "type": "scroll",
  "when": ">=2",
  "args": {
    "deltas": [240, 900, 1500],
    "x": 0,
    "wait_after_scroll_ms": 800
  }
}
```

### 12.13 `mouse_click`

`args` 字段：

- `position.x` / `position.y`：点击位置（数值或 `center`/`middle`）
- `x` / `y`：可选备用写法
- `button`：`left | right | middle`
- `click_count`：`1~3`
- `delay_ms`：`0~3000`

```json
{
  "type": "mouse_click",
  "when": ">=2",
  "args": {
    "position": { "x": "center", "y": "center" },
    "button": "left",
    "click_count": 1,
    "delay_ms": 0
  }
}
```

### 12.14 `press`

`args` 字段：

- `key`：键名（必填，如 `Space`、`Enter`）

```json
{ "type": "press", "when": ">=2", "args": { "key": "Space" } }
```

### 12.15 `log`

`args` 字段：

- `message`：日志文本

```json
{ "type": "log", "when": "=1", "args": { "message": "site-rule-hit" } }
```

## 13. 递归探测与规则的关系

递归探测由主配置 `recursionDepth` 控制，与规则动作链独立：

- 第 1 层：用户输入 URL
- 第 2 层及以后：从“已加载页面/响应文本”提取到的页面链接继续探测
- `monitorTries` 仅控制每一层 URL 的尝试次数
- `monitorInteraction` 仅控制是否执行动作链

规则文件用于“如何操作页面”，不控制“是否提取 m3u8”这一核心行为。  
链接提取始终由监测引擎持续执行。

## 14. 运行日志中的规则命中信息

每次监测开始前会打印：

- 规则文件来源路径
- 命中站点规则数量
- 每条命中规则的 `name / host / url_contains / url_regex / actions_count`

该日志用于确认规则匹配是否成功。
