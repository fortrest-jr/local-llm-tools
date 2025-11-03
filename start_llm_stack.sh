#!/bin/bash

# Скрипт для запуска LLM стека в Termux с tmux
# Использование: ./start_llm_stack.sh <путь_к_модели>

# Проверка аргумента с путем к модели
if [ -z "$1" ]; then
    echo "Ошибка: не указан путь к модели"
    echo "Использование: $0 <путь_к_модели>"
    exit 1
fi

MODEL_PATH="$1"

# Создание нотификации
termux-notification \
    --id "llm-stack" \
    --title "Запуск LLM стека" \
    --content "Запуск llama.cpp, sillytavern и kv_cache_saver..."

# Включение wake-lock для предотвращения засыпания
termux-wake-lock

# Определяем путь к скрипту (относительно текущего каталога скрипта)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Сессия 1: llama.cpp
# ЗАМЕНИТЕ команду ниже на вашу команду llama.cpp
# Пример: tmux new-session -d -s llama "llama.cpp -m \"$MODEL_PATH\" -ngl 32 -c 2048 --port 8080"
tmux new-session -d -s llama "# ВАШУ КОМАНДУ LLAMA.CPP С АРГУМЕНТОМ  ЗАМЕНИТЕ ЗДЕСЬ -m \"$MODEL_PATH\"

# Сессия 2: sillytavern
tmux new-session -d -s sillytavern ~/SillyTavern/start.sh

tmux new-session -d -s kv_cache_saver "python ~/llm-tools/kv_cache_saver.py"

tmux attach -t kv_cache_saver

