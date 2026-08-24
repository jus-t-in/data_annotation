# 采用本机 Python 服务与浏览器前端

**Status: accepted**

交互应用的终态由仅绑定 loopback 的本机 Python 服务提供项目、数据、自动标注、HTTP 命令与静态资源，由 React、TypeScript 和 Vite 构建的前端运行在系统浏览器中；不封装 Electron、AppImage 或 Linux 系统安装包。PySide6 只保留为迁移期对照实现，Web 工作流达到功能和回归等价后移除。本机服务必须校验 Host、Origin 和修改请求会话令牌，不把 CORS 当作授权机制。
