# 离线多通道时间序列标注工具调研与重构启示

检索日期：2026-08-17（Asia/Shanghai）

## 1. 范围与方法

本报告面向一个明确边界：单机离线、单用户、多通道步态/传感器时间序列标注工作台；终态由本地 Python 服务在 loopback 上提供 TypeScript 浏览器界面，需要人工区间/边界编辑、内置离线自动预标注、QA、审计、崩溃恢复，以及项目级可版本化标签模式。活动轨和地形轨固定存在，轨内标签与合法组合不能继续写死，历史标注必须保持可解释。

候选分两组：

- 直接相邻：Label Studio、BORIS、OXI、Datavyu、VIA。它们分别覆盖多通道时间序列、行为事件、逐样本信号、时间对齐编码和轻量时间段标注。
- 架构可迁移但产品错位：INCEpTION、CVAT、Argilla。它们的项目模式、版本、预标注和质量工作流值得借鉴，但文本或视觉任务模型不适合直接替换本项目。

事实只采集自官方仓库、官方文档和标准组织页面。GitHub star 是 2026-08-17 从 GitHub Repository API 读取的瞬时值，只代表公开可见度，不代表专业领域适配度。表中的“适配判断”和“优缺点”是基于证据作出的本项目分析，不是上游项目的自我声明。

## 2. 结论先行

