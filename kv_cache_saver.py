#!/usr/bin/env python3

import os
import sys
import time
import signal
import logging
import requests
import threading
import select
from pathlib import Path
from datetime import datetime
from typing import Optional, Any, List


LLAMA_URL = os.getenv("LLAMA_URL", "http://127.0.0.1:8080")
SAVE_DIR = Path(os.getenv("KV_SAVE_DIR", str(Path.home() / "kv_cache")))
SAVE_INTERVAL = int(os.getenv("KV_SAVE_INTERVAL", "60"))
MAX_FILES = int(os.getenv("KV_MAX_FILES", "10"))
SLOT_ID = int(os.getenv("LLAMA_SLOT_ID", "3"))
BASE_NAME_ENV = os.getenv("KV_BASE_NAME")
LOG_FILE = SAVE_DIR / "kv_cache_saver.log"

_base_name_var = ""
_cache_pattern_var = ""


def get_base_name() -> str:
    return _base_name_var


def set_base_name(value: str) -> None:
    global _base_name_var, _cache_pattern_var
    _base_name_var = value
    _cache_pattern_var = f"{value}_*.bin"


def get_cache_pattern() -> str:
    return _cache_pattern_var


running = True
logger: Optional[logging.Logger] = None
user_input_ready = False
user_choice: Optional[str] = None


def get_available_base_names() -> List[str]:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    cache_files = list(SAVE_DIR.glob("*.bin"))
    base_names: set[str] = set()
    for cache_file in cache_files:
        parts = cache_file.stem.split("_")
        if len(parts) >= 2:
            base_name = "_".join(parts[:-1])
            base_names.add(base_name)
    return sorted(
        list(base_names),
        key=lambda x: max((f.stat().st_mtime for f in SAVE_DIR.glob(f"{x}_*.bin") if f.exists()), default=0),
        reverse=True,
    )


def choose_base_name() -> None:
    if BASE_NAME_ENV:
        set_base_name(BASE_NAME_ENV)
        return

    available_names = get_available_base_names()

    if not available_names:
        print("\nНайдено существующих сессий: нет")
        name = input("Введите имя новой сессии (Enter для 'session'): ").strip()
        set_base_name(name if name else "session")
        return

    print("\nНайдено существующих сессий:")
    for i, name in enumerate(available_names, 1):
        files_count = len(list(SAVE_DIR.glob(f"{name}_*.bin")))
        print(f"  {i}. {name} ({files_count} файлов)")
    print(f"  {len(available_names) + 1}. Добавить новую")
    print("\nВыберите вариант (30 секунд на выбор, Enter для автовыбора последней):")

    global user_input_ready, user_choice
    user_input_ready = False
    user_choice = None

    def timeout_handler():
        global user_input_ready, user_choice
        if not user_input_ready:
            user_choice = "0"
            user_input_ready = True

    timer = threading.Timer(30.0, timeout_handler)
    timer.daemon = True
    timer.start()

    try:
        if sys.stdin.isatty() and hasattr(select, 'select'):
            sys.stdout.write("> ")
            sys.stdout.flush()
            while not user_input_ready:
                if select.select([sys.stdin], [], [], 0.5)[0]:
                    line = sys.stdin.readline().strip()
                    user_choice = line
                    user_input_ready = True
                    break
        else:
            line = input("> ").strip()
            user_choice = line
            user_input_ready = True
    except (EOFError, KeyboardInterrupt):
        user_choice = "0"
        user_input_ready = True

    timer.cancel()

    choice = user_choice if user_choice else "0"

    if choice == "0" or not choice:
        selected = available_names[0]
        set_base_name(selected)
        print(f"\nАвтоматически выбрана последняя сессия: {selected}")
    elif choice.isdigit() and 1 <= int(choice) <= len(available_names):
        selected = available_names[int(choice) - 1]
        set_base_name(selected)
        print(f"\nВыбрана сессия: {selected}")
    elif choice == str(len(available_names) + 1):
        name = input("Введите имя новой сессии: ").strip()
        selected = name if name else "session"
        set_base_name(selected)
        print(f"\nСоздана новая сессия: {selected}")
    else:
        try:
            selected = available_names[int(choice) - 1]
            set_base_name(selected)
            print(f"\nВыбрана сессия: {selected}")
        except (ValueError, IndexError):
            name = input("Неверный выбор. Введите имя новой сессии: ").strip()
            selected = name if name else "session"
            set_base_name(selected)
            print(f"\nСоздана новая сессия: {selected}")


def setup_logging() -> logging.Logger:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    log = logging.getLogger('kv_cache_saver')
    log.setLevel(logging.INFO)
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    log.addHandler(console_handler)
    return log


