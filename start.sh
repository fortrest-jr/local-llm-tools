#!/bin/bash

# Скрипт для запуска LLM стека в Termux с tmux
# Использование: ./start.sh <путь_к_модели>

set -exo pipefail

if [ -z "$1" ]; then
    echo "Ошибка: не указан путь к модели"
    echo "Использование: $0 <путь_к_модели>"
    exit 1
fi

MODEL_PATH="$1"

# Загружаем команду для llama из локального файла конфигурации
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LLAMA_CONFIG_FILE="$SCRIPT_DIR/llama_command.local.sh"

if [ ! -f "$LLAMA_CONFIG_FILE" ]; then
    echo "Ошибка: файл $LLAMA_CONFIG_FILE не найден"
    echo "Создайте файл с переменной LLAMA_COMMAND"
    exit 1
fi

if [ -z "$LLAMA_COMMAND" ]; then
    echo "Ошибка: переменная LLAMA_COMMAND не определена в файле $LLAMA_CONFIG_FILE"
    exit 1
fi

start_sessions() {
    source "$LLAMA_CONFIG_FILE"
    tmux new -d -s llama "$LLAMA_COMMAND"
    tmux has-session -t sillytavern 2>/dev/null || tmux new-session -d -s sillytavern ~/SillyTavern/start.sh
}

# Создаем уведомление с кнопками управления
termux-notification \
    --id "llm-stack" \
    --title "llama.cpp + SillyTavern" \
    --button1 "Kill" \
    --button1-action "bash -c 'tmux kill-server; termux-wake-unlock; termux-notification-remove llm-stack'" \
    --button2 "Restart" \
    --button2-action "bash -c '$(declare -f start_sessions); tmux kill-server; sleep 2; start_sessions'" \
    --ongoing

termux-wake-lock
start_sessions
tmux attach -t llama