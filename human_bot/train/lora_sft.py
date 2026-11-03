"""LoRA SFT for the human-style contract. Runs on a Colab/Kaggle GPU, not on this box.

Colab (T4, ~35 min for 1k examples, 3 epochs):

    !pip -q install "trl>=0.9" "peft>=0.11" "transformers>=4.43" datasets bitsandbytes accelerate
    !python lora_sft.py --data sft/train.jsonl --val sft/val.jsonl --out ./adapter \
        --model Qwen/Qwen2.5-7B-Instruct

Then serve it behind any OpenAI-compatible endpoint:

    # vLLM with the adapter merged, or:
    !pip -q install vllm
    !python -m vllm.entrypoints.openai.api_server --model ./merged --port 8000
    # .env: OPENAI_BASE_URL=http://127.0.0.1:8000/v1  HUMAN_BOT_MODEL=local

Why Qwen2.5-7B-Instruct: strong Hinglish/Hindi, 7B fits a 16 GB T4 with 4-bit QLoRA.
You do NOT need to fine-tune to get "human". Prompt + humanizer gets you most of the
way; fine-tuning buys accent, rhythm and persona consistency at scale.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_chat(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append({"messages": json.loads(line)["messages"]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="sft/train.jsonl")
    ap.add_argument("--val", default="sft/val.jsonl")
    ap.add_argument("--out", default="./adapter")
    ap.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=1024)
    a = ap.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    tok = AutoTokenizer.from_pretrained(a.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def to_text(ex: dict) -> dict:
        return {"text": tok.apply_chat_template(ex["messages"], tokenize=False, add_generation_prompt=False)}

    train = Dataset.from_list(load_chat(a.data)).map(to_text, remove_columns=["messages"])
    val = Dataset.from_list(load_chat(a.val)).map(to_text, remove_columns=["messages"]) if Path(a.val).exists() else None

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        a.model, quantization_config=bnb, device_map="auto", attn_implementation="eager"
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(
        r=a.rank, lora_alpha=a.rank * 2, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    ))
    model.print_trainable_parameters()

    args = SFTConfig(
        output_dir=a.out,
        num_train_epochs=a.epochs,
        per_device_train_batch_size=a.batch,
        gradient_accumulation_steps=a.accum,
        learning_rate=a.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        logging_steps=5,
        eval_strategy="epoch" if val else "no",
        save_strategy="epoch",
        bf16=torch.cuda.is_bf16_supported(),
        max_seq_length=a.max_len,
        dataset_text_field="text",
        packing=False,
        report_to="none",
    )
    trainer = SFTTrainer(
        model=model, args=args, train_dataset=train, eval_dataset=val, processing_class=tok,
    )
    trainer.train()
    trainer.save_model(a.out)
    tok.save_pretrained(a.out)
    print(f"\nadapter written to {a.out}")
    print("Next: run eval/humanness.py on held-out REAL calls, not on this val split.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
