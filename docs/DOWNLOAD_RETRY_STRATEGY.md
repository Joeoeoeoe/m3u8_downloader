# 分片下载重试与日志说明

本文说明分片下载阶段的重试预算、超时调节、完成判定和日志字段。

## 重试预算

`RetryFailed(retries=10)` 的实际重试轮次按任务规模计算，非固定值：

- 基础轮次：`max(10, retries)`
- 分片规模补偿：`min(20, total_segments // 150)`
- 首轮失败补偿：`min(40, first_pass_failed // 6)`
- 总上限：`120`

计算式：

`max_retry_rounds = min(120, base + long_video_bonus + failed_bonus)`

其中 `first_pass_failed` 指“首轮全量下载完成后”仍失败的分片数量。

## 提前停止

每轮重试都会比较失败列表是否收敛：

- `recovered = before_failed - after_failed`
- 连续多轮 `recovered <= 0` 视为停滞
- 停滞达到阈值（按预算动态计算，约 `5~12`）提前停止

## 超时调节

超时采用双向调节：

- 观察窗口：`delta_connections` 与 `delta_failures`
- 最近失败率 `>= 0.45`：超时 `+2`
- 最近失败率 `<= 0.15`：超时 `-1`
- 冷却时间：`8s`
- 边界：`4~25s`

重试轮次中还会做小步微调：

- 连续停滞达到 2 轮：`+1`
- 连续强恢复达到 3 轮：`-1`

## 候选完成判定

候选地址在 ffmpeg 成功后，按以下规则写入 `completed`：

- 无失败分片：`completed=true`
- 有失败分片但满足容忍条件：`completed=true`
- 有失败分片且不满足容忍条件：`completed=false`

当前容忍条件（任一满足）：

- 剩余失败分片数 `<= 2`
- 下载成功率 `>= 0.995`

实现与来源：

- 阈值定义在 `UI/MyWindow.py` 的 `completion_policy`（当前值：`maxMissingSegments=2`、`minSuccessRatio=0.995`）。
- 判定发生在候选下载完成并 `ffmpeg` 返回成功之后，再决定该候选写入 `completed=true/false`。
- 上述阈值属于下载器内部固定策略，当前不提供设置界面配置项。

## 日志字段

每个候选条目（`"0"`, `"1"`...）包含：

- `completed`
- `completedByTolerance`
- `mergeCompleted`
- `hasMissingSegments`
- `segmentStats`：`total/downloaded/failed`
- `completionMetrics`：`successRatio/missingRatio`
- `failedSegments`：剩余失败分片（`name/url`）
- `status`

## 加载规则

读取 `Data/*.json` 时：

- `completed=true` 归入已完成
- 其他归入未完成
