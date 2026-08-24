# 使用 PySide6 与 pyqtgraph 构建本地标注编辑器

**Status: superseded by ADR-0027**

首版采用仅正式支持 Linux 的单机桌面应用，以 PySide6 管理窗口与控件、pyqtgraph 提供多轨信号及交互边界，并直接复用现有 Python 自动识别代码。相比本机 Web 架构，它无需服务进程和前后端协议；相比 Matplotlib 控件，它更适合联动缩放、拖拽边界和同步表格，同时保留未来跨平台打包的可能。
