#!/bin/bash
# Пример файла конфигурации для команды llama
# Отредактируйте этот файл под свои нужды
# Файл llama_command.local.sh игнорируется git и не будет перезаписан при git pull

# Переменная MODEL_PATH будет доступна из основного скрипта
# Пример команды:
LLAMA_COMMAND="llama-server -m \"$MODEL_PATH\" --port 8080"

# Или более сложный пример:
# LLAMA_COMMAND="llama-server -m \"$MODEL_PATH\" --port 8080 --threads 8 --ctx-size 4096"

