# LoRA 路径与迁移

图像工作目录默认为 `build/prisma-images/`，也可以用 `--image-workspace` 指定其他位置。训练任务的权重保存在 `jobs/<任务 ID>/checkpoints/`，数据集保存在 `datasets/<数据集 ID>/`，手动导入的 SD 1.x LoRA 保存在 `loras/`。

Anything V3 推理和训练共用模型查找顺序。Prisma 先查工作目录的 `models/`，再查 `PRISMA_MEDIA_HOME` 指定的共享目录。已有共享模型时保留原位置即可。新设备可以安装同一底模，或把已有文件放到其中一个位置。导出清单记录底模文件名、来源和可用的 SHA-256。

## 按需导出

从 `image_catalog` 或图像工作室查看 LoRA。图像工作室里选择一个 LoRA 后点「导出所选 LoRA 与训练资料」。MCP 的 `lora_export` 接受 `{"loras":["<LoRA ID>"]}`，支持同时选择多个检查点。输出在图像工作目录下的 `exports/`，可以用返回的路径或下载链接取走。

命令行可直接指定 ZIP 的保存位置。把示例 ID 替换为当前目录中的实际 ID。

```text
python lora_export.py transfer.zip --lora <LoRA_ID>
python lora_export.py transfer.zip --all
```

`--lora` 可以重复使用。也可用 `--image-workspace` 指定源工作目录。导出仅在运行时读取所选检查点，附上它们关联的训练数据和任务记录。同一数据集在 ZIP 中只写入一次。导入来的权重若没有训练记录，ZIP 会包含权重及其导入 ID。底模单独安装一次，ZIP 只记录依赖。

旧实验若没有标准任务记录，可在源工作目录的 `lora-transfer.json` 的 `legacy` 对象中按 LoRA ID 前缀登记已有数据。每条记录包含 `source`（图像目录的 catalog 来源 ID）、`images`（带扩展名的图片名列表）、`name`、`trigger` 和 `training`（历史参数）。导出时从原图和同名标注读取，按内容生成稳定的数据集 ID。可选的 `notes` 对象把 ZIP 内 `notes/` 下的相对路径映射到仓库中的相对文件路径，适合随包带走提示词和推理设置。

## 在另一台机器上使用

1. 在目标设备准备 Prisma 代码并解压 ZIP 到一个新目录。
2. 用 `python webui.py --image-workspace <解压目录>` 启动。若设备已在共享目录安装底模，启动前把 `PRISMA_MEDIA_HOME` 指向该目录。否则使用工具箱安装模型和训练环境。
3. 在图像工作室选择已有 LoRA，按 `manifest.json` 的 `strength` 设置推理权重。续训 SD 1.x 或 SDXL 时，按 `workspace_id` 和 `dataset_id` 选择起点权重和数据集，再从历史任务读取训练参数。归档中的原命令和 TOML 用于查看历史参数，其中可能保留旧设备路径。Prisma 提交新训练时会根据当前工作目录生成配置。

续训从选定的 LoRA 权重开始，优化器状态会重新建立。导出 ZIP 可以存到另一块盘或外置存储，迁移结束后可按需清理该 ZIP。
