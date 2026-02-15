# monitor.rules.json 配置说明（可读版）

这份文档只讲“如何写出可用、可维护的规则”，不需要翻源码。

## 0. 快速开始（最小可用）

```json
{
    "global": {
        "actions": [
            { "type": "click", "args": { "selectors": ["$player"], "repeat": 2 } },
            { "type": "wait_for_candidates", "args": { "ms": 1600 } },
            { "type": "extract", "args": {} }
        ]
    }
}
```

## 1. 文件位置与加载

### 1.1 默认位置

- 运行文件：`monitor.rules.json`
- 默认路径：与 `MonitorM3U8.py` 同目录（项目根目录）

### 1.2 在主配置中指定

```json
{ "monitorRulesPath": "configs/monitor.rules.json" }
```

等价字段名：`monitorRulesPath` / `rulesPath` / `rules_path`。

路径规则：

- 绝对路径：直接用
- 仅文件名：按 `MonitorM3U8.py` 同目录
- 相对路径：按程序当前工作目录

### 1.3 文件损坏时

- 不存在：自动创建默认 `monitor.rules.json`
- 解析失败：重命名为 `*.broken-时间戳`，再重建默认文件

示例（自动修复后文件名）：

```
monitor.rules.json.broken-20260215-103012
```

## 2. 术语与执行模型（先看这个）

### 2.1 交互与尝试

一次“监测”会有多次尝试（attempt）。每次尝试会打开浏览器页面，执行动作链并抓取候选。

- `attempt=1`：首次交互
- `attempt>=2`：重试交互
- `attempt=last`：最后一次尝试

### 2.2 动作执行顺序

每次交互会按如下顺序执行动作：

1. `global.actions`
2. 命中的 `sites[i].actions`（按 `sites` 顺序追加）

同一个 URL 可以命中多个站点规则，都会执行。

示例（先全局，再站点）：

```json
{
    "global": { "actions": [ { "type": "log", "args": { "message": "global" } } ] },
    "sites": [
        {
            "name": "site-a",
            "match": { "host": ["a.com"] },
            "actions": [ { "type": "log", "args": { "message": "site-a" } } ]
        }
    ]
}
```

### 2.3 交互开关与尝试次数（独立于 depth）

`monitor.rules.json` 的动作链是否执行，由 `monitorInteraction` 控制，不再和 `depth` 绑定。

- `monitorInteraction=true`：执行动作链
- `monitorInteraction=false`：不执行动作链（只做基础抓取）

尝试次数由 `monitorTries` 控制（每个 URL 的嗅探尝试次数）。

## 3. depth 简化写法（可选）

`config/config.json` 里的 `depth` 支持数字或语义别名：

- `off / none` → `0`
- `lite / basic / simple` → `1`
- `standard / normal / default` → `2`（推荐）
- `deep / full / aggressive` → `3`

示例：

```json
{ "depth": "standard" }
```

注意：`depth` 只表达递归相关深度；交互行为由 `monitorInteraction` 单独控制。

## 4. 总体结构

```json
{
    "chains": { "chain_name": [ { "type": "extract", "args": {} } ] },
    "global": { "actions": [], "chains": {} },
    "sites": [
        {
            "name": "example-site",
            "enabled": true,
            "match": { "host": ["example.com"], "url_contains": ["/play/"], "url_regex": "" },
            "actions": [],
            "chains": {}
        }
    ]
}
```

字段含义：

- `chains`：全局可复用动作链
- `global`：全局动作
- `sites`：站点规则（按 URL 匹配追加动作）

## 5. `when`：更丰富的交互阶段

### 5.1 常用值

- `all`：每次交互都执行（默认）
- `first`：仅首次交互
- `retry`：仅重试交互（attempt>=2）
- `last`：仅最后一次尝试

### 5.2 语义别名

- `init/startup/start/initial` → 等价 `first`
- `final/end` → 等价 `last`

### 5.3 条件表达式（推荐）

你可以写成条件：

- `attempt=1`
- `attempt>=2`
- `attempt=last`
- `attempt<=2`

示例：

```json
{ "type": "click", "when": "attempt>=2", "args": { "selectors": ["$player"] } }
```

未知值会按 `all` 处理，不会报错。

## 6. Action 统一格式

所有动作建议写成：

