- `MainWindow.ui`
  - 主界面设计文件（网址输入、文件名输入、开始/停止/配置/清空按钮、输出文本框）。

- `MainWindow.py`
  - `MainWindow.ui` 对应的自动生成代码（`Ui_MainWindow`）。
  - 只负责创建控件和基础属性，不包含业务逻辑。

- `ConfigTabWindow.ui`
  - 当前使用的配置窗口设计文件（按 `常规/存储/下载` 分 Tab）。
  - 包含代理设置、最大并行数量、下载方式、深度等控件。

- `ConfigTabWindow.py`
  - `ConfigTabWindow.ui` 对应的自动生成代码（`Ui_ConfigWindow`）。

- `MyWindow.py`
  - UI 业务逻辑核心文件。
  - 负责信号槽绑定、配置读写、启动/停止任务、日志输出重定向。
  - 其中 `MyConfigWindow` 使用 `ConfigTabWindow`；`Worker` 负责调度监测与下载流程。

- `ConfigWindow.ui`（旧）
  - 旧版配置窗口设计文件，已被 `ConfigTabWindow.ui` 取代。

- `ConfigWindow.py`（旧）
  - 旧版配置窗口自动生成代码，当前主流程不再使用。


