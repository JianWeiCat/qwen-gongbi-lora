#!/usr/bin/env python3
"""Run Qwen-Image-Edit-2511 + gongbi LoRA validation on local test images."""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
from PIL import Image
from tqdm import tqdm

from diffsynth.pipelines.qwen_image import ModelConfig, QwenImagePipeline


DEFAULT_PROMPT = (
    "Convert the input image into traditional Chinese gongbi painting style, "
    "preserving the original composition, subject details, and spatial structure."
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lora-path", required=True, type=Path)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", default=Path("outputs/validation"), type=Path)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--height", default=1024, type=int)
    parser.add_argument("--width", default=1024, type=int)
    parser.add_argument("--steps", default=40, type=int)
    parser.add_argument("--seed", default=123, type=int)
    parser.add_argument("--device", default="cuda")
    return parser.parse_args()


def list_images(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def load_pipe(device: str, lora_path: Path) -> QwenImagePipeline:
    pipe = QwenImagePipeline.from_pretrained(
        torch_dtype=torch.bfloat16,
        device=device,
        model_configs=[
            ModelConfig(
                model_id="Qwen/Qwen-Image-Edit-2511",
                origin_file_pattern="transformer/diffusion_pytorch_model*.safetensors",
            ),
            ModelConfig(
                model_id="Qwen/Qwen-Image",
                origin_file_pattern="text_encoder/model*.safetensors",
            ),
            ModelConfig(
                model_id="Qwen/Qwen-Image",
                origin_file_pattern="vae/diffusion_pytorch_model.safetensors",
            ),
        ],
        tokenizer_config=None,
        processor_config=ModelConfig(model_id="Qwen/Qwen-Image-Edit", origin_file_pattern="processor/"),
    )
    pipe.load_lora(pipe.dit, str(lora_path))
    return pipe


def main() -> None:
    args = parse_args()
    if not args.lora_path.exists():
        raise SystemExit(f"LoRA not found: {args.lora_path}")
    if not args.input_dir.exists():
        raise SystemExit(f"input dir not found: {args.input_dir}")

    images = list_images(args.input_dir)
    if not images:
        raise SystemExit(f"no input images found in {args.input_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pipe = load_pipe(args.device, args.lora_path)

    for input_path in tqdm(images, desc="validating"):
        with Image.open(input_path) as image:
            edit_image = image.convert("RGB").resize((args.width, args.height))

        output = pipe(
            args.prompt,
            edit_image=edit_image,
            seed=args.seed,
            num_inference_steps=args.steps,
            height=args.height,
            width=args.width,
            zero_cond_t=True,
        )
        output.save(args.output_dir / f"{input_path.stem}_gongbi.png")


if __name__ == "__main__":
    main()

