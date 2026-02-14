# monitor.rules.json 配置完整说明

这份文档是 `monitor.rules.json` 的完整规格说明，目标是：

- 不看源码，也能写出可运行的规则；
- 明确每个字段可写什么、默认值是什么、错写会怎样；
- 让配置风格统一：统一使用 `type + when + args`。

配套示例文件：`docs/monitor.rules.example.json`。

## 1. 文件位置与加载规则

### 1.1 运行时规则文件

- 默认运行文件：`monitor.rules.json`
- 默认位置：和 `MonitorM3U8.py` 同目录（也就是项目根目录）

### 1.2 示例文件

- 固定示例文件：`docs/monitor.rules.example.json`
- 仅用于参考，不会自动作为运行规则加载

### 1.3 在主配置中指定路径

在 `Config.json` 中设置：

```json
{
    "monitorRulesPath": "monitor.rules.json"
}
```

支持以下字段名（兼容写法，效果等价）：

- `monitorRulesPath`
- `rulesPath`
- `rules_path`

路径解析规则：

- 绝对路径：直接使用。
- 仅文件名（如 `monitor.rules.json`）：按 `MonitorM3U8.py` 同目录解析。
- 带相对目录（如 `configs/monitor.rules.json`）：按程序当前工作目录解析。

### 1.4 文件不存在/损坏时的行为

- 文件不存在：自动创建默认 `monitor.rules.json`。
- JSON 解析失败或根结构不是对象：原文件重命名为 `*.broken-时间戳`，然后重建默认文件。
- 单个动作执行报错：只跳过该动作，流程继续。

## 2. 规则文件总结构

完整结构如下（字段可按需删减）：

```json
{
    "chains": {
        "chain_name": [
            {
                "type": "extract",
                "when": "all",
                "args": {}
            }
        ]
    },
    "global": {
        "actions": [],
        "chains": {}
    },
    "sites": [
        {
            "name": "example-site",
            "enabled": true,
            "match": {
                "host": ["example.com", "*.example.com"],
                "url_contains": ["/play/"],
                "url_regex": ""
            },
            "actions": [],
            "chains": {}
        }
    ]
}
```

顶层字段说明：

- `chains`：全局可复用动作链字典，键是链名，值是 action 数组。
- `global`：全局规则，所有页面都先执行这里的动作。
- `sites`：按站点匹配追加动作。

## 3. 执行模型（一定要先理解）

### 3.1 执行顺序

每次交互执行动作时，顺序是：

1. `global.actions`（按顺序）
2. `sites` 中匹配成功的规则（按 `sites` 列表顺序），其 `actions` 依次追加执行

同一个 URL 可以匹配多个 site 规则，都会执行。

### 3.2 `when` 交互阶段

`when` 允许值：

- `all`：每次交互都执行（默认）
- `first`：仅首次交互执行
- `retry`：仅重试交互执行

说明：

- `interaction_stage=1` 视为首次。
- `interaction_stage>1` 视为重试。
- 未知值按 `all` 处理。

### 3.3 仅在深度 2/3 执行动作

动作交互逻辑只在 `depth` 为 `2` 或 `3` 时触发。  
如果 `depth=0` 或 `1`，规则文件虽然会加载，但不会执行交互动作链。

## 4. Action 统一格式

每个动作都建议写成：

```json
{
    "type": "click",
    "when": "retry",
    "args": {
        "selectors": ["$player"]
    }
}
```

字段定义：

- `type`：动作类型，必填。
- `when`：可选，默认 `all`。
- `args`：可选，建议始终提供对象 `{}`，便于风格统一。

兼容说明：

- 旧写法把参数直接放在 action 顶层（例如 `{"type":"click","selector":"..."}`）仍兼容；
- 程序会自动并入 `args`；
- 新配置请只用 `args`。

## 5. Chain（动作链）机制

### 5.1 定义链

在 `chains` 或 `global.chains` / `sites[i].chains` 里定义：

```json
{
    "chains": {
        "close_popup": [
            { "type": "click", "args": { "selectors": [".close"] } },
            { "type": "wait", "args": { "ms": 200 } }
        ]
    }
}
```

### 5.2 调用链

链调用也是一个 action：

```json
{
    "type": "chain",
    "when": "first",
    "args": {
        "name": "close_popup"
    }
}
```

`chain` 的 `args` 字段：

- `name`：链名，必填。
- `when`：可选，和顶层 `when` 等价；如果两者都写，顶层 `when` 优先。

### 5.3 链展开规则

- 支持链中再调用链。
- 最大递归深度 10，超出后跳过。
- 循环引用会跳过并打印日志。
- 引用不存在会跳过并打印日志。
- 如果 `chain` action 写了 `when`，它会“覆盖”链内未显式写 `when` 的动作。

### 5.4 链作用域优先级

链来源按作用域合并：

1. 顶层 `chains`
2. `global.chains`（可覆盖同名顶层链）
3. `sites[i].chains`（可覆盖全局同名链，仅对该 site 生效）

## 6. site 匹配字段（`sites[i].match`）

结构：

