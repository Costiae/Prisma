# Prisma 资源目录

## 按章制作 Scenario

从 Scenario 节点的「按章制作」读取保存过的原文章节。程序保留原文字句和行号，模型输出编号锚定的场景、说话人、视角、表情、音乐和事件 CG 标注，以及角色的叙事分级。成功批次按输入、模型和参数记录，可用同样参数接着处理。已生成的制作稿从脚本 IDE 审查。源树中的 `production/scenario.json` 是可移植 Scenario，`production/staging.json` 保留演出标注与角色分级，Ren’Py 源码把标注放在对应台词或叙述之前。

作品风格、角色设计、场景与事件 CG 声明属于当前项目。背景提示词结合场景声明和作品风格。同一场的音乐切换和事件 CG 都保留原文起点和理由。CG 只为有代表性、情绪强烈且有画面表现力的瞬间声明，制作提示词结合事件构图和 CG 整体风格。角色仅短暂出场且对主线影响有限时，可标为次要角色。次要和待定角色保留对白与角色记录，制作名单只包含明确进入立绘流程的角色。

`scenario_production_review` 的 `cg` 是当前制作稿的完整 CG 列表，每条填写原文行 `at`、`title`、`composition` 和 `reason`。省略 `cg` 会沿用已有标记，传空列表可清空。通过 `character_roles` 按已登记名称填写 `role` 和 `reason`，分级允许 `primary`、`secondary`、`unreviewed`。发布后可用 `scenario_production_registry` 的 `cg-design` 查看 CG 声明，或用 `character-design` 加 `portraits_only: true` 读取立绘制作名单。在验证一份模型制作稿后，可用 `scenario_production_defaults_save` 保存该次模型和实际参数。

「场景背景」传出背景提示词和场景 BGM 需求。「背景生成」用 `pipeline_inputs` 读取已通过的场景背景，或从 `draft_scene_background` 选用场景草稿。通过 `scenario_background_prompt` 载入提示词，把返回的 `prompt_id` 随 `image_generate` 提交。背景生成和事件 CG 节点通过时须关联已可用的图片文件。「音乐生成」的 `pipeline_inputs` 提供整体曲风、场景 BGM 和角色曲参考。场景 BGM 草稿可以在背景出图前试用。

每个 Prisma 工作目录有自己的 `catalog.sqlite3`，默认位于 `build/prisma-images/`。项目、剧本、演出绑定、提示词、素材、工具、模型、训练数据、任务和导出使用同一个目录。SQLite 保存可搜索的记录和关系，任务的完整参数及日志可通过任务 ID 查看。

每条资源有一个可空的 `project_id`。打开项目后，资源目录和任务历史按项目查询。底模和工具使用共享目录，图像工作室的训练和生成选择器也可使用共享数据集与 LoRA。将共享素材加入项目或另存项目时，会登记该项目的素材记录。相同的媒体内容共用原文件。

## 浏览与维护

