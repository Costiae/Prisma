# 从零开始使用 Prisma

克隆项目后，从仓库根目录启动。WebUI 和 MCP 服务都只需要 Python 3.11 或以上版本，使用同一个后台进程。单独用 MCP 时也运行这条命令，可以不打开浏览器。

```powershell
python webui.py
```

| 入口 | 地址或命令 |
| --- | --- |
| WebUI | `http://127.0.0.1:8765/#projects` |
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

本地推理支持 Windows x64 与 Vulkan；Apple Silicon 支持 MPS 与原生 MLX。本机动漫风格训练优先使用 **Anima Base + MFlux / Apple MLX**：Anima 是动漫与插画专用的 2B 模型，Base 变体适合 LoRA 训练；MFlux 训练全部 28 个 transformer block 的注意力、MLP 和 AdaLN 模块，训练 LoRA 输出 ComfyUI 兼容权重。该路径使用 512 分辨率、Rank 16、batch size 1、梯度检查点和 2 GB MLX 缓存。MFlux 固定为官方提交 `02b92461ec246ec44f5149b5dc3f69817a263a67`；Anima Base 固定为仓库提交 `457fbf842cb86e96af72c65bdd13e3f1c448de84`，模型/VAE/文字编码器约 5.7 GB。Qwen3 和 T5 tokenizer 在安装时解析并固定各自修订，所有文件哈希写入安装与训练任务记录。Anima 模型与其衍生权重仅限非商业用途；生成图片的使用权依模型卡条款处理。Iris-3B 也使用该 MLX 环境；其 Base 权重和 Qwen3-VL 文本编码器约 20 GB。SD 1.x 与 SDXL 训练仍支持 Windows x64/CUDA 12.4 和 Apple Silicon/MPS：SD 1.x 使用 512 分辨率，SDXL 使用 1024 分辨率并训练 U-Net LoRA。Animagine XL 4.0 Opt 固定到 `cagliostrolab/animagine-xl-4.0` revision `2b7c1b397761bf5bd3cc42e5b39ec99314a75a96`，SHA-256 为 `6327eca98bfb6538dd7a4edce22484a1bbc57a8cff6b11d075d40da1afb847ac`。SD 1.x/SDXL 使用隔离的 Python 3.10.20 和固定版 sd-scripts；MFlux 使用独立 Python 3.13。

