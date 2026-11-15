# 从零开始使用 Prisma

克隆项目后，从仓库根目录启动。WebUI 和 MCP 服务都只需要 Python 3.11 或以上版本，使用同一个后台进程。单独用 MCP 时也运行这条命令，可以不打开浏览器。

```powershell
python webui.py
```

| 入口 | 地址或命令 |
| --- | --- |
| WebUI | `http://127.0.0.1:8765/#image-studio` |
| MCP Streamable HTTP | `http://127.0.0.1:8765/mcp` |
| MCP stdio | `python /absolute/path/to/Prisma/mcp_stdio.py` |

MCP 使用标准 JSON-RPC，支持 `2025-11-25`、`2025-06-18` 和 `2025-03-26` 初始化协议。配置支持 HTTP 的 MCP 客户端时填入 `/mcp` 地址。只支持 stdio 的客户端可以用下面的配置示例，将脚本改成实际的绝对路径。自定义服务端口时，给两个命令传入相同的 `--port`。

```json
{
  "mcpServers": {
    "prisma": {
      "command": "python",
      "args": ["C:/path/to/Prisma/mcp_stdio.py"]
    }
  }
}
```

## 路径一 · 调用在线图像 API

1. 在启动进程的 PowerShell 中设置 `OPENAI_API_KEY`，再启动 `python webui.py`。如果服务已经启动，需要让新进程读取环境变量。API key 由服务端读取，无需写入 MCP 客户端或提交到项目。
2. 在 WebUI 选择「在线模型」，或在 MCP 调用 `image_catalog` 确认 `online.ready`，然后调用 `image_generate`。在线模式会把 `prompt` 发送给 OpenAI，并按账号计费。这条路线无需下载底模和本地工具。
3. 用 `image_job` 查询任务 ID，等 `state` 为 `done`。WebUI 可以直接预览，MCP 可以用 `image_artifact` 读取图片或获取下载地址。

下面的 `image_generate` 请求会生成一张横向背景图。

```json
{
  "mode": "online",
  "prompt": "A cozy library at dusk, bright flat cel colors, visual novel background",
  "model": "gpt-image-1.5",
  "size": "1536x1024",
  "quality": "medium",
  "count": 1
}
```

重提在线任务会创建新的 API 请求。在线结果受服务端模型版本和行为影响，记录可以还原请求参数，重提可能得到不同图片。

## 路径二 · 安装本地模型并训练 LoRA

本地安装和推理支持 Windows x64 与 Vulkan。训练另需兼容 CUDA 12.4 的 NVIDIA 显卡及驱动。Anything V3 底模约 2.13 GB，首次拉取完整工具链还需为 CUDA PyTorch 解包和缓存留出空间。

1. **安装组件。** 在工具箱点「拉取完整 LoRA 工具链」，或调用 `image_install` 并传 `{"target":"training"}`。安装自动准备 Vulkan 推理、Anything V3、Python 3.10.20、CUDA PyTorch、sd-scripts 和 CLIP 分词文件。只想先试底模时选 `{"target":"inference"}`。使用 `image_job` 查看下载阶段、失败原因与日志，失败后可再次提交同一目标。
2. **准备自己的图。** 将 PNG、JPEG 或 WebP 和同名 UTF-8 `.txt` 标注放入 `Reference/Img`，或启动时传入 `--reference-dir "D:/my-art"`。WebUI 也支持同时上传图片和标注。图库为空时，可以先跑底模生成候选图并下载，编写好标注后，再作为自备训练数据导入。画风 LoRA 可设置一个共用触发词，在每张图的标注里分别记录发色、服装、构图等内容。
3. **保存快照。** 先调用 `dataset_source` 并传 `{"source":"reference"}`，用 `dataset_image` 看图，再用 `dataset_save` 传入选择的文件名和修改后的标注。若调用方手上只有文件内容，可以在各条目上加 `data`，传对应图片的 Base64。`dataset_export` 返回清单、下载地址和本地目录。WebUI 的「下载数据集 ZIP」也会导出图片、标注和校验值。
4. **训练。** 用返回的数据集 ID 调用 `lora_train`。默认设置是 200 步、每 100 步保存一次、Rank 4、学习率 `0.0001`、每张图重复 10 次。`image_job` 完成后会给出 checkpoint 文件名、校验值和可直接用于推理的 LoRA ID。训练起点是指定的 Anything V3 底模，已有 LoRA 可以经 `lora_import` 单独用于推理。
5. **出图和对照。** 调用 `image_generate`，设 `mode` 为 `local`。`loras` 传 `['']` 使用底模，传一个 ID 应用该权重，传两个选项按相同 Seed 对照。提示词写入自己设置的触发词。任务完成后，用 `image_artifact` 下载或预览图片。WebUI 中点「加入素材」即可挂到当前工程。