不建议迁移到任何一个现成平台，但建议把现有桌面程序渐进迁移为本地 Web 应用：Python 进程绑定 loopback、提供领域命令与静态资源，TypeScript 前端运行在用户现有浏览器中。PySide6 仅在迁移期保留到 Web 功能与回归结果达到同等水平，不作为目标界面；首期不引入 Electron、AppImage 或仅 Linux 的发布形态。Label Studio 证明时间序列标注、本地服务、TypeScript 前端和 SQLite 可以组合在同一产品中，但其部署与通用任务模型不应直接成为本项目底座。([LS-README](#source-ls-readme)) ([LS-TS](#source-ls-ts))

应组合借鉴五套模式：

1. Label Studio 的项目级声明式标注配置，以及“已有标注使用某标签时不可直接移除”的保护。([LS-CONFIG](#source-ls-config))
2. INCEpTION 的 layer/tagset 分离、项目快照和包含 layer configuration 的 Git 版本化；它是本次调研中最接近“模式与标注共同版本化”的先例，但当前功能仍标为实验性。([INC-GUIDE](#source-inc-guide))
3. BORIS 的 project/ethogram/observation 分层、point/state 事件、未配对状态检查、自动保存和恢复思路。([BORIS-PROJECT](#source-boris-project)) ([BORIS-PREF](#source-boris-pref))
4. OXI 的浏览器端多序列联动与框选交互，但不采用它的逐样本单标签存储模型。([OXI-LABELER](#source-oxi-labeler)) ([OXI-HELP](#source-oxi-help))
5. W3C PROV 的 Entity/Activity/Agent、revision/derivation/attribution 关系，用作本地审计语义，而不引入 RDF 技术栈。([STD-PROV](#source-std-prov))

标签模式采用不可变发布快照：稳定 `schema_id` + 单调递增 `version` + 内容 SHA-256。两条固定轨分别为 `activity` 和 `terrain`；用户可以手动新增、修改显示属性和停用轨内标签，也可以修改合法组合，但不能新增或删除轨。发布后不原地改定义；改显示名、新增标签、停用标签或修改合法组合都生成新版本。每份正式标注固定引用模式版本与上一标注修订，迁移或编辑都生成新的不可变修订，不覆盖历史。

## 3. 候选概览

| 候选 | GitHub 可见度与许可 | 功能/数据 | 架构、技术栈、离线方式 | 标签/模式扩展 | 质量、审计、恢复 | 对本项目的判断 |
|---|---|---|---|---|---|---|
| **Label Studio** | 28,076 stars；Apache-2.0；主语言 TypeScript。([LS-GH](#source-ls-gh)) | 原生 `TimeSeries`/`MultiChannel`，可从 CSV、TSV、JSON 展示多通道并标时间区间；结果为带 `start/end` 的 JSON。([LS-TS](#source-ls-ts)) | 本地 pip/Docker 启动 Web 服务；默认 SQLite；前端 React + mobx-state-tree，生产示例为 Nginx + PostgreSQL。([LS-README](#source-ls-readme)) | 项目使用 XML 配置 Object/Control/Visual tags；已有标注使用标签时不能直接移除该标签。([LS-CONFIG](#source-ls-config)) | 可导入带 `model_version` 的 predictions 供复核；Community 版没有 reviewer、activity logs、annotation history、agreement 等完整质量工作流。([LS-PRED](#source-ls-pred)) ([LS-COMPARE](#source-ls-compare)) | **架构形态和数据形态最接近，但不直接作为底座。** 可借本地 Python/SQLite + TypeScript 浏览器、声明式标签和删标签保护；通用任务模型与多用户部署超出本项目。 |
| **BORIS** | 245 stars；GPL-3.0；主语言 Python；作者报告其论文已有 2,834+ 次同行评议引用。([BORIS-GH](#source-boris-gh)) ([BORIS-README](#source-boris-readme)) | 视频/音频/现场行为事件；ethogram 可定义 point/state、分类、修饰符和互斥矩阵；外部数值数据可与媒体同步绘图，但文档限定同时绘制两个值。([BORIS-PROJECT](#source-boris-project)) ([BORIS-OBS](#source-boris-obs)) | 跨平台桌面；Python 3.12、PySide6、pyqtgraph、pandas、SciPy、scikit-learn。([BORIS-STACK](#source-boris-stack)) | 项目文件包含 ethogram、变量、subjects、coding maps 和 observations；行为 code 必填，可导入/导出 ethogram，但没有不可变模式历史。([BORIS-PROJECT](#source-boris-project)) | 状态事件未配对警告、time budget、Cohen's kappa IRR、JSON 项目文件和定时自动保存。([BORIS-ANALYSIS](#source-boris-analysis)) ([BORIS-PREF](#source-boris-pref)) | **领域工作流参考价值最高，不是目标技术栈。** 事件模型、恢复和 QA 可借；桌面界面、多通道能力和可原地修改的项目 JSON 不满足已确认终态。 |
| **OXI** | 10 stars；MIT；主语言 JavaScript；最后 push 为 2023-08-07。([OXI-GH](#source-oxi-gh)) | 面向长多通道时间序列，可选 active/reference series、框选多点、同时标多个 series；一个样本只能有一个标签。([OXI-README](#source-oxi-readme)) ([OXI-HELP](#source-oxi-help)) | Vue 2 + D3 + PapaParse 的纯客户端应用，Express 只提供静态文件；数据不上传网络。([OXI-PACKAGE](#source-oxi-package)) ([OXI-INDEX](#source-oxi-index)) | 可在会话中新增/删除标签；删除标签会清空使用该标签的样本，没有模式版本/迁移边界。([OXI-LABELER](#source-oxi-labeler)) | 每次修改自动保存浏览器本地会话并可恢复；导出 CSV。官方资料未描述 QA/审计。([OXI-HELP](#source-oxi-help)) | **最接近绘图交互，最不接近领域语义。** 框选/active-reference 交互可借；逐样本单标签、2M 行截断和破坏式删标签不应照搬。 |
| **Datavyu** | 33 stars；GPL-3.0；主语言 Java。([DAT-GH](#source-dat-gh)) | 桌面视频行为编码；列中 cell 带 onset、offset、ordinal 和用户 codes，支持跨列 temporal alignment。([DAT-SPREAD](#source-dat-spread)) | Java 桌面程序，Ruby/JRuby 脚本 API；Windows/macOS 有构建产物。([DAT-README](#source-dat-readme)) | Code Editor 可增、改名、删除列和 code，表格自动更新；没有文档化的模式版本或迁移。([DAT-CODE](#source-dat-code)) | 官方最佳实践和脚本支持 inter-rater reliability；内置 CSV 导出，复杂导出可用 Ruby。([DAT-REL](#source-dat-rel)) ([DAT-EXPORT](#source-dat-export)) | **适合借鉴多轨时间对齐和编码手册思维。** 缺少原生传感器曲线与自动预标注，技术栈老且 GPL，不宜复用实现。 |
| **VIA 3** | 247 stars；BSD-2-Clause；主语言 JavaScript。([VIA-GH](#source-via-gh)) | 手工标图片/音频/视频；音视频支持 start/end temporal segments，属性支持 text/checkbox/radio/select。([VIA-CODE](#source-via-code)) | 无外部库的 HTML/CSS/JS，单个小于 300KB 的自包含 HTML 可离线运行。([VIA-README](#source-via-readme)) | Project JSON 有 attribute ID、options、`rev`、`rev_timestamp`、`data_format_version`；删除属性会删除对应 metadata 值，不是历史安全迁移。([VIA-CODE](#source-via-code)) | 定位为 manual annotation；所引官方设计文档未给出 QA、审计或自动标注工作流。([VIA-README](#source-via-readme)) | **适合借数据驱动 UI 和稳定内部 ID。** 极轻，但没有传感器通道、领域 QA、恢复/审计深度。 |
| **INCEpTION** | 711 stars；Apache-2.0；主语言 Java。([INC-GH](#source-inc-gh)) | 多用户文本 span/relation/chain 标注，内置 recommender、curation、agreement。([INC-README](#source-inc-readme)) ([INC-GUIDE](#source-inc-guide)) | Web 应用；Windows/macOS 桌面包启动本地服务器和浏览器，Linux 可运行 standalone JAR。([INC-INSTALL](#source-inc-install)) | 自定义 layers/features/tagsets；实验性 Project Versioning 用 `.inception` Git 仓库快照全部文档和 layer configuration。([INC-GUIDE](#source-inc-guide)) | Curation 合并多标注者结果、agreement metrics、项目 log；还支持可配置 annotation 文件内部备份。([INC-GUIDE](#source-inc-guide)) ([INC-BACKUP](#source-inc-backup)) | **模式版本化的最佳参考，产品模型严重错位。** 借 tagset/layer/snapshot，不借 UIMA、文本编辑器和多用户服务。 |
| **CVAT** | 16,538 stars；MIT；主语言 Python。([CVAT-GH](#source-cvat-gh)) | 图片、视频、3D 视觉标注和大量 CV 数据集格式；支持模型自动预标注。([CVAT-FORMATS](#source-cvat-formats)) ([CVAT-AUTO](#source-cvat-auto)) | Docker/浏览器多服务；Django + DRF 后端、PostgreSQL、Redis/Kvrocks、导入/导出/自动标注等 workers。([CVAT-ARCH](#source-cvat-arch)) | Project 下 tasks 继承 labels，label 可加 attributes；没有面向历史标注的不可变模式版本。([CVAT-PROJECT](#source-cvat-project)) | 有 ground truth/validation 概念；quality metrics 和冲突 review 页面标明仅 Online/Enterprise。([CVAT-QA](#source-cvat-qa)) | **只借鉴 QA 分层和 worker 接口。** 输入模型、部署复杂度和付费质量边界均不适合单机传感器标注。 |
| **Argilla** | 5,081 stars；Apache-2.0；主语言 Python。([ARG-GH](#source-arg-gh)) | 面向 NLP/LLM/多模态记录；dataset settings 定义 fields、questions、guidelines；record 可有 suggestions 和人工 responses。([ARG-README](#source-arg-readme)) ([ARG-RECORD](#source-arg-record)) | Python SDK 连接独立 Server/Web UI，需要 API URL/key。([ARG-README](#source-arg-readme)) | dataset 发布后只允许有限字段更新；支持设置最少 submitted responses 的任务分配，但不是不可变 schema 历史。([ARG-DATASET](#source-arg-dataset)) | suggestion/response 分离清楚，适合保存模型建议与人工答案；不提供时间区间/多通道信号模型。([ARG-RECORD](#source-arg-record)) | **只借预标注与人工结果分离。** Record/question 模型不应强套到连续时间状态。 |

## 4. 逐项启示

### 4.1 Label Studio：配置能力强，但“可配置”不等于“可版本化”

Label Studio 的时间序列标注配置可以声明多个 `Channel`，用 `TimeSeriesLabels` 标区间，并以 CSV/TSV/JSON 输入、JSON 输出 `start/end`。这验证了“项目声明 UI + 通用时间区间结果”的可行性。([LS-TS](#source-ls-ts))

更重要的是，它在修改项目配置时阻止删除仍被已有 annotation 使用的标签。这个保护应进入本项目领域层，而不是只放在 GUI 确认框中。([LS-CONFIG](#source-ls-config))

但 Label Studio 的 Community 版缺少 reviewer、activity logs、annotation history 和 agreement；这些不是可直接获得的开源能力。([LS-COMPARE](#source-ls-compare)) 因此应借它的本地 Web 形态，而不是替换现有程序或接受其完整服务栈；审计、恢复与步态领域规则仍由本项目实现。

### 4.2 BORIS：与当前领域工作流最贴近

BORIS 把“行为定义（ethogram）”与“观测（observation）”放在同一项目容器中，并明确区分无持续时间的 point event 和带 start/stop 的 state event；未闭合 state event 会产生 `UNPAIRED` 警告。([BORIS-PROJECT](#source-boris-project)) 这与本项目“边界事件形成状态区间”的模型接近，提示我们应让 track/label schema 与 annotation document 分离，但共同由 project 引用。

BORIS 的自动保存、JSON 项目文件、IRR 和外部数据同步是实用参考。([BORIS-PREF](#source-boris-pref)) ([BORIS-ANALYSIS](#source-boris-analysis)) 不应复制的是把 schema 与 observation 放进一个可原地编辑 JSON；本项目要求历史解释和显式迁移，需要不可变 schema snapshots。

### 4.3 INCEpTION：版本化标签模式的最佳先例

INCEpTION 把 layer、feature、tagset 分开管理；tagset 是 feature 可引用的固定标签列表。其 41.3 文档还说明：现有 annotation 在 tagset 改动或删除后保留原值。([INC-GUIDE](#source-inc-guide)) 这说明“保留数据”与“仍可解释数据”是两回事：本项目不能只保留字符串，还必须保留当时 schema snapshot。

其 Project Versioning 会快照所有 documents 和 layer configuration 到项目内 Git 仓库，但仍标为 experimental。([INC-GUIDE](#source-inc-guide)) 建议借“模式和数据一起定格”的语义，不借“在用户项目里嵌 Git”这一具体机制；本项目用 SQLite 中的不可变版本/修订记录、内容 hash 和显式迁移表达同一语义。

### 4.4 OXI / VIA / Datavyu：局部 UI 与数据模型参考

OXI 证明纯客户端可流畅进行 active/reference series 切换、brush 选段和多 series 同步标记，但其实际存储是逐样本单标签 CSV。([OXI-HELP](#source-oxi-help)) 本项目应保留区间/边界为真值，brush 只是一种生成边界的交互，不能改成逐样本真值。

VIA 的内部 attribute ID 与展示值分离、Project JSON 带 data format version，值得借鉴。([VIA-CODE](#source-via-code)) 但其属性删除会删除 metadata 值，因此“有 ID”本身仍不够，必须让发布版本不可变。

Datavyu 的列/cell/onset/offset 和 temporal alignment 适合解释多轨道重叠状态；Ruby scripts 也证明把批量校验/导出放在 API 层而非 GUI 层更利于复现。([DAT-SPREAD](#source-dat-spread)) ([DAT-EXPORT](#source-dat-export))

### 4.5 CVAT / Argilla：只借分层，不借产品形态

CVAT 把自动标注、导入、导出、质量分析拆成独立 worker，适合提醒本项目把 recognizer、renderer、exporter、QA 从界面编排中解耦。([CVAT-ARCH](#source-cvat-arch)) 但单机程序无需部署消息队列和多服务拓扑；一个本地 Python 进程内的应用模块，再通过 localhost 接口供浏览器调用即可。

Argilla 明确区分 model `suggestions` 与人工 `responses`。([ARG-RECORD](#source-arg-record)) 本项目应同样保存“算法提出了什么”和“正式标注是什么”，并把接受、修改、删除自动事件记录为审计动作，不能仅用最终 CSV 的 `source` 字段猜历史。

## 5. 标准与配置模式

### 5.1 JSON Schema 2020-12

JSON Schema 用于定义 JSON 结构、验证和文档，并用 `$schema` 声明 dialect、`$id` 标识 schema resource。([STD-JSON](#source-std-json)) 推荐用它约束 `project.json` 引导清单、HTTP JSON 消息和便携导出；SQLite 内部表结构由数据库 migration 与领域校验共同约束，不能为了“可读”再维护一套同权威的 JSON 数据副本。

### 5.2 W3C PROV-O

PROV-O 的起点是 Entity、Activity、Agent，并定义 `wasGeneratedBy`、`wasDerivedFrom`、`wasAttributedTo`、`wasRevisionOf` 等关系。([STD-PROV](#source-std-prov)) 本项目不需要 RDF/OWL，但审计字段应能回答同样的问题：哪个用户/算法，在何时，以哪个 schema/detector/input hash，执行了什么动作，生成或修订了哪个 annotation revision。

### 5.3 ASAM OpenLABEL

ASAM OpenLABEL 是多传感器数据标注和场景 tagging 的 JSON schema/文件格式，官方目标还包括通过可扩展描述语言吸收标签演进并保持 backward compatibility。([STD-OPENLABEL](#source-std-openlabel)) 它主要面向自动驾驶对象、场景和坐标系，直接采用会把本项目拖入不需要的对象/3D 模型。建议只在未来需要跨工具交换时增加可选 exporter；内部模型保持步态领域的 track/boundary 语义。

## 6. 推荐目标架构

```text
系统浏览器中的 TypeScript 前端
        │  同源 HTTP/JSON；只发送领域命令，不直接读写项目文件
        ▼
localhost Python Host
├── HTTP + 静态资源适配器
├── Application
│   ├── Project / Trial / LabelSchema
│   ├── EditAnnotation / PublishSchema
│   ├── RunBuiltInV2Recognizer / RunQA
│   └── ImportLegacy / ExportCompatibility
├── 固定步态传感器输入适配器
├── 内置 V2 识别器适配器
└── SQLite Repository
        │
        ▼
项目目录：project.json + project.sqlite3 + exports/
原始 CSV：可位于项目外，由项目保存路径、hash 和显式试次元数据
```

这是一个单进程本地应用，不是可部署到局域网的服务器。宿主只绑定 `127.0.0.1`（需要 IPv6 时另行显式处理 `::1`），不监听 `0.0.0.0`；IPv4 与 IPv6 标准都规定对应回环地址不得离开本机。([NET-LOOPBACK](#source-net-loopback)) 同时校验固定 Host、精确 Origin，并要求修改请求携带启动会话/CSRF 令牌；CORS 与浏览器的 local-network 限制都不能代替服务端授权。([WEB-LOCAL](#source-web-local)) 前端静态资源与 JSON 接口由同一宿主提供，避免额外的跨域配置。默认启动命令拉起 Python 宿主并打开系统浏览器，不引入 Electron；PySide6 只作为迁移期对照实现，达到 Web 等价验收后退出。

### 6.1 固定输入契约与可配置标签模式

必须把两类“硬编码”分开处理：

- **继续固定**：步态传感器列名、单位、派生通道与 V2 识别器所需输入。这是当前数据/算法契约，改成任意通道配置会削弱验证边界。
- **移出硬编码**：活动、地形的标签 code、显示名、颜色、说明、启停状态和合法组合。项目模式始终恰好包含 `activity` 与 `terrain` 两条轨，用户不能新增或删除轨，但可以手动新增、修改和停用轨内标签并编辑组合矩阵。
- **与文件名解耦**：subject、session、trial 等身份由项目登记，不再要求从文件名推断；文件名只保留为来源信息。每个外部原始 CSV 登记规范化路径、内容 SHA-256 和显式元数据。

`LabelSchemaVersion` 保存稳定 `schema_id`、整数 `version`、内容 hash、两轨标签及组合规则。草稿可编辑；发布后不可变，任何增改或停用均发布下一版本。正式 `AnnotationRevision` 固定引用一个 schema version、原始 CSV hash 和父修订；保存、迁移或接受识别结果都新建修订，不更新旧记录。

### 6.2 SQLite 与文件树的取舍

| 方案 | 优点 | 主要代价 | 本项目判断 |
|---|---|---|---|
| JSON/CSV 文件树为权威存储 | 人可读、易做局部导出、无需数据库 migration | schema、标注修订、审计和当前指针跨多个文件，难以一次原子提交；唯一性与引用完整性需要重复实现 | 继续用于清单与兼容导出，不作为内部权威状态 |
| 单个 SQLite 项目库 | 一个事务可同时写入不可变修订、审计事件和 head 指针；可用唯一约束、外键和一致性检查；适合单机单写者 | 不适合手工编辑；需要数据库 migration、备份和完整性检查 | **推荐作为内部权威存储** |
| SQLite + JSON 双写为双权威 | 兼顾查询与表面可读性 | 一致性、恢复和版本升级复杂度最高 | 拒绝；JSON 只能是可再生成的清单/导出 |

SQLite 官方文档明确：数据库同一时刻只有一个写事务，事务修改具备全有或全无的原子性；这与本项目“一个宿主、一个写者、短事务串行提交”的约束一致。([SQLITE-TXN](#source-sqlite-txn))

建议项目布局：

```text
project-root/
├── project.json          # 小型引导清单：格式版本、项目 ID、数据库文件名
├── project.sqlite3       # 权威状态：项目、schema 版本、试次、修订、审计、草稿
└── exports/              # 可再生成的七列 CSV、报告与便携清单
```

同一个事务写入 annotation revision、边界快照/增量、审计命令与新的 head 指针，失败则全部回滚。一个 Python 进程持有项目写锁，应用层串行执行写命令；每个命令带调用方看到的 `expected_revision_id`，旧浏览器标签页提交时明确报冲突。人员交接采用“关闭当前写者后再由下一人打开项目”的顺序交接，不做实时协作，也不依赖 SQLite 来解决多写者协调。

Phase 1 默认使用短事务与 `synchronous=FULL`，先不为理论并发收益启用 WAL；若真实长序列基准证明读写互阻成为问题，再评估 WAL、checkpoint 和 SQLite 运行时版本。WAL 仍然只有一个写者，且需要同机共享内存语义。([SQLITE-WAL](#source-sqlite-wal)) 项目关闭时可以直接复制已关闭数据库；在线备份必须使用 SQLite Online Backup API 取得一致快照，不直接复制正在写入的数据库文件。([SQLITE-BACKUP](#source-sqlite-backup))

### 6.3 兼容边界

内部 SQLite 格式可以不具备人工可编辑性，但以下外部契约在 Phase 1 冻结：

- 七列 annotation CSV 的列名、字段语义和排序不变；它是兼容导入/导出格式，不再是项目权威状态，也不单独承担 schema 语义。
- `youbu-annotation`、`youbu-auto-annotate`、`youbu-annotation-aid`、`youbu-visualize` 四个命令继续可用；前两个内部转接新 application interface，编辑器命令改为启动 localhost 并打开浏览器。
- 现有 V2 识别行为用冻结语料锁定。首版只有一个内置识别器实现，藏在内部 interface 后；不做第三方插件发现、安装或兼容承诺。
- 内置识别器只对其声明支持的默认 V2 schema 运行。自定义标签模式仍可人工标注，但不能静默把未知标签映射成 V2 自动结果。

通用 schema QA 负责未知标签、非法组合、时间顺序和相邻重复状态；V2 QA 负责协议、证据和算法阈值。浏览器只提交命令与显示结果，不复制这两层领域规则。

## 7. Phase 1 实现方案

Phase 1 只完成架构迁移、项目/模式能力和 Web 等价，不同时开展插件体系、多人协作、Electron/AppImage、Linux 专用安装包或新识别算法。

### 7.1 契约冻结

- 给现有 V2 语料建立 golden tests，冻结七列 CSV、报告关键语义、四个 CLI 的参数/退出码，以及编辑、QA、恢复的领域结果。
- 把固定传感器列契约与默认 V2 标签集合分别做成显式 fixture，防止重构时把“输入契约固定”误改成“标签继续写死”。

### 7.2 领域与项目内核

- 提取与 UI 无关的 domain/application interface；PySide6 和新 HTTP 适配器在迁移期调用同一组命令。
- 建立 SQLite migration、项目锁和 repository；导入外部原始 CSV 时记录 hash 与显式 subject/session/trial，不再依赖文件名作为身份主键。
- 实现固定双轨的 schema draft/publish：标签可手动新增、修改、停用，合法组合可编辑；发布版本不可变。
- 正式保存改为生成不可变 annotation revision；七列 CSV 和报告由指定修订导出。

### 7.3 本地服务与 TypeScript 前端

- Python 宿主只开放 loopback，同源提供前端资源和按领域命令设计的 JSON 接口；长时间识别任务支持取消和明确状态，但首期仍在单进程内执行。
- TypeScript 前端复现当前核心工作流：打开/登记试次、联动信号浏览、光标吸附、边界/区间增删改、撤销重做、QA 闭环、复核、恢复、导出和模式编辑。
- 前端采用已确认的 React + TypeScript + Vite；绘图库在接入真实长序列前用性能原型确定，避免在缺少基准时锁定不合适的渲染方案。

### 7.4 等价验收与切换

Phase 1 完成需同时满足：

1. 冻结 V2 语料经旧实现和新 application path 生成的正式七列 CSV 逐字段等价，QA 与识别边界不发生未经批准的变化。
2. 四个 CLI 的兼容测试全部通过；原有脚本不需要理解 SQLite。
3. 项目关闭重开后，外部 CSV 路径/hash、显式试次元数据、schema 版本和 annotation head 一致；源文件变化会阻断正式保存。
4. 用户能在浏览器中新增标签、修改显示属性、停用标签和编辑合法组合；已发布 schema 及旧 annotation revision 均不可原地改写。
5. 两个浏览器标签页基于同一旧 revision 提交时，后提交者收到可恢复的版本冲突；进程崩溃测试不会留下半个修订或无审计的 head。
6. 服务不绑定非 loopback 地址，断网环境可完成全部核心工作流；项目目录不需要 Electron、AppImage 或 Linux 专用包才能打开。
7. Web 工作流、键盘操作、长序列交互和恢复达到现有 PySide6 基线后，才移除 PySide6 界面；在此之前它只承担迁移对照，不继续发展新功能。

## 8. 已确认决策与延后项

已确认：

1. 终态为本地 Python 服务 + TypeScript 系统浏览器，服务只绑定 localhost；PySide6 仅保留到 Web 等价。
2. 恰好两条轨：活动与地形。轨内标签及组合规则可手动添加、修改、停用，不支持任意轨。
3. 步态传感器列契约继续硬编码；subject/session/trial 与文件名解耦，由项目显式登记。
4. 项目目录引用外部原始 CSV 并保存 hash；内部权威状态用 SQLite，JSON/CSV 用作清单、兼容输入输出或可再生成报告。
5. 单写者、顺序交接；schema 版本与正式 annotation revision 都不可变。
6. 七列 CSV 和四个 CLI 保持兼容；内置 V2 识别行为冻结，并通过内部 interface 接入。
7. 首版不做第三方插件、实时多人协作、Electron/AppImage 或 Linux 专用发布。

延后到 Phase 1 之后：跨 schema 的批量迁移工作台、第三方识别器扩展、多人协作、便携 bundle 与新的部署包装。它们不得反向扩大 Phase 1 interface。

## 9. 来源

以下来源均检索于 **2026-08-17**。

<a id="source-ls-gh"></a>**LS-GH** — [GitHub Repository API: HumanSignal/label-studio](https://api.github.com/repos/HumanSignal/label-studio)

<a id="source-ls-readme"></a>**LS-README** — [Label Studio official README](https://github.com/HumanSignal/label-studio/blob/develop/README.md)

<a id="source-ls-ts"></a>**LS-TS** — [Label Studio: Time Series Labeling](https://labelstud.io/templates/time_series.html)；[TimeSeries tag](https://labelstud.io/tags/timeseries.html)

<a id="source-ls-config"></a>**LS-CONFIG** — [Label Studio: Configure labeling interface](https://labelstud.io/guide/setup)

<a id="source-ls-pred"></a>**LS-PRED** — [Label Studio: Import pre-annotated data](https://labelstud.io/guide/predictions)

<a id="source-ls-compare"></a>**LS-COMPARE** — [Label Studio Community and Enterprise feature comparison](https://labelstud.io/guide/label_studio_compare)

<a id="source-boris-gh"></a>**BORIS-GH** — [GitHub Repository API: olivierfriard/BORIS](https://api.github.com/repos/olivierfriard/BORIS)

<a id="source-boris-readme"></a>**BORIS-README** — [BORIS official README](https://github.com/olivierfriard/BORIS/blob/master/README.md)

<a id="source-boris-stack"></a>**BORIS-STACK** — [BORIS pyproject.toml](https://github.com/olivierfriard/BORIS/blob/master/pyproject.toml)

<a id="source-boris-project"></a>**BORIS-PROJECT** — [BORIS User Guide: Create a project](https://www.boris.unito.it/user_guide/create_project/)

<a id="source-boris-obs"></a>**BORIS-OBS** — [BORIS User Guide: Create an observation](https://www.boris.unito.it/user_guide/observations/)

<a id="source-boris-analysis"></a>**BORIS-ANALYSIS** — [BORIS User Guide: Analysis](https://www.boris.unito.it/user_guide/analysis/)

<a id="source-boris-pref"></a>**BORIS-PREF** — [BORIS User Guide: Preferences](https://www.boris.unito.it/user_guide/preferences/)

<a id="source-oxi-gh"></a>**OXI-GH** — [GitHub Repository API: kplabs-pl/OXI](https://api.github.com/repos/kplabs-pl/OXI)

<a id="source-oxi-readme"></a>**OXI-README** — [OXI official README](https://github.com/kplabs-pl/OXI/blob/main/README.md)

<a id="source-oxi-package"></a>**OXI-PACKAGE** — [OXI package.json](https://github.com/kplabs-pl/OXI/blob/main/package.json)；[server.js](https://github.com/kplabs-pl/OXI/blob/main/server.js)

<a id="source-oxi-index"></a>**OXI-INDEX** — [OXI client-side upload source](https://github.com/kplabs-pl/OXI/blob/main/src/views/Index.vue)

<a id="source-oxi-labeler"></a>**OXI-LABELER** — [OXI label editor source](https://github.com/kplabs-pl/OXI/blob/main/src/views/Labeler.vue)

<a id="source-oxi-help"></a>**OXI-HELP** — [OXI format/recovery/export help source](https://github.com/kplabs-pl/OXI/blob/main/src/views/Help.vue)

<a id="source-dat-gh"></a>**DAT-GH** — [GitHub Repository API: databrary/datavyu](https://api.github.com/repos/databrary/datavyu)

<a id="source-dat-readme"></a>**DAT-README** — [Datavyu official README](https://github.com/databrary/datavyu/blob/master/README.md)

<a id="source-dat-spread"></a>**DAT-SPREAD** — [Datavyu docs: Spreadsheet overview](https://github.com/databrary/datavyu-docs/blob/master/source/guide/spreadsheet.txt)

<a id="source-dat-code"></a>**DAT-CODE** — [Datavyu docs: Configure columns and codes](https://github.com/databrary/datavyu-docs/blob/master/source/guide/tutorials/configure-datavyu-codes.txt)

<a id="source-dat-rel"></a>**DAT-REL** — [Datavyu docs: Inter-rater reliability](https://github.com/databrary/datavyu-docs/blob/master/source/best-practices/checks/reliability.txt)

<a id="source-dat-export"></a>**DAT-EXPORT** — [Datavyu docs: Export data](https://github.com/databrary/datavyu-docs/blob/master/source/guide/tutorials/export-data.txt)；[Scripting API](https://github.com/databrary/datavyu-docs/blob/master/source/api/introduction-to-scripting.txt)

<a id="source-via-gh"></a>**VIA-GH** — [GitHub Repository API: ox-vgg/via](https://api.github.com/repos/ox-vgg/via)

<a id="source-via-readme"></a>**VIA-README** — [VIA 3 official README](https://github.com/ox-vgg/via/blob/master/via-3.x.y/README.md)

<a id="source-via-code"></a>**VIA-CODE** — [VIA 3 code documentation and Project JSON](https://github.com/ox-vgg/via/blob/master/via-3.x.y/CodeDoc.md)；[data model source](https://github.com/ox-vgg/via/blob/master/via-3.x.y/src/js/_via_data.js)

<a id="source-inc-gh"></a>**INC-GH** — [GitHub Repository API: inception-project/inception](https://api.github.com/repos/inception-project/inception)

<a id="source-inc-readme"></a>**INC-README** — [INCEpTION official README](https://github.com/inception-project/inception/blob/main/README.md)

<a id="source-inc-install"></a>**INC-INSTALL** — [INCEpTION single-user installation source](https://github.com/inception-project/inception/blob/main/inception/inception-doc/src/main/resources/META-INF/asciidoc/user-guide/intro-installation.adoc)

<a id="source-inc-guide"></a>**INC-GUIDE** — [INCEpTION 41.3 User Guide](https://inception-project.github.io/releases/41.3/docs/user-guide.html)

<a id="source-inc-backup"></a>**INC-BACKUP** — [INCEpTION internal annotation backup source](https://github.com/inception-project/inception/blob/main/inception/inception-doc/src/main/resources/META-INF/asciidoc/admin-guide/settings_internal-backup.adoc)

<a id="source-cvat-gh"></a>**CVAT-GH** — [GitHub Repository API: cvat-ai/cvat](https://api.github.com/repos/cvat-ai/cvat)

<a id="source-cvat-arch"></a>**CVAT-ARCH** — [CVAT architecture](https://docs.cvat.ai/docs/administration/community/advanced/cvat-architecture/)

<a id="source-cvat-project"></a>**CVAT-PROJECT** — [CVAT Projects](https://docs.cvat.ai/docs/workspace/projects/)

<a id="source-cvat-auto"></a>**CVAT-AUTO** — [CVAT automatic annotation](https://docs.cvat.ai/docs/annotation/auto-annotation/automatic-annotation/)

<a id="source-cvat-qa"></a>**CVAT-QA** — [CVAT quality control and edition availability](https://docs.cvat.ai/docs/qa-analytics/quality-control/)

<a id="source-cvat-formats"></a>**CVAT-FORMATS** — [CVAT dataset formats](https://docs.cvat.ai/docs/dataset_management/formats/)

<a id="source-arg-gh"></a>**ARG-GH** — [GitHub Repository API: argilla-io/argilla](https://api.github.com/repos/argilla-io/argilla)

<a id="source-arg-readme"></a>**ARG-README** — [Argilla official README](https://github.com/argilla-io/argilla/blob/main/README.md)

<a id="source-arg-dataset"></a>**ARG-DATASET** — [Argilla: Create, update and delete datasets](https://docs.argilla.io/latest/how_to_guides/dataset/)

<a id="source-arg-record"></a>**ARG-RECORD** — [Argilla: Add, update and delete records](https://docs.argilla.io/latest/how_to_guides/record/)

<a id="source-std-json"></a>**STD-JSON** — [JSON Schema Draft 2020-12 Core](https://json-schema.org/draft/2020-12/json-schema-core.html)

<a id="source-std-prov"></a>**STD-PROV** — [W3C Recommendation: PROV-O](https://www.w3.org/TR/prov-o/)

<a id="source-std-openlabel"></a>**STD-OPENLABEL** — [ASAM OpenLABEL official standard page](https://www.asam.net/standards/detail/openlabel/)

<a id="source-sqlite-txn"></a>**SQLITE-TXN** — [SQLite transactions](https://www.sqlite.org/lang_transaction.html#read_transactions_versus_write_transactions)；[SQLite is Transactional](https://www.sqlite.org/transactional.html)；[Atomic Commit](https://www.sqlite.org/atomiccommit.html#_introduction)

<a id="source-sqlite-wal"></a>**SQLITE-WAL** — [SQLite WAL concurrency](https://www.sqlite.org/wal.html#concurrency)；[WAL overview and limitations](https://www.sqlite.org/wal.html#overview)；[PRAGMA synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous)

<a id="source-sqlite-backup"></a>**SQLITE-BACKUP** — [SQLite Online Backup API](https://www.sqlite.org/backup.html#using_the_sqlite_online_backup_api)

<a id="source-net-loopback"></a>**NET-LOOPBACK** — [RFC 1122, IPv4 internal host loopback](https://www.rfc-editor.org/rfc/rfc1122.html#section-3.2.1.3)；[RFC 4291, IPv6 loopback address](https://www.rfc-editor.org/rfc/rfc4291.html#section-2.5.3)

<a id="source-web-local"></a>**WEB-LOCAL** — [W3C Secure Contexts: localhost](https://www.w3.org/TR/secure-contexts/#localhost)；[Fetch Standard: CORS protocol](https://fetch.spec.whatwg.org/#http-cors-protocol)；[Local Network Access: goals and CSRF scope](https://wicg.github.io/local-network-access/#goals)
