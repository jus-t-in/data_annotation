# 步态数据标注器

步态数据标注器是面向协议试次的 Linux 离线桌面工具集。该目录是完整、可独立复制和构建的 Python 项目，不依赖仓库外的脚本或示例数据。

工具集包含：

- 桌面标注编辑器：浏览多通道信号、修订边界、QA 闭环、撤销/重做和正式保存。
- 自动标注器：从完整试次信号生成活动与楼梯地形草稿及 QA 报告。
- 标注辅助工具：生成信号条带图、复核工作表并将工作表构建为正式标注。
- 静态可视化工具：绘制腿部位置、组合状态区间和事件备注。

所有工具完全离线运行，不上传数据。自动结果和人工修订均属于近似标注，不代表视频真值；术语与边界口径见 [CONTEXT.md](CONTEXT.md)。

## 支持环境

- Ubuntu/Debian 桌面 Linux，建议 Python 3.12；Python 支持范围为 3.10-3.13。
- 运行依赖：PySide6、pyqtgraph、NumPy、SciPy 和 Matplotlib。
- 中文图表建议安装 Noto Sans CJK SC。
- 不需要网络服务或数据库。

## 从 GitHub 部署

以下命令适用于可联网的 Ubuntu/Debian 桌面系统。

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y \
  ca-certificates git python3 python3-venv \
  libdbus-1-3 libegl1 libfontconfig1 libgl1 libx11-xcb1 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
  libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 \
  libxcb-xkb1 libxkbcommon-x11-0
```

中文图表建议同时安装字体：

```bash
sudo apt install -y fonts-noto-cjk
```

### 2. 克隆并安装

```bash
git clone https://github.com/jus-t-in/data_annotation.git
cd data_annotation
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

### 3. 验证安装

```bash
python -m pip check
youbu-annotation --version
youbu-auto-annotate --help
youbu-annotation-aid --help
youbu-visualize --help
```

以上命令均成功后启动桌面编辑器：

```bash
youbu-annotation
```

以后打开新终端时，先重新进入仓库并激活虚拟环境：

```bash
cd /path/to/data_annotation
source .venv/bin/activate
youbu-annotation
```

也可以不激活虚拟环境，直接执行 `.venv/bin/youbu-annotation`。

### 常见部署错误

- `ensurepip is not available`：系统未安装 `python3-venv`。安装后删除本次失败生成的 `.venv`，再从创建虚拟环境开始执行。
- `SSL: UNEXPECTED_EOF_WHILE_READING`：PyPI 连接被代理或网络中断。检查代理后重试，或临时使用 `python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple .`。
- `Could not load the Qt platform plugin "xcb"`：Qt/XCB 系统库缺失。重新执行第 1 步的系统依赖安装命令。

不要使用 `--trusted-host` 绕过 TLS 证书校验。

## 其他部署方式

### 安装已构建的 wheel

```bash
python3 -m venv .venv
.venv/bin/python -m pip install /path/to/youbu_annotation-0.1.0-py3-none-any.whl
```

单个应用 wheel 不包含第三方依赖。无网络的新机器应使用 [离线部署文档](docs/DEPLOYMENT.md) 中的完整离线包流程。

## 命令入口

安装后提供四个命令：

```text
youbu-annotation
youbu-auto-annotate
youbu-annotation-aid
youbu-visualize
```

每个命令均支持 --help。本项目不保留根目录包装脚本；安装后也可通过 `python -m youbu_annotation.<模块名>` 运行对应模块。

## 桌面编辑器

直接选择试次：

```bash
youbu-annotation
```

也可指定原始 CSV 和聚合标注文件：

```bash
youbu-annotation /data/P03_S01_T01_.csv \
  --annotations /data/state_changes.csv
```

未传 --annotations 时，默认使用原始 CSV 同目录的 state_changes.csv。标注文件不得覆盖原始试次 CSV。

编辑器提供联动信号图、活动与地形状态轨、事件表、边界吸附、区间与确认标记、撤销/重做、自动重新识别、QA 闭环、协作锁、备份、事务恢复、XDG 草稿恢复和 PNG 导出。结构校验、全部 QA、标注员 ID 和完整时间线复核声明同时满足后才能正式保存。

## 自动标注

处理目录中的全部试次文件：

```bash
youbu-auto-annotate /data
```

只处理某个受试者并指定输出：

```bash
youbu-auto-annotate /data \
  --subject P06 \
  --output /data/P06_state_changes.auto.csv \
  --report /data/P06_state_changes.report.json
```