下面分别是 `dataset_save` 和 `lora_train` 的参数。训练时将 `dataset` 替换为上一步返回的 ID。

```json
{
  "name": "style-snapshot-1",
  "source": "reference",
  "trigger": "myCelStyle",
  "items": [
    {"name": "portrait.png", "caption": "woman with short brown hair, teal jacket, head and shoulders"},
    {"name": "garden.jpg", "caption": "two women walking, bright garden, full figure composition"}
  ]
}
```

```json
{
  "dataset": "0123456789abcdef",
  "name": "my_style_r1",
  "steps": 200,
  "save_every": 100,
  "rank": 4,
  "lr": 0.0001,
  "repeats": 10,
  "seed": 1065270805
}
```

本地推理时，将 `<checkpoint-id>` 替换为 `image_job` 返回的 checkpoint `id`。此例用同一 Seed 对照底模与新权重。

```json
{
  "mode": "local",
  "prompt": "myCelStyle, two women in a garden, crisp flat cel shadows",
  "negative": "animal ears, halo, text, watermark",
  "loras": ["", "<checkpoint-id>"],
  "width": 512,
  "height": 896,
  "steps": 20,
  "cfg": 6.5,
  "seed": 271828,
  "count": 2
}
```

## 从 Scenario 到成品

1. `scenario_draft` 接收 `raw` 和可选的说话人映射。人工校对 `scenario` 后，用 `scenario_inspect` 取得场景和行号。
2. `scenario_asset_prompt` 接收 `scenario`、场景 ID、`kind`（`background`、`sprite` 或 `cg`）和可选的 `line`、`brief`、`trigger`。它返回提示词和附近的剧本台词，供确认构图与人物细节后生成。
3. 在在线或本地路线挑图后，调用 `renpy_export`。`cues` 和 WebUI 同格式，每个生成素材通过 `image_assets` 映射到任务与文件。`renpy_export` 返回磁盘路径和下载地址。

要在 `1:1` 的第一句切换背景，可以给 `renpy_export` 传以下字段，并带上完整的 `scenario`。

```json
{
  "cues": {"1:1": {"1": [{"background": "garden.png"}]}},
  "image_assets": {
    "garden.png": {"job_id": "0123456789abcdef", "name": "local-1-000.png"}
  }
}
```

## 保存与复跑

默认工作目录是 `build/prisma-images`。数据集清单保留标注、顺序和图片 SHA-256，任务记录保留有效参数、输入文件校验值、训练环境、checkpoint 和图像。WebUI 的「恢复数据集 ZIP」会恢复同一个数据集 ID。超过 45 MiB 的 ZIP 可以解压后用 `--reference-dir` 指向含 `dataset.json` 的目录，再调用 `dataset_restore` 并省略 `data`。迁移完整历史时，同时复制所需任务目录和导入的 LoRA。`image_replay` 或 WebUI 的「复跑本地任务」会复用旧参数，在 GPU 工作前核对新记录的输入。旧任务可能没有完整的校验记录。更换显卡、驱动或推理后端仍可能影响结果。

纯净环境可以用 `python webui.py --no-existing-media --image-workspace build/fresh-prisma` 启动。若已准备官方发布文件，可放入该工作目录的 `downloads`，安装器会校验后使用。没有缓存时会从发布源下载。训练 Python 包版本记录在仓库的 `requirements-lora-win-cu124.txt`，CUDA 两个 wheel 由官方 cu124 索引单独安装。模型、推理工具和训练源码的版本与校验值在 `image_toolkit.py` 中。