```json
{ "type": "click", "when": "retry", "args": { "selectors": ["$player"] } }
```

兼容说明：旧写法把参数放在顶层仍可用，但建议统一放到 `args`。

## 7. Chain（动作链）

### 7.1 定义

```json
{
    "chains": {
        "close_popup": [
            { "type": "click", "args": { "selectors": [".close"], "repeat": 2 } },
            { "type": "wait", "args": { "ms": 200 } }
        ]
    }
}
```

### 7.2 调用

```json
{ "type": "chain", "when": "first", "args": { "name": "close_popup" } }
```

### 7.3 规则

- 链可嵌套，最大递归深度 10
- 循环引用会跳过并打印日志
- `chain` 自己写了 `when` 会覆盖链内未写 `when` 的动作

## 8. 站点匹配（`sites[i].match`）

```json
{
    "match": {
        "host": ["example.com", "*.example.com"],
        "url_contains": ["/play/", "/vod/"],
        "url_regex": "https?://.*"
    }
}
```

逻辑：`host`、`url_contains`、`url_regex` 之间是 AND；每一类数组内部是 OR。

## 9. 通用参数约定

### 9.1 `target`

- `page`：仅主页面（默认）
- `frame/frames`：仅子 frame
- `all/page_and_frames`：主页面 + 子 frame

示例：

```json
{ "type": "click", "args": { "target": "all", "selectors": [".btn"] } }
```

### 9.2 选择器字段

`selectors`（数组）或 `selector`（单个）都可以，程序会合并去重。

示例：

```json
{ "type": "hover", "args": { "selector": ".player" } }
```

### 9.3 `$player` 宏

`$player` 会展开为内置播放器相关选择器集合（`video`、常见播放按钮等）。

示例：

```json
{ "type": "click", "args": { "selectors": ["$player"] } }
```

## 10. Action 类型与示例

下文“默认值/范围”来自程序实际约束，每个动作给一个最小示例。

### 10.1 `extract`

```json
{ "type": "extract", "args": {} }
```

### 10.2 `recover`

```json
{ "type": "recover", "args": {} }
```

### 10.3 `wait`

```json
{ "type": "wait", "args": { "ms": 300 } }
```

### 10.4 `wait_for_candidates`

```json
{ "type": "wait_for_candidates", "args": { "ms": 1500 } }
```

### 10.5 `play_media`

```json
{ "type": "play_media", "args": { "target": "page" } }
```

### 10.6 `click`

```json
{ "type": "click", "args": { "selectors": ["$player"], "repeat": 2 } }
```

### 10.7 `hover`

```json
{ "type": "hover", "args": { "selectors": [".player"] } }
```

### 10.8 `fill`

```json
{ "type": "fill", "args": { "selector": "input[name='q']", "value": "m3u8" } }
```

### 10.9 `wait_for_selector`

```json
{ "type": "wait_for_selector", "args": { "selector": ".player", "state": "visible" } }
```

### 10.10 `wait_for_load_state`

```json
{ "type": "wait_for_load_state", "args": { "state": "networkidle", "timeout_ms": 8000 } }
```

### 10.11 `goto`

```json
{ "type": "goto", "args": { "url": "/play", "wait_until": "domcontentloaded" } }
```

### 10.12 `evaluate`

```json
{ "type": "evaluate", "args": { "script": "() => console.log('ping')" } }
```

### 10.13 `scroll`

```json
{ "type": "scroll", "args": { "deltas": [240, 800], "wait_for_candidates_ms": 1200 } }
```

### 10.14 `mouse_click`

```json
{ "type": "mouse_click", "args": { "position": { "x": "center", "y": "center" } } }
```

### 10.15 `press`

```json
{ "type": "press", "args": { "key": "Space" } }
```

### 10.16 `log`

```json
{ "type": "log", "args": { "message": "debug" } }
```

### 10.17 `chain`

```json
{ "type": "chain", "args": { "name": "close_popup" } }
```

## 11. 推荐组织方式

1. 把通用流程放到 `chains`（例如“首次流程”“重试流程”）。
2. 在 `global.actions` 只做流程编排（调用 chain）。
3. 站点差异写在 `sites[].actions`，只改必要步骤。
4. 所有动作统一为 `type + when + args`。
5. 逐步加动作，先 `click + wait_for_candidates`，再补 `scroll/hover/evaluate`。
