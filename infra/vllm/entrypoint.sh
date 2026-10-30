#!/usr/bin/env bash
# Запуск vLLM OpenAI-совместимого сервера из переменных окружения (см. .env.example).
set -euo pipefail

args=(
  serve "${VLLM_MODEL_ID:-Qwen/Qwen3.8-27B}"
  --served-model-name "${LLM_MODEL:-qwen3.8-27b}"
  --host 0.0.0.0
  --port 8000
  --tensor-parallel-size "${TENSOR_PARALLEL:-1}"
  --max-model-len "${MAX_MODEL_LEN:-131072}"
  --gpu-memory-utilization "${GPU_MEM_UTIL:-0.90}"
  --enable-prefix-caching
  --enable-prompt-tokens-details
  --enable-auto-tool-choice
  --tool-call-parser "${TOOL_CALL_PARSER:-hermes}"
  --reasoning-parser "${REASONING_PARSER:-qwen3}"
)

# fp8 — это квантизация весов в vLLM, а не dtype: активации остаются в bfloat16.
case "${DTYPE:-bfloat16}" in
  bfloat16) args+=(--dtype bfloat16) ;;
  fp8) args+=(--dtype bfloat16 --quantization fp8) ;;
  *) echo "DTYPE=${DTYPE}: ожидается bfloat16 или fp8" >&2; exit 2 ;;
esac

# Дополнительные флаги (например, --hf-overrides для YaRN при длинном контексте).
if [[ -n "${VLLM_EXTRA_ARGS:-}" ]]; then
  read -r -a extra <<< "${VLLM_EXTRA_ARGS}"
  args+=("${extra[@]}")
fi

echo "vllm ${args[*]}"
exec vllm "${args[@]}"
