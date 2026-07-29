# 离线部署

## 支持范围

- Linux x86_64，建议 Python 3.12。
- 构建机必须与目标机使用相同的 CPU 架构、Python 主次版本，并尽量使用不高于目标机的 glibc 版本。
- 目标机不需要网络，但必须预装对应版本的 Python、venv 和基础图形系统库。

## 在联网构建机生成离线包

进入项目根目录执行：

```bash
./scripts/build_offline_bundle.sh /tmp/youbu-annotation-offline
```

脚本生成 `/tmp/youbu-annotation-offline.tar.gz`。其中包含应用 wheel、全部 Python 依赖 wheel、校验文件、安装脚本和本文档。输出路径必须尚不存在，避免混入旧 wheel。

## 在离线目标机安装

传输并解压离线包：

```bash
tar -xzf youbu-annotation-offline.tar.gz
cd youbu-annotation-offline
sha256sum -c SHA256SUMS
./install_offline.sh /opt/youbu-annotation/.venv
```

若 `/opt/youbu-annotation` 不可写，可改用用户目录。安装过程强制使用 `--no-index`，不会访问软件源。
安装脚本完成前会导入全部运行依赖，并检查四个命令入口。任何依赖缺失或入口损坏都会使安装返回非零退出码。

## 启动

```bash
/opt/youbu-annotation/.venv/bin/youbu-annotation
/opt/youbu-annotation/.venv/bin/youbu-auto-annotate --help
/opt/youbu-annotation/.venv/bin/youbu-annotation-aid --help
/opt/youbu-annotation/.venv/bin/youbu-visualize --help
```

没有桌面菜单或终端环境时，可直接调用虚拟环境中的完整命令路径。中文图表建议在目标机安装 Noto Sans CJK SC 字体。

## 直接安装单个 wheel

有内部 PyPI 镜像或依赖已经就绪时，也可以只传应用 wheel：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install youbu_annotation-0.1.0-py3-none-any.whl
```

单个应用 wheel 不包含 NumPy、SciPy、Qt 等第三方依赖；完全离线的新机器应使用完整离线包。
