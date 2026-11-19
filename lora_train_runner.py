"""Run the pinned SD 1.x or SDXL LoRA trainer.

Executed in the training virtual environment with sd-scripts as its cwd.
"""

from pathlib import Path
import runpy
import sys


def main():
    code = Path.cwd()
    sdxl = '--sdxl' in sys.argv
    if sdxl:
        sys.argv.remove('--sdxl')
        script = code / 'sdxl_train_network.py'
    else:
        script = code / 'train_network.py'
    sys.path.insert(0, str(code))
    if not sdxl:
        import torch
        from library import model_util as model

        original = model.load_models_from_stable_diffusion_checkpoint

        def load(v2, ckpt_path, device='cpu', dtype=None, unet_use_linear_projection_in_v2=True):
            if v2 or torch.device(device).type != 'cuda':
                return original(v2, ckpt_path, device, dtype, unet_use_linear_projection_in_v2)
            _, state = model.load_checkpoint_with_text_encoder_conversion(ckpt_path)

            def materialize(module, weights):
                module.load_state_dict(weights, assign=True)
                return module.to(device=device, dtype=torch.float16)

            config = model.create_unet_diffusers_config(v2, unet_use_linear_projection_in_v2)
            weights = model.convert_ldm_unet_checkpoint(v2, state, config)
            config['in_channels'] = weights['conv_in.weight'].shape[1]
            # Meta construction allocates no parameter storage. Assign the saved
            # tensors directly, so initialization never allocates a second model.
            with torch.device('meta'):
                unet = model.UNet2DConditionModel(**config)
                vae = model.AutoencoderKL(**model.create_vae_diffusers_config())
                text = model.wrap_clip_text_model(model.CLIPTextModel._from_config(model.CLIPTextConfig(
                    vocab_size=49408, hidden_size=768, intermediate_size=3072, num_hidden_layers=12,
                    num_attention_heads=12, max_position_embeddings=77, hidden_act='quick_gelu',
                    pad_token_id=1, bos_token_id=0, eos_token_id=2, projection_dim=768,
                ), dtype=torch.float16))
            # CLIP's nonpersistent positions are supplied by its constructor in the
            # normal loader; they are intentionally absent from checkpoint weights.
            text.text_model.embeddings.position_ids = torch.arange(77, device='cpu').expand((1, -1))
            unet = materialize(unet, weights)
            vae = materialize(vae, model.convert_ldm_vae_checkpoint(state, model.create_vae_diffusers_config()))
            text = materialize(text, model.convert_ldm_clip_checkpoint_v1(state))
            return text, vae, unet

        model.load_models_from_stable_diffusion_checkpoint = load
    sys.argv[0] = str(script)
    runpy.run_path(sys.argv[0], run_name='__main__')


if __name__ == '__main__':
    main()
