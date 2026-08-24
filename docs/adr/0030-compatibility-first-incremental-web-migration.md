# 兼容优先地渐进迁移到 Web

**Status: accepted**

重构期间冻结内置 V2 识别器的事件、QA 和退出码语义，保留七列标注 CSV 以及 `youbu-annotation`、`youbu-auto-annotate`、`youbu-annotation-aid`、`youbu-visualize` 四个命令入口。先让现有 PySide6 与新 HTTP 适配器调用同一领域模块接口，再逐工作流迁移浏览器界面；达到试次加载、信号浏览、编辑、撤销重做、QA、自动预标注、恢复、保存和导出等价后才删除旧界面。识别器通过内部接口隔离，但首版不提供第三方插件发现或安装。