def wait_for_server(log: logging.Logger, max_retries: int = 30, retry_delay: int = 2) -> bool:
    log.info(f"Ожидание поднятия сервера {LLAMA_URL}...")
    for attempt in range(max_retries):
        try:
            response = requests.get(f"{LLAMA_URL}/health", timeout=5)
            if response.status_code == 200:
                log.info(f"Сервер доступен после {attempt + 1} попыток")
                return True
        except (requests.exceptions.RequestException, requests.exceptions.Timeout):
            pass
        if attempt < max_retries - 1:
            log.debug(f"Попытка {attempt + 1}/{max_retries} неудачна, повтор через {retry_delay}с")
            time.sleep(retry_delay)
    log.error(f"Сервер не доступен после {max_retries} попыток")
    return False


def get_latest_cache_file() -> Optional[Path]:
    cache_files = list(SAVE_DIR.glob(get_cache_pattern()))
    if not cache_files:
        return None
    cache_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return cache_files[0]


def load_cache(log: logging.Logger) -> bool:
    cache_file = get_latest_cache_file()
    if cache_file is None:
        log.info(f"Файлы кеша для '{get_base_name()}' не найдены, пропуск загрузки")
        return False
    log.info(f"Загрузка кеша из файла: {cache_file.name}")
    try:
        payload = {"filename": str(cache_file.name)}
        url = f"{LLAMA_URL}/slots/{SLOT_ID}?action=restore"
        response = requests.post(url, json=payload, timeout=300)
        if response.status_code == 200:
            log.info(f"Кеш успешно загружен из {cache_file.name}")
            return True
        else:
            log.warning(f"Ошибка загрузки кеша: статус {response.status_code}, {response.text}")
            return False
    except Exception as e:
        log.error(f"Ошибка при загрузке кеша: {e}", exc_info=True)
        return False


def rotate_cache_files(log: logging.Logger):
    cache_files = list(SAVE_DIR.glob(get_cache_pattern()))
    if len(cache_files) <= MAX_FILES:
        return
    cache_files.sort(key=lambda x: x.stat().st_mtime)
    files_to_delete = cache_files[:-MAX_FILES]
    for file_path in files_to_delete:
        try:
            file_path.unlink(missing_ok=True)
            log.info(f"Удален старый файл кеша: {file_path.name}")
        except Exception as e:
            log.warning(f"Не удалось удалить файл {file_path.name}: {e}")


def save_cache(log: logging.Logger) -> bool:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{get_base_name()}_{timestamp}.bin"
    log.info(f"Сохранение кеша в файл: {filename}")
    try:
        payload = {"filename": filename}
        url = f"{LLAMA_URL}/slots/{SLOT_ID}?action=save"
        response = requests.post(url, json=payload, timeout=300)
        if response.status_code != 200:
            log.error(f"Ошибка сохранения кеша: статус {response.status_code}, {response.text}")
            return False
        log.info(f"Кеш успешно сохранен в {filename}")
        rotate_cache_files(log)
        return True
    except Exception as e:
        log.error(f"Ошибка при сохранении кеша: {e}", exc_info=True)
        return False


def signal_handler(signum: int, frame: Any) -> None:
    global running
    if logger is not None:
        logger.info(f"Получен сигнал {signum}, завершение работы...")
    running = False


def main():
    global logger, running
    choose_base_name()
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Запуск kv_cache_saver")
    logger.info(f"LLAMA_URL: {LLAMA_URL}")
    logger.info(f"SAVE_DIR: {SAVE_DIR}")
    logger.info(f"SAVE_INTERVAL: {SAVE_INTERVAL} секунд")
    logger.info(f"MAX_FILES: {MAX_FILES}")
    logger.info(f"SLOT_ID: {SLOT_ID}")
    logger.info(f"BASE_NAME: {get_base_name()}")
    logger.info("=" * 60)
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, signal_handler)
    if not wait_for_server(logger):
        logger.error("Не удалось подключиться к серверу, завершение работы")
        sys.exit(1)
    load_cache(logger)
    logger.info(f"Начало периодического сохранения (интервал: {SAVE_INTERVAL}с)")
    last_save_time = time.time()
    while running:
        try:
            current_time = time.time()
            if current_time - last_save_time >= SAVE_INTERVAL:
                save_cache(logger)
                last_save_time = current_time
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Получен KeyboardInterrupt, завершение работы...")
            running = False
            break
        except Exception as e:
            logger.error(f"Неожиданная ошибка в основном цикле: {e}", exc_info=True)
            time.sleep(5)
    logger.info("Выполнение финального сохранения перед завершением...")
    save_cache(logger)
    logger.info("Работа завершена")


if __name__ == "__main__":
    main()
