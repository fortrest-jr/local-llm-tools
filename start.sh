#!/bin/bash

# Скрипт для запуска LLM стека в Termux с tmux
# Использование: ./start_llm_stack.sh <путь_к_модели>

set -eo pipefail

if [ -z "$1" ]; then
    echo "Ошибка: не указан путь к модели"
    echo "Использование: $0 <путь_к_модели>"
    exit 1
fi

MODEL_PATH="$1"

termux-notification \
    --id "llm-stack" \
    --title "Запуск LLM стека" \
    --content "Запуск llama.cpp, sillytavern и kv_cache_saver..."

termux-wake-lock

tmux new -d -s llama "# ВАШУ КОМАНДУ LLAMA.CPP С АРГУМЕНТОМ  ЗАМЕНИТЕ ЗДЕСЬ -m \"$MODEL_PATH\"

tmux new -d -s sillytavern ~/SillyTavern/start.sh

tmux new -d -s kv_cache_saver "python ~/local-llm-tools/kv_cache_saver.py"

tmux attach -t kv_cache_saver

