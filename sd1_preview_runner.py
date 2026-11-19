"""Generate reproducible SD 1.x previews with the installed Apple MPS environment."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch
from diffusers import (DPMSolverMultistepScheduler, EulerAncestralDiscreteScheduler,
                       EulerDiscreteScheduler, StableDiffusionPipeline)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if not torch.backends.mps.is_available():
        raise RuntimeError('Apple MPS is not available.')

    pipeline = StableDiffusionPipeline.from_single_file(
        config['model'], config=config['config_repo'], revision=config['config_revision'],
        cache_dir=config['cache_dir'], torch_dtype=torch.float16, use_safetensors=True,
    )
    scheduler = {
        'euler_a': EulerAncestralDiscreteScheduler,
        'euler': EulerDiscreteScheduler,
        'dpm++2m': DPMSolverMultistepScheduler,
    }[config['sampler']]
    scheduler_args = {'use_karras_sigmas': True} if config['sampler'] == 'dpm++2m' else {}
    pipeline.scheduler = scheduler.from_config(pipeline.scheduler.config, **scheduler_args)
    pipeline.vae.enable_slicing()
    pipeline.to('mps')
    if not hasattr(pipeline.text_encoder, 'text_model'):
        pipeline.text_encoder.text_model = SimpleNamespace(
            final_layer_norm=pipeline.text_encoder.final_layer_norm)

    adapters = {}
    for index, variant in enumerate(config['variants']):
        if variant['lora']:
            adapter = f'prisma_style_{index}'
            pipeline.load_lora_weights(variant['lora'], adapter_name=adapter)
            adapters[index] = adapter

    output_dir = Path(config['output_dir'])
    output_dir.mkdir(parents=True, exist_ok=True)
    image_index = 0
    for variant_index, variant in enumerate(config['variants']):
        if variant_index in adapters:
            pipeline.enable_lora()
            pipeline.set_adapters([adapters[variant_index]],
                                  adapter_weights=[float(variant['weight'])])
        elif adapters:
            pipeline.disable_lora()
        for image_number in range(config['count']):
            image_index += 1
            seed = config['seed'] + image_number
            generator = torch.Generator(device='cpu').manual_seed(seed)
            print(f"Generating {image_index}/{len(config['variants']) * config['count']} "
                  f"({variant['label']}, seed {seed})", flush=True)
            result = pipeline(
                prompt=config['prompt'], negative_prompt=config['negative'],
                width=config['width'], height=config['height'],
                num_inference_steps=config['steps'], guidance_scale=config['cfg'],
                generator=generator, clip_skip=config['clip_skip'],
            ).images[0]
            pixels = np.asarray(result)
            if not np.isfinite(pixels).all() or pixels.std() < 3:
                raise RuntimeError('SD 1.x returned an invalid or nearly uniform image; preview was not saved.')
            result.save(output_dir / f'sd1-{image_index:02d}.png', format='PNG')


if __name__ == '__main__':
    main()