- 在左侧打开「素材」，下方的「资源目录」可在当前项目和共享库之间切换，支持按类别、名称、ID、路径或参数搜索。打开记录可查来源和引用。手动放入 Prisma 可发现目录的资源可用「重新扫描本机目录」登记。
- 在「项目」创建工作区并导入原文；打开项目后编辑会自动写入版本。「另存为新项目」可以从已打开的内容开始一个新项目。
- 项目卡片的「删除项目」将项目移入回收站。在回收站点「恢复项目」可以找回原项目、历史和文件。对应 MCP 工具为 `project_list`、`project_delete` 和 `project_restore_deleted`。
- 打开项目后编辑内容会自动写入版本；顶部提供「写入」「历史记录」和「撤回」。历史版本可以恢复，恢复会新建一个版本以保留完整来路。MCP 提供 `project_history`、`project_undo` 和 `project_restore`。
- 项目工作区的「项目配置」按用途设置文本模型、图像模型和训练底模。文本模型是项目全局 LLM；每个 LLM 任务默认继承它，只有任务的 `llm_overrides.<task>.enabled` 为 `true` 时才合并该任务的 `settings` 覆盖。当前 LLM 调用是 `scenario`。图像生成可分别配置本地底模与在线模型，LoRA 训练有独立训练底模。其他参数包括 LLM Temperature/Token 上限/请求超时/系统提示词、图像 LoRA/权重/尺寸/采样器/步数/CFG/Seed/候选数、训练数据集/续训来源/步数/保存间隔/Rank/学习率/重复次数/Seed、Ren’Py 输出分辨率与默认字体，以及资产提示词默认风格和负面提示词。图像与训练设置会作为该项目新任务的默认值；MCP 可在 `project_save` 的 `config` 中保存这些分组，未传入的字段使用默认值。
- 「API Provider」集中维护连接。OpenAI 兼容接入 Chat Completions、图像生成和语音合成，Anthropic 兼容接入 Messages。域名后没有路径时使用 `/v1`，有路径时将其作为网关的 API 前缀。Fetch Models 保存 `/models` 返回的 metadata。目录不可用或用途识别有误时，可手动声明或编辑模型。连接测试检查模型接口权限。
- 选中服务商后可直接搜索模型并查看能力。思考、多模态、图像／音频／视频输入、工具调用和结构化输出读取 API metadata；缺失时标为未知，可在「详情」手动修正并保留原始 metadata。每个模型的「发送测试」发出一次实际请求，可能消耗额度。最近一次结果记录接口、耗时、HTTP 状态及服务商返回的用量（若提供）。
- 页面下方可分别保存工作区文本、图像和音频默认模型。节点选择 Provider 时默认继承对应模型，也可在节点内选择其他已声明的 Provider 和模型。改选保存在当前浏览器会话，按项目和节点隔离。试跑配置不会写回项目默认参数，运行记录保存实际用到的模型和参数。配音节点的本地路径用于导入已生成的音频。
- Provider、Key、工作区默认和最近一次模型测试保存在 `catalog.sqlite3`。WebUI 和 MCP 共用 `api_providers`、`api_provider_save`、`api_provider_delete`、`api_provider_models`、`api_provider_model_save`、`api_provider_model_test`、`api_provider_test`、`api_model_defaults_save` 和 `speech_generate`。旧项目仍可读取已保存的 API 地址，Key 保存在工作区。
- 原文转写、稳定台词 ID、手动保存 Scenario 与演出绑定、场景提示词和出图都会登记。图像任务保留实际使用的提示词及所选底模、LoRA 和项目。R1–R3 的旧 LoRA 数据关系来自工作目录配置的 `lora-transfer.json`。
- 上传的图片、音频和字体按 SHA-256 存在 `media/`。复用生成图时引用任务原件。导出登记实际使用的 Scenario、演出绑定和素材，数据集、LoRA 和 Ren’Py 导出都可在资源目录找到。

## 脚本工作台

在「剧本」中点选一行，再点「为此句安排演出」，会打开「演出」并定位到相应台词。也可在「演出」直接生成脚本预览，再点选代码中的台词。右侧可预览当前项目的图片或试听音频，插入或替换演出动作。应用后会更新演出绑定和脚本预览。

侧栏一级入口「脚本 IDE」打开当前项目的全量工作区。路线图仍保留脚本节点、直接上下游和通过记录，两处读取同一个工程草稿。未选项目时，先选定入口再打开项目。窄屏用「项目导航」展开入口。

从「演出绑定」点「在脚本 IDE 中编辑」，会在同一工作台打开当前项目。已有工程则打开原源码，首次使用时可把生成的演出预览载为草稿。带 `project` 的页面地址可以在刷新或新标签页中重新打开同一个项目和节点。

「工程」菜单可以创建可运行的起始工程、从 Scenario 生成 `script.rpy`，或导入整个 Ren’Py 工程及素材。工程树以 `game/` 为根，保留导入的相对路径。「源码」支持多标签、语法高亮、缩进、撤销、查找替换、符号跳转和全工程搜索。素材栏可预览媒体并插入已通过资产。「保存全部」或 `Ctrl+S` 保存整份源码与素材映射。