```json
{
    "host": ["example.com", "*.example.com"],
    "url_contains": ["/play/", "/vod/"],
    "url_regex": "https?://.*"
}
```

字段说明：

- `host`：字符串或字符串数组，匹配 URL hostname，支持 `*` 通配符（`fnmatch`）。
- `url_contains`：字符串或字符串数组，子串匹配完整 URL。
- `url_regex`：正则匹配完整 URL（忽略大小写）。

逻辑关系：

- `host`、`url_contains`、`url_regex` 三类条件之间是 AND（都要通过）。
- 每类数组内部是 OR（命中任意一项即可）。

额外说明：

- 比较时统一小写处理（`url_regex` 除外，它是 `re.IGNORECASE`）。
- 正则写错（非法表达式）时，该 site 规则直接不匹配。

## 7. 通用参数约定

### 7.1 `target`（作用目标）

适用于支持 `target` 的动作（例如 `click/hover/fill/play_media/wait_for_selector/evaluate`）：

- `page`：仅主页面（默认）
- `frames` 或 `frame`：仅子 frame
- `all` 或 `page_and_frames`：主页面 + 子 frame

### 7.2 选择器字段

可写：

- `selectors`: 字符串数组
- `selector`: 单个字符串

如果两者都写，会合并去重。

### 7.3 `$player` 宏

在 `selector(s)` 中写 `$player`，会展开为内置播放器相关选择器集合（`video`、常见播放按钮等）。

## 8. 所有 action 类型与参数清单

下文中“默认值/范围”是程序内实际约束。

### 8.1 `extract`

作用：从页面中提取候选资源链接。  
`args`：无。

### 8.2 `recover`

作用：如果页面被弹窗/跳转带偏，尝试回到稳定 URL。  
`args`：无。

### 8.3 `wait`

作用：固定等待。  
参数：

- `args.ms`：等待毫秒。默认 `300`，范围 `0~30000`。

### 8.4 `wait_for_candidates`

作用：等待直到出现新候选或超时。  
参数：

- `args.ms`：等待毫秒。默认 `1500`，范围 `0~30000`。

### 8.5 `play_media`

作用：对 `video/audio` 执行 `play()`（并尝试静音播放）。  
参数：

- `args.target`：默认 `page`。

### 8.6 `click`

作用：按选择器点击元素。每轮每个 selector 命中后，点击到第一个可见元素就进入下一步。  
参数：

- `args.target`：默认 `page`。
- `args.selectors` / `args.selector`：点击目标选择器，至少提供其一。
- `args.repeat`：轮次数。默认 `1`，范围 `1~20`。
- `args.wait_ms`：每轮结束后额外等待。默认 `300`，范围 `0~30000`。
- `args.max_per_selector`：每个 selector 最多遍历元素数。默认 `2`，范围 `1~20`。
- `args.visible_timeout_ms`：等待元素可见超时。默认 `600`，范围 `100~10000`。
- `args.click_timeout_ms`：点击超时。默认 `1400`，范围 `100~20000`。
- `args.wait_after_click_ms`：单次点击后等待再 recover。默认 `250`，范围 `0~10000`。
- `args.wait_for_candidates_ms`：每轮后等待新候选。默认 `0`，范围 `0~30000`。

### 8.7 `hover`

作用：按选择器 hover。  
参数：

- `args.target`：默认 `page`。
- `args.selectors` / `args.selector`：目标选择器。
- `args.repeat`：默认 `1`，范围 `1~20`。
- `args.max_per_selector`：默认 `1`，范围 `1~20`。
- `args.visible_timeout_ms`：默认 `600`，范围 `100~10000`。
- `args.hover_timeout_ms`：默认 `1200`，范围 `100~20000`。
- `args.wait_ms`：默认 `200`，范围 `0~30000`。
- `args.wait_for_candidates_ms`：默认 `0`，范围 `0~30000`。

### 8.8 `fill`

作用：输入内容，可选自动提交键。  
参数：

- `args.target`：默认 `page`。
- `args.selectors` / `args.selector`：输入框选择器。
- `args.value`：输入内容，默认空字符串。
- `args.index`：匹配多个元素时选哪个，从 0 开始。默认 `0`，最小 `0`。
- `args.fill_timeout_ms`：默认 `2500`，范围 `100~30000`。
- `args.visible_timeout_ms`：默认 `600`，范围 `100~10000`。
- `args.require_visible`：是否必须可见，默认 `true`。
- `args.submit_key`：可选，填充后按键，例如 `Enter`。

### 8.9 `wait_for_selector`

作用：等待某个选择器达到指定状态。  
参数：

- `args.target`：默认 `page`。
- `args.selector`：必填。
- `args.state`：默认 `visible`。建议使用 Playwright 支持的值（如 `attached/visible/hidden/detached`）。
- `args.timeout_ms`：默认 `5000`，范围 `100~60000`。

### 8.10 `wait_for_load_state`

作用：等待页面加载状态。  
参数：

- `args.state`：`domcontentloaded | load | networkidle | commit`。默认 `networkidle`，非法值回落默认。
- `args.timeout_ms`：默认 `8000`，范围 `100~60000`。

