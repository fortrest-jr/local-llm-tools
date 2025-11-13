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

# Загружаем команду для llama из локального файла конфигурации
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LLAMA_CONFIG_FILE="$SCRIPT_DIR/llama_command.local.sh"

if [ ! -f "$LLAMA_CONFIG_FILE" ]; then
    echo "Ошибка: файл $LLAMA_CONFIG_FILE не найден"
    echo "Создайте файл с переменной LLAMA_COMMAND"
    exit 1
fi

source "$LLAMA_CONFIG_FILE"

if [ -z "$LLAMA_COMMAND" ]; then
    echo "Ошибка: переменная LLAMA_COMMAND не определена в файле $LLAMA_CONFIG_FILE"
    exit 1
fi

# Скрипт для graceful shutdown всех сессий
CLOSE_SCRIPT='
for session in sillytavern llama; do
    if tmux has-session -t "$session" 2>/dev/null; then
        tmux send-keys -t "$session" C-c
    fi
done
termux-notification-remove llm-stack
termux-wake-unlock
'

termux-notification \
    --id "llm-stack" \
    --title "llama.cpp + SillyTavern" \
    --button1 "close" \
    --button1-action "bash -c '$CLOSE_SCRIPT'" \
    --button2 "kill" \
    --button2-action "bash -c 'tmux kill-server; termux-notification-remove llm-stack; termux-wake-unlock'" \
    --ongoing

termux-wake-lock

tmux new -d -s llama "$LLAMA_COMMAND"

tmux new -d -As sillytavern ~/SillyTavern/start.sh

tmux attach -t llama