- 「剧情」可跨文件编辑场景、角色、台词、选项、跳转和演出素材，或跳到对应的源码。自定义 Ren’Py 逻辑仍可在源码中编辑。「分支」显示可识别的静态去向。「剧情预览」从 `start` 或剧情页当前场景开始。
- 「界面」可拖动文本、按钮、图片和色块，调整尺寸、可见条件和按钮动作，也可将一个界面作为组件复用。变量可绑定到控件文本，由按钮更新。界面和变量分别写入 `prisma_ui.rpy` 和 `prisma_variables.rpy`。手写或导入的界面可直接编辑源码。
- 「本地化」用 SDK 生成 `tl/<language>/` 翻译模板，载入后作为源码修改。语言素材替换保留原资源路径，并由 Ren’Py 在运行时查找对应语言的替换文件。
- 「版本」可查看每次保存后的源码与素材差异，把历史版本载成新草稿。「工程 → 合并最新版本」会合并分别修改的文件，并逐个询问有冲突的文件要保留哪份内容。
- 「发布」可以在同一工作区检查、通过和下载工程 ZIP。通过记录会把脚本版本交给路线图的导出节点。启动 Prisma 时用 `PRISMA_RENPY_SDK` 指定 SDK 目录，可运行原生 Lint、实机试玩并构建 Windows、Linux 或 macOS 发行包。安装 SDK 的 Web 支持后也会提供 Web 选项。
- 发行变体在 `prisma-variants.json` 中定义截止场景和排除路径。构建任务保留所用版本、目标平台、变体和日志。需要签名更新时，用 `PRISMA_RENPY_UPDATE_KEYS` 指定密钥目录。SDK 从目录读取该项目的 `<project_id>.pem` 并产出独立更新 ZIP。构建不会把更新包上传到外部。 [Ren’Py 命令行用法](https://www.renpy.org/doc/html/cli.html)

MCP 用 `script_editor_open` 读取草稿或指定 `revision`，用 `script_editor_save` 保存 `files`、`project_assets`、`managed_assets` 和所基于的 `base`。`files` 把相对路径映射到源码，`project_assets` 把相对路径映射到已登记的素材 ID。旧版 `text` 参数仍可更新 `script.rpy`。`script_editor_check` 检查工程。在脚本节点通过保存的版本后，`renpy_export` 接受 `script_id`，`renpy_sdk_start` 接受 `revision` 和 `mode`，两者均需 `project_id`。

`script_editor_story` 读取全工程的场景与分支，`script_story_edit` 可用 `files` 和 `file` 编辑支持的跨文件跳转，也兼容原来的单文件 `text`。调用 `script_editor_screens` 和 `script_editor_variables` 生成界面与变量源码，`script_editor_history` 和 `script_editor_diff` 读取保存记录和差异，`script_editor_merge` 可预览草稿合并结果。用 `script_editor_variants` 读取或写出变体配置。构建、翻译任务通过 `renpy_sdk_start` 提交，用 `image_job` 查询状态，分别用 `renpy_sdk_builds` 和 `renpy_sdk_translations` 获取结果。

CodeMirror 构建产物随仓库提供，启动 WebUI 时直接加载。修改编辑器引擎后，可在 `web/editor-build/` 运行 `npm ci --ignore-scripts` 和 `npm run build` 重新生成浏览器包及许可证清单。

## 项目路线图

每个路线图节点有独立工作台。工作台顶部用小图显示当前节点和直接上下游，点击邻接节点即可切换。中间显示工具、参数、当前结果和节点版本。项目中已导入的原文会直接出现在原文节点。

底部「重试」用当前参数重新运行本节点，或回到本节点手动调整。「通过」把当前结果自动传给所有直接下游。路线图以颜色区分未完成、进行中、已完成和已跳过。一个节点只有在所有直接下游都已完成、输入版本仍有效且没有使用该节点的结果时才显示「已跳过」。

在「LoRA 训练」或「本地模型与工具」中可加载或导入已有 LoRA，然后直接进入本地出图。实际生成需要当前设备支持对应的底模和推理环境。MCP 用 `lora_import` 登记文件，用 `lora_prepare` 获取本地生成配置和环境状态。

下游只接收已通过且选中本节点的上游版本。更新输入后，旧结果不会自动流入下游。每个版本记录所属项目、使用的上游版本和产物引用，可从资源目录追溯来源。

在仓库根目录的 `pipeline-nodes.json` 注册节点。每个节点有稳定的 `id`、名称、输入与产物说明，通过 `panels` 指定工具面板，通过 `retry_control` 指定重试触发的操作。左侧「路线图」下按注册顺序列出所有节点，包括规划中的节点和参考输入。修改线路只需改目标节点的 `depends_on`。有意的回流用 `{"node":"train","feedback":true}` 表示。重启 Prisma 后，前端根据依赖生成节点、箭头和产物表，旧版项目的节点历史仍可读取。

MCP 可用 `pipeline_state` 查看拓扑和当前版本，`pipeline_inputs` 获取指定节点已通过的上游版本，`pipeline_reusable` 查找可复用产物。`pipeline_action` 与 WebUI 共用节点操作，支持 `retry` 和 `pass`。更新节点时传最近版本的 `id` 作为 `base`，通过时传入当前有效的上游版本 ID，避免把旧输入的结果误传给下游；通过后的版本自动传给所有直接下游。

## 已通过资产

打开项目后，从侧栏进入「已通过资产」。这里集中展示各节点当前通过的成果，可以按节点筛选或按文件名、资源 ID 搜索。点选成果可查看版本和原有资源，预览图片、试听音频或读取文档。素材快照会列出当时选用的文件。直接上游版本发生变化时，该成果标为「待复核」。选择「退回修改」会停止传递该版本，并保留版本记录。

MCP 用 `pipeline_approved` 读取同一份列表，传 `project_id`，也可加 `node` 读取单个节点的完整说明。选择资源后用 `library_item` 查看详情。退回可用 `pipeline_action` 的 `retry`，并将当前版本 ID 传给 `base`。

## MCP

| 工具 | 参数示例 |
| --- | --- |
| `library_list` | `{"kind":"lora","query":"prisma","limit":20,"offset":0}` |
| `library_list`（按项目） | `{"project_id":"<项目 ID>","kind":"asset"}` |
| `library_item` | `{"kind":"lora","id":"<查询返回的 ID>"}` |
| `library_refresh` | `{}` |
| `project_save` | 传入 `title`、`scenario_draft` 返回的 `scenario` 对象和可选的 `assets` |
| `project_open` | `{"id":"<项目 ID>"}` |
| `asset_import` | `{"library_id":"<已有素材 ID>"}` |
| `scenario_save` | 传入 Scenario JSON 和可选的 `cues` |
| `script_preview` | 传入 Scenario JSON 和可选的 `asset_ids` |
| `script_bind` | 传入预览使用的 Scenario、cues 和 asset_ids，再指定 `scene`、`index` 与 `cue` |

保存已有项目时，传入项目 `id` 及上次返回的 `revision`，避免覆盖其他设备或客户端保存的新版本。`image_generate` 支持 `prompt_id` 和 `project_id`。`renpy_export` 可以通过 `asset_ids` 传入文件名到素材 ID 的映射。现有 `image_job`、`image_artifact` 等入口仍可读取完整任务和文件。

命令行的 `prisma.py draft` 和 `prisma.py build` 也登记输入与产物。可用 `python prisma.py --workspace <目录> draft ...` 选择目标资源目录。

## 迁移与恢复

首次打开工作目录会导入已有记录。旧数据库升级时会保留已有项目和可明确归属的记录，无项目归属的历史资料进入共享库。项目版本和文档的可迁移清单在 `records/`，复用的上传素材在 `media/`。训练任务、数据集和导出继续使用各自的清单。迁移整个项目工作目录时把所需的 `records/`、`media/`、任务及数据集一并带走，SQLite 可由这些清单重建。LoRA 迁移 ZIP 仍可单独解压为工作目录。工作目录外的本机共享模型和历史引用按本机路径登记。