1. **安装组件。** 在工具箱选择训练底模后点「拉取所选 LoRA 工具链」，或调用 `image_install` 并传 `{"target":"training","base_model":"anima"}`；`base_model` 可选 `sd1`、`sdxl`、`iris3b` 或 Apple Silicon 专用的 `anima`。`anima` 安装 MFlux、隔离的 Python 3.13、Anima Base、VAE、文字编码器和 tokenizer；模型权重约 5.7 GB。`iris3b` 复用 MFlux 环境并下载 Iris Base 与 Qwen3-VL 文本编码器。SDXL 使用 Animagine XL checkpoint 和分词文件；SD 1.x 使用 Anything V3。选择 `{"target":"inference"}`（Windows）安装 Vulkan 推理工具和 Anything V3。`image_job` 记录仓库、准确提交、文件哈希、依赖版本、下载阶段和日志。失败后可再次提交同一目标。
2. **准备自己的图。** 将 PNG、JPEG 或 WebP 和同名 UTF-8 `.txt` 标注放入 `Reference/Img`，或启动时传入 `--reference-dir "D:/my-art"`。WebUI 也支持同时上传图片和标注。图库为空时，可以先跑底模生成候选图并下载，编写好标注后，再作为自备训练数据导入。画风 LoRA 可设置一个共用触发词，在每张图的标注里分别记录发色、服装、构图等内容。
3. **保存快照。** 先调用 `dataset_source` 并传 `{"source":"reference"}`，用 `dataset_image` 看图，再用 `dataset_save` 传入选择的文件名和修改后的标注。若调用方手上只有文件内容，可以在各条目上加 `data`，传对应图片的 Base64。`dataset_export` 返回清单、下载地址和本地目录。WebUI 的「下载数据集 ZIP」也会导出图片、标注和校验值。
4. **训练。** 用返回的数据集 ID 调用 `lora_train`，并把 `base_model` 设为 `sd1`、`sdxl`、`iris3b` 或 `anima`。Anima 默认 Rank 16、学习率 1e-4、25 个完整数据轮次，使用 MFlux / Apple MLX；该模型不支持从已有 LoRA 续训。Iris 同样默认 Rank 16。两种 MLX 路线的 `steps` 表示期望更新步数，系统按数据集张数取整为完整轮次，并在 `options.epochs` 记录实际轮数。SD 1.x 默认 Rank 4；SDXL 默认 Rank 8，可用 `parent_lora` 从同系列权重继续训练。`image_job` 会保留有效参数、数据集与模型哈希、配置文件、工具版本、预览图和 checkpoint；用 `image_artifact` 下载预览或权重。
5. **出图和对照。** 调用 `image_generate`，设 `mode` 为 `local`。用 `base_model: "anima"` 选 Anima Base，`loras` 传 `['']` 使用底模、传一个 ID 应用 LoRA，或传两个兼容选项按相同 Seed 对照。Anima 默认 1024 方图、30 步、CFG 4.5 和 `er_sde` 采样器；使用风格 LoRA 时提示词加入 `prismaStyle` 触发词。MFlux 在这台 Mac 上直接用 Apple MLX 生成，并写入与 ComfyUI 兼容的 LoRA 权重。任务完成后，用 `image_artifact` 下载或预览图片。WebUI 中点「加入素材」即可挂到当前工程。

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
  "name": "my_anima_style_r1",
  "base_model": "anima",
  "steps": 1925,
  "save_every": 500,
  "rank": 16,
  "lr": 0.0001,
  "seed": 1065270805
}
```

本地推理时，将 `<checkpoint-id>` 替换为 `image_job` 返回的 checkpoint `id`。此例用同一 Seed 对照底模与新权重。

```json
{
  "mode": "local",
  "prompt": "myCelStyle, two women in a garden, crisp flat cel shadows",
  "base_model": "anima",
  "negative": "worst quality, low quality, score_1, score_2, score_3, blurry, text, watermark",
  "loras": ["", "<checkpoint-id>"],
  "width": 512,
  "height": 896,
  "steps": 30,
  "cfg": 4.5,
  "sampler": "er_sde",
  "seed": 271828,
  "count": 2
}
```

## 从 Scenario 到成品

1. `scenario_draft` 接收 `raw` 和可选的说话人映射。返回的 `scenario` 是 JSON 对象，带稳定的文本 ID 和原文行号。用 WebUI 按章节、场景与台词校对，或由程序直接读取。用 `scenario_inspect` 可取得每场的文本和 ID，也可把旧版 Markdown 转成标准 JSON。
2. `scenario_asset_prompt` 接收 `scenario`、场景 ID、`kind`（`background`、`sprite` 或 `cg`）和可选的 `line`、`brief`、`trigger`。它返回提示词和附近的剧本台词，供确认构图与人物细节后生成。
3. 在在线或本地路线挑图后，调用 `renpy_export`。`cues` 和 WebUI 同格式，每个生成素材通过 `image_assets` 映射到任务与文件。`renpy_export` 返回磁盘路径和下载地址。

已有 Scenario 可以调用 `scenario_anchor`，传入 `scenario` 和可选的 `cues`。它按当前条目编号转换绑定，并返回须一起保存的 `scenario` 和 `cues`。导出时可用 `by_line_id` 保存以文本 ID 为键的动作列表，插入文本或调整场景次序后仍指向原条目。重新填写一句已配音的台词时，复核该 ID 上的配音。若删除了仍有绑定的 ID，导出会指出该 ID。

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

默认工作目录是 `build/prisma-images`。数据集清单保留标注、顺序和图片 SHA-256，任务记录保留所选底模及哈希、训练分辨率、有效参数、输入数据、工具版本、分词文件哈希、checkpoint 和图像。WebUI 的「恢复数据集 ZIP」会恢复同一个数据集 ID。超过 45 MiB 的 ZIP 可以解压后用 `--reference-dir` 指向含 `dataset.json` 的目录，再调用 `dataset_restore` 并省略 `data`。迁移完整历史时，同时复制所需任务目录和导入的 LoRA。`image_replay` 或 WebUI 的「复跑本地任务」会复用旧参数，在 GPU 工作前核对新记录的输入。旧任务可能没有完整的校验记录。更换显卡、驱动或推理后端仍可能影响结果。

纯净环境可以用 `python webui.py --no-existing-media --image-workspace build/fresh-prisma` 启动。若已准备官方发布文件，可放入该工作目录的 `downloads`，安装器会校验后使用。没有缓存时会从发布源下载。训练 Python 包版本分别记录在 `requirements-lora-win-cu124.txt` 和 `requirements-lora-macos-mps.txt`；Apple MPS 使用 PyTorch 2.8.0 与 torchvision 0.23.0，以支持 FP16 GradScaler，Windows CUDA 版本独立固定。模型、推理工具和训练源码的版本与校验值在 `image_toolkit.py` 中。
