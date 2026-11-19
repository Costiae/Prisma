# Prisma 资源目录

每个 Prisma 工作目录有自己的 `catalog.sqlite3`，默认位于 `build/prisma-images/`。项目、剧本、演出绑定、提示词、素材、工具、模型、训练数据、任务和导出使用同一个目录。SQLite 保存可搜索的记录和关系，任务的完整参数及日志可通过任务 ID 查看。

## 浏览与维护

- WebUI 顶部的「资源目录」支持按类别、名称、ID、路径或参数搜索。打开记录可查来源和引用。手动放入 Prisma 可发现目录的资源可用「重新扫描本机目录」登记。
- 在「项目」保存当前原文、Scenario、演出绑定、素材和字体。再次保存会追加版本，可从资源目录查看和打开旧版本。「另存为新项目」可以从已打开的内容开始一个新项目。
- 原文转写、稳定台词 ID、手动保存 Scenario 与演出绑定、场景提示词和出图都会登记。图像任务保留实际使用的提示词及所选底模、LoRA 和项目。R1–R3 的旧 LoRA 数据关系来自工作目录配置的 `lora-transfer.json`。
- 上传的图片、音频和字体按 SHA-256 存在 `media/`。复用生成图时引用任务原件。导出登记实际使用的 Scenario、演出绑定和素材，数据集、LoRA 和 Ren’Py 导出都可在资源目录找到。

## MCP

| 工具 | 参数示例 |
| --- | --- |
| `library_list` | `{"kind":"lora","query":"prisma","limit":20,"offset":0}` |
| `library_item` | `{"kind":"lora","id":"<查询返回的 ID>"}` |
| `library_refresh` | `{}` |
| `project_save` | `{"title":"序章","scenario":"# 序章\n\n雨停了。","assets":{}}` |
| `project_open` | `{"id":"<项目 ID>"}` |
| `asset_import` | `{"library_id":"<已有素材 ID>"}` |
| `scenario_save` | `{"scenario":"# 序章\n\n雨停了。","cues":{}}` |

保存已有项目时，传入项目 `id` 及上次返回的 `revision`，避免覆盖其他设备或客户端保存的新版本。`image_generate` 支持 `prompt_id` 和 `project_id`。`renpy_export` 可以通过 `asset_ids` 传入文件名到素材 ID 的映射。现有 `image_job`、`image_artifact` 等入口仍可读取完整任务和文件。

命令行的 `prisma.py draft` 和 `prisma.py build` 也登记输入与产物。可用 `python prisma.py --workspace <目录> draft ...` 选择目标资源目录。

## 迁移与恢复

首次打开工作目录会导入已有记录。项目版本和文档的可迁移清单在 `records/`，复用的上传素材在 `media/`。训练任务、数据集和导出继续使用各自的清单。迁移整个项目工作目录时把所需的 `records/`、`media/`、任务及数据集一并带走，SQLite 可由这些清单重建。LoRA 迁移 ZIP 仍可单独解压为工作目录。工作目录外的本机共享模型和历史引用按本机路径登记。
