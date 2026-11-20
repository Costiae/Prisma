# Prisma 资源目录

每个 Prisma 工作目录有自己的 `catalog.sqlite3`，默认位于 `build/prisma-images/`。项目、剧本、演出绑定、提示词、素材、工具、模型、训练数据、任务和导出使用同一个目录。SQLite 保存可搜索的记录和关系，任务的完整参数及日志可通过任务 ID 查看。

每条资源有一个可空的 `project_id`。打开项目后，资源目录和任务历史按项目查询。底模和工具使用共享目录，图像工作室的训练和生成选择器也可使用共享数据集与 LoRA。将共享素材加入项目或另存项目时，会登记该项目的素材记录。相同的媒体内容共用原文件。

## 浏览与维护

- 在左侧打开「素材」，下方的「资源目录」可在当前项目和共享库之间切换，支持按类别、名称、ID、路径或参数搜索。打开记录可查来源和引用。手动放入 Prisma 可发现目录的资源可用「重新扫描本机目录」登记。
- 在「项目」创建工作区并导入原文；打开项目后编辑会自动写入版本。「另存为新项目」可以从已打开的内容开始一个新项目。
- 项目卡片的「删除项目」将项目移入回收站。在回收站点「恢复项目」可以找回原项目、历史和文件。对应 MCP 工具为 `project_list`、`project_delete` 和 `project_restore_deleted`。
- 打开项目后编辑内容会自动写入版本；顶部提供「写入」「历史记录」和「撤回」。历史版本可以恢复，恢复会新建一个版本以保留完整来路。MCP 提供 `project_history`、`project_undo` 和 `project_restore`。
- 项目工作区的「项目配置」按用途设置文本模型、图像模型和训练底模。文本模型是项目全局 LLM；每个 LLM 任务默认继承它，只有任务的 `llm_overrides.<task>.enabled` 为 `true` 时才合并该任务的 `settings` 覆盖。当前 LLM 调用是 `scenario`。图像生成可分别配置本地底模与在线模型，LoRA 训练有独立训练底模。其他参数包括 LLM Temperature/Token 上限/请求超时/系统提示词、图像 LoRA/权重/尺寸/采样器/步数/CFG/Seed/候选数、训练数据集/续训来源/步数/保存间隔/Rank/学习率/重复次数/Seed、Ren’Py 输出分辨率与默认字体，以及资产提示词默认风格和负面提示词。图像与训练设置会作为该项目新任务的默认值；MCP 可在 `project_save` 的 `config` 中保存这些分组，未传入的字段使用默认值。
- LLM 密钥由服务端环境变量 `PRISMA_LLM_API_KEY` 或 `OPENAI_API_KEY` 提供，页面仅显示密钥来源状态，不会将密钥写入项目。新项目及旧版本默认使用 1280 × 720；`project_save` 可用 `config: {"resolution":{"width":1920,"height":1080}}` 覆盖。
- 原文转写、稳定台词 ID、手动保存 Scenario 与演出绑定、场景提示词和出图都会登记。图像任务保留实际使用的提示词及所选底模、LoRA 和项目。R1–R3 的旧 LoRA 数据关系来自工作目录配置的 `lora-transfer.json`。
- 上传的图片、音频和字体按 SHA-256 存在 `media/`。复用生成图时引用任务原件。导出登记实际使用的 Scenario、演出绑定和素材，数据集、LoRA 和 Ren’Py 导出都可在资源目录找到。

## 脚本工作台

在「剧本」中点选一行，再点「为此句安排演出」，会打开「演出」并定位到相应台词。也可在「演出」直接生成脚本预览，再点选代码中的台词。右侧可预览当前项目的图片或试听音频，插入或替换演出动作。应用后会更新演出绑定和脚本预览。

在侧栏打开「脚本 IDE」，从 Scenario 生成、导入 AI 生成的 `.rpy`，或新建入口场景 `start`。「剧情」是默认视图。可按场景修改台词，新建角色和场景，添加选项、跳转和演出。资产栏可预览并插入已通过素材。复杂语句可点「源码」定位原行，剧情操作会保留其它源码。

「试玩本场」支持台词、分支、跳转和已登记的常见图像及音频语句。遇到 Python、自定义画面或其它复杂命令时，试玩会标出行号。两个编辑视图共用同一个草稿，用「保存草稿」或 `Ctrl+S` 保存版本。「载入上一版」把上一版放回编辑区，再保存就会产生一个新版本。

在脚本节点点「通过」后，可直接点「下载已通过工程」。ZIP 使用这一版源码和它引用的项目资产。后续改 Scenario 或演出绑定时，可以在 IDE 显式重新生成。运行和完整语法检查使用 Ren’Py SDK。

MCP 用 `script_editor_open` 读取草稿或指定 `revision`。`script_preview` 返回可作为编辑起点的 `script` 和 `managed_assets`，`script_editor_asset` 返回可插入的语句及素材路径。用 `script_editor_save` 保存 `text`、`managed_assets` 和编辑基于的 `base`。随后向 `pipeline_action` 传入保存的 `script` 资源引用并通过脚本节点，`renpy_export` 接受该 `script_id` 和 `project_id`。

`script_story_inspect` 返回场景目录、角色和选中场景的剧情行。`script_story_edit` 接收原稿 `text` 和 `operation`，返回局部修改后的 `text` 及新增的 `managed_assets`。把新增素材映射与原稿的映射合并后，再用 `script_editor_save` 保存。

## 项目路线图

每个路线图节点有独立工作台。工作台顶部用小图显示当前节点和直接上下游，点击邻接节点即可切换。中间显示工具、参数、当前结果和节点版本。项目中已导入的原文会直接出现在原文节点。

底部「重试」用当前参数重新运行本节点，或回到本节点手动调整。「通过」把当前结果自动传给所有直接下游。路线图以颜色区分未完成、进行中、已完成和已跳过。一个节点只有在所有直接下游都已完成、输入版本仍有效且没有使用该节点的结果时才显示「已跳过」。

在「LoRA 训练」或「本地模型与工具」中可加载或导入已有 LoRA，然后直接进入本地出图。实际生成需要当前设备支持对应的底模和推理环境。MCP 用 `lora_import` 登记文件，用 `lora_prepare` 获取本地生成配置和环境状态。

下游只接收已通过且选中本节点的上游版本。更新输入后，旧结果不会自动流入下游。每个版本记录所属项目、使用的上游版本和产物引用，可从资源目录追溯来源。

在仓库根目录的 `pipeline-nodes.json` 注册节点。每个节点有稳定的 `id`、名称、输入与产物说明，通过 `panels` 指定工具面板，通过 `retry_control` 指定重试触发的操作。修改线路只需改目标节点的 `depends_on`。有意的回流用 `{"node":"train","feedback":true}` 表示。重启 Prisma 后，前端根据依赖生成节点、箭头和产物表，旧版项目的节点历史仍可读取。

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