参考标注只在显式传入 --reference 时用于事后评分，不参与检测。脚本拒绝输出覆盖参考文件。发现阻断级 QA 时仍写出草稿和报告，但退出码为 1；参数或输入错误的退出码为 2。

## 复核辅助

生成原始信号条带图：

```bash
youbu-annotation-aid plots /data
```

把自动结果转换为复核工作表：

```bash
youbu-annotation-aid revise /data/P06_state_changes.auto.csv \
  --data-dir /data \
  --output /data/review_worksheet.csv
```

将人工修订的工作表构建为正式标注：

```bash
youbu-annotation-aid build /data/review_worksheet.csv \
  --data-dir /data \
  --output /data/state_changes.csv
```

从另一个受试者的事件序列创建空时间工作表时，参考、目标受试者和输出位置均显式指定：

```bash
youbu-annotation-aid worksheet \
  --reference /reference/state_changes.csv \
  --skeleton-subject P04 \
  --subject P08 \
  --output /data/reference_worksheet.csv
```

## 静态可视化

```bash
youbu-visualize \
  --input-dir /data \
  --annotations /data/state_changes.csv
```

图片输出到 INPUT_DIR/plots。可同时传入多个标注 CSV。

## 输入约束

原始 CSV 必须为 UTF-8 或 UTF-8 BOM，使用英文逗号分隔。文件名需符合 `P<数字>_S<数字>_T<数字>...csv`，例如 `P06_S01_T05_v2.csv`。必需信号包括：

- RX Date/Time 或 Elapsed (s) 时间列。
- 三轴 Accelerometer/* 与三轴 Gyroscope/*。
- 俯仰与侧倾/俯仰。
- 左右腿位置/右腿位置。
- 左右腿位置/左右位置 或 左右腿位置/左腿位置。

所有必需数值必须有限。输入按时间排序，同时间戳的重复行逐通道平均；相邻采样超过 1.0 秒记为数据断档。

默认活动标签为 STILL、WALKING、BEND、SQUAT、HIGH_KNEE_SINGLE、HIGH_KNEE_ALTERNATING、TURNING_LEFT、TURNING_RIGHT、SHUFFLE 和 OTHER；默认地形标签为 LEVEL、ASCENT、DESCENT。可通过编辑器的“编辑 -> 管理标注类别”添加、停用或重新启用项目级自定义活动与地形。类别保存在标注 CSV 同目录的 `label_catalog.json`；导入标注 CSV 中未登记的类别时会自动加入。停用的类别不再用于新标注，但已有历史标注仍可载入和保存。ASCENT、DESCENT 只允许与 STILL 或 WALKING 组合，自定义地形不受此组合限制。

## 正式输出

聚合标注 CSV 固定包含：

```text
session_id,trial_id,input_file,timestamp,activity_truth,terrain_truth,notes
```

保存当前试次时只替换相同 input_file 的行，其他试次保持不变。配套文件包括审计报告、单份配对备份、协作锁和短暂存在的事务文件。恢复草稿位于 XDG_STATE_HOME 或 ~/.local/state/youbu-annotation/recovery。

## 源码结构

| 路径 | 职责 |
|------|------|
| src/youbu_annotation/editor.py | 桌面命令入口 |
| src/youbu_annotation/gui.py | PySide6/pyqtgraph 主窗口 |
| src/youbu_annotation/trial.py | CSV 校验、采样归一化和显示信号 |
| src/youbu_annotation/labels.py | 项目级活动与地形类别目录 |
| src/youbu_annotation/model.py | 边界、确认标记、撤销、QA 和结构校验 |
| src/youbu_annotation/auto_annotate.py | 离线识别算法与 CLI |
| src/youbu_annotation/auto_adapter.py | 识别结果到可编辑文档的适配 |
| src/youbu_annotation/annotation_aid.py | 标定与复核辅助 CLI |
| src/youbu_annotation/visualize_leg_positions.py | 静态可视化 CLI |
| src/youbu_annotation/storage.py | 聚合 CSV、报告、锁、备份和事务保存 |
| src/youbu_annotation/recovery.py | XDG 恢复草稿 |
| src/youbu_annotation/renderer.py | 编辑器 PNG 导出 |
| pyproject.toml | wheel 元数据和命令入口 |
| scripts/build_offline_bundle.sh | 完整离线包构建 |
| scripts/install_offline.sh | 目标机无网络安装 |
| docs/DEPLOYMENT.md | 离线部署说明 |

自动识别方法和桌面技术决策见 docs/adr。首版不支持视频、流式采集、遥测、多标签页或参考标注对照视图。