### 8.11 `goto`

作用：跳转页面。  
参数：

- `args.url`：必填。支持相对地址（会基于当前稳定 URL 解析）。
- `args.wait_until`：`domcontentloaded | load | networkidle | commit`。默认 `domcontentloaded`，非法值回落默认。
- `args.timeout_ms`：默认 `18000`，范围 `100~120000`。

### 8.12 `evaluate`

作用：执行 JavaScript。  
参数：

- `args.script`：必填，JS 代码字符串。
- `args.selector`：可选。
- `args.target`：仅在提供 `selector` 时生效，默认 `page`。
- `args.arg`：可选，作为 evaluate 参数传入。

执行方式：

- 没有 `selector`：执行 `page.evaluate(script[, arg])`。
- 有 `selector`：执行 `target.eval_on_selector_all(selector, script[, arg])`。

### 8.13 `scroll`

作用：鼠标滚轮滚动，可多段滚动。  
参数：

- `args.deltas`：推荐，数组，每一项是一段 y 滚动距离。
- `args.y`：当 `deltas` 不可用时，单次 y 滚动距离，默认 `240`。
- `args.x`：x 方向滚轮，默认 `0`。
- `args.wait_after_scroll_ms`：每段滚动后等待。默认 `250`，范围 `0~30000`。
- `args.wait_for_candidates_ms`：每段滚动后等待候选。默认 `1200`，范围 `0~30000`。
- `args.recover_after_scroll`：每段后是否尝试 recover，默认 `true`。

### 8.14 `mouse_click`

作用：坐标点击。  
参数：

- `args.position.x`：数字或 `"center"`/`"middle"`，默认视口中心 x。
- `args.position.y`：数字或 `"center"`/`"middle"`，默认视口中心 y。
- `args.button`：`left | right | middle`，默认 `left`。
- `args.click_count`：默认 `1`，范围 `1~3`。
- `args.delay_ms`：默认 `0`，范围 `0~3000`。

风格建议：

- 请只写 `args.position.x/y`，不要再写顶层 `x/y`。

### 8.15 `press`

作用：键盘按键。  
参数：

- `args.key`：必填，如 `Space`、`Enter`、`ArrowDown`。

### 8.16 `log`

作用：输出调试日志到控制台。  
参数：

- `args.message`：日志文本。

### 8.17 `chain`

作用：调用动作链（展开后执行）。  
参数：

- `args.name`：必填，链名。
- `args.when`：可选（等价于 action 顶层 `when`）。

## 9. 字段级“必填/可选/默认”速查

### 9.1 `sites[i]`

- `name`：可选，默认 `site_序号`。
- `enabled`：可选，默认 `true`。
- `match`：可选，默认空匹配（不限制）。
- `actions`：可选，默认空数组。
- `chains`：可选，默认空对象。

### 9.2 `global`

- `actions`：可选，默认空数组。
- `chains`：可选，默认空对象。

### 9.3 `action`

- `type`：必填。
- `when`：可选，默认 `all`。
- `args`：可选，建议始终提供对象。

## 10. 推荐配置风格（可维护且强）

1. 把通用流程放到 `chains`（例如“首次流程”“重试流程”）。
2. 在 `global.actions` 只做流程编排（调用 chain）。
3. 站点差异写在 `sites[].actions`，只改必要步骤。
4. 所有动作都统一成 `type + when + args`。
5. 逐步加动作，不要一次塞太多：先 `click + wait_for_candidates`，再补 `scroll/hover/evaluate`。

## 11. 两个完整示例

### 11.1 最小可用（全局）

```json
{
    "chains": {},
    "global": {
        "actions": [
            {
                "type": "click",
                "args": {
                    "selectors": ["$player"],
                    "repeat": 2
                }
            },
            {
                "type": "wait_for_candidates",
                "args": {
                    "ms": 1800
                }
            },
            {
                "type": "extract",
                "args": {}
            }
        ]
    },
    "sites": []
}
```

### 11.2 站点增强（弹窗 + 播放区）

```json
{
    "chains": {
        "close_popup": [
            {
                "type": "click",
                "args": {
                    "target": "all",
                    "selectors": [".close", ".btn-close", ".modal .x"],
                    "repeat": 2,
                    "wait_ms": 150
                }
            }
        ]
    },
    "global": {
        "actions": [
            {
                "type": "chain",
                "when": "first",
                "args": { "name": "close_popup" }
            },
            {
                "type": "click",
                "args": { "selectors": ["$player"] }
            },
            {
                "type": "wait_for_candidates",
                "args": { "ms": 1500 }
            }
        ]
    },
    "sites": [
        {
            "name": "example-player",
            "match": {
                "host": ["example.com", "*.example.com"],
                "url_contains": ["/play/"]
            },
            "actions": [
                {
                    "type": "hover",
                    "args": {
                        "target": "all",
                        "selectors": [".player", "$player"]
                    }
                },
                {
                    "type": "mouse_click",
                    "args": {
                        "position": { "x": "center", "y": "center" }
                    }
                }
            ]
        }
    ]
}
```

