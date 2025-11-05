#!/usr/bin/env python3

import os
import sys
import time
import signal
import logging
import requests
import threading
import select
import shutil
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Any, List


LLAMA_URL = os.getenv("LLAMA_URL", "http://127.0.0.1:8080")
SAVE_DIR = Path(os.getenv("KV_SAVE_DIR", str(Path.home() / "kv_cache")))
SAVE_INTERVAL = int(os.getenv("KV_SAVE_INTERVAL", "60"))
MAX_FILES = int(os.getenv("KV_MAX_FILES", "10"))
MAX_BACKUPS = int(os.getenv("KV_MAX_BACKUPS", "5"))
BACKUP_INTERVAL = int(os.getenv("KV_BACKUP_INTERVAL", "3600"))  # 1 час по умолчанию
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


def get_backup_pattern() -> str:
    """Паттерн для поиска бекапов: backup_{base_name}_*.bin"""
    return f"backup_{get_cache_pattern()}"


running = True
logger: Optional[logging.Logger] = None
user_input_ready = False
user_choice: Optional[str] = None
shutdown_signals_count = 0


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
    cache_files = get_cache_files()
    if not cache_files:
        return None
    return cache_files[0]


def get_cache_files() -> List[Path]:
    """Получает список всех файлов кеша в текущей сессии"""
    cache_files = list(SAVE_DIR.glob(get_cache_pattern()))
    cache_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return cache_files


def get_all_available_files() -> List[Path]:
    """Получает список всех доступных файлов для загрузки (кеши + бекапы)"""
    cache_files = get_cache_files()
    backup_files = list(SAVE_DIR.glob(get_backup_pattern()))
    backup_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    # Объединяем: сначала обычные кеши, потом бекапы
    all_files = cache_files + backup_files
    return all_files


def choose_cache_file(log: logging.Logger, interactive: bool = True, timeout: Optional[float] = 10.0) -> Optional[Path]:
    all_files = get_all_available_files()

    if not all_files:
        if log:
            log.info(f"Файлы кеша для '{get_base_name()}' не найдены")
        else:
            print(f"\nФайлы кеша для '{get_base_name()}' не найдены")
        return None

    if not interactive:
        # Автоматически выбираем последний файл (обычный кеш, если есть)
        cache_files = get_cache_files()
        if cache_files:
            return cache_files[0]
        # Если нет обычных кешей, берем последний бекап
        return all_files[0]

    # Интерактивный выбор
    cache_files = get_cache_files()
    backup_files = list(SAVE_DIR.glob(get_backup_pattern()))
    backup_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    print(f"\nДоступные файлы для загрузки:")
    file_index = 1

    if cache_files:
        print(f"\nОбычные кеши ({len(cache_files)}):")
        for cache_file in cache_files:
            file_size = cache_file.stat().st_size / (1024 * 1024)  # MB
            mtime = datetime.fromtimestamp(cache_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {file_index}. {cache_file.name} ({file_size:.2f} MB, {mtime})")
            file_index += 1

    if backup_files:
        print(f"\nБекапы ({len(backup_files)}):")
        for backup_file in backup_files:
            file_size = backup_file.stat().st_size / (1024 * 1024)  # MB
            mtime = datetime.fromtimestamp(backup_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {file_index}. {backup_file.name} ({file_size:.2f} MB, {mtime})")
            file_index += 1

    print(f"\n  {file_index}. Пропустить загрузку")
    if timeout is not None:
        print(f"\nВыберите файл для загрузки ({int(timeout)} секунд на выбор, Enter для автовыбора последнего):")
    else:
        print(f"\nВыберите файл для загрузки (Enter для автовыбора последнего):")

    global user_input_ready, user_choice
    user_input_ready = False
    user_choice = None

    timer = None
    if timeout is not None:

        def timeout_handler():
            global user_input_ready, user_choice
            if not user_input_ready:
                user_choice = "0"
                user_input_ready = True

        timer = threading.Timer(timeout, timeout_handler)
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
                # Если timeout=None, продолжаем ждать, но проверяем running для корректного завершения
                if timeout is None and not running:
                    user_choice = "0"
                    user_input_ready = True
                    break
        else:
            # Для не-TTY используем input() - он блокирующий и не требует таймаута
            line = input("> ").strip()
            user_choice = line
            user_input_ready = True
    except (EOFError, KeyboardInterrupt):
        user_choice = "0"
        user_input_ready = True

    if timer is not None:
        timer.cancel()

    choice = user_choice if user_choice else "0"

    if choice == "0" or not choice:
        if cache_files:
            selected = cache_files[0]
            print(f"\nАвтоматически выбран последний кеш: {selected.name}")
            return selected
        if all_files:
            selected = all_files[0]
            print(f"\nАвтоматически выбран последний файл: {selected.name}")
            return selected
        print("\nНет доступных файлов для загрузки")
        return None
    elif choice.isdigit() and 1 <= int(choice) <= len(all_files):
        selected = all_files[int(choice) - 1]
        print(f"\nВыбран файл: {selected.name}")
        return selected
    elif choice == str(len(all_files) + 1):
        print("\nЗагрузка пропущена")
        return None
    else:
        try:
            choice_num = int(choice)
            if 1 <= choice_num <= len(all_files):
                selected = all_files[choice_num - 1]
                print(f"\nВыбран файл: {selected.name}")
                return selected
        except ValueError:
            pass
        print("\nНеверный выбор, загрузка пропущена")
        return None


def load_cache_from_file(log: logging.Logger, cache_file: Path) -> bool:
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


def load_cache(log: logging.Logger, interactive: bool = True) -> bool:
    cache_file = choose_cache_file(log, interactive=interactive)
    if cache_file is None:
        return False
    return load_cache_from_file(log, cache_file)


def rotate_cache_files(log: logging.Logger):
    cache_files = list(SAVE_DIR.glob(get_cache_pattern()))
    if len(cache_files) <= MAX_FILES:
        return
    cache_files.sort(key=lambda x: x.stat().st_mtime)
    files_to_delete = cache_files[:-MAX_FILES]
    for file_path in files_to_delete:
        try:
            file_path.unlink(missing_ok=True)
            hash_file = file_path.with_suffix(file_path.suffix + ".hash")
            hash_file.unlink(missing_ok=True)
            log.info(f"Удален старый файл кеша: {file_path.name}")
        except Exception as e:
            log.warning(f"Не удалось удалить файл {file_path.name}: {e}")


def get_latest_backup() -> Optional[Path]:
    backup_files = list(SAVE_DIR.glob(get_backup_pattern()))
    if not backup_files:
        return None
    backup_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return backup_files[0]


def get_file_hash(file_path: Path) -> str:
    """Вычисляет SHA256 хеш файла"""
    sha256_hash = hashlib.sha256()
    block_size = 1024 * 1024  # 1 MB
    with open(file_path, "rb") as f:
        # Читаем файл блоками для больших файлов
        for byte_block in iter(lambda: f.read(block_size), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def get_saved_hash(file_path: Path) -> Optional[str]:
    """Получает сохраненный хеш файла из .hash файла"""
    hash_file = file_path.with_suffix(file_path.suffix + ".hash")
    if hash_file.exists():
        try:
            return hash_file.read_text(encoding="utf-8").strip()
        except Exception:
            return None
    return None


def save_hash(file_path: Path, file_hash: str) -> None:
    """Сохраняет хеш файла в .hash файл"""
    hash_file = file_path.with_suffix(file_path.suffix + ".hash")
    try:
        hash_file.write_text(file_hash, encoding="utf-8")
    except Exception:
        pass


def get_file_hash_cached(file_path: Path) -> str:
    """Получает хеш файла, используя кеш если доступен"""
    # Сначала пробуем получить сохраненный хеш
    saved_hash = get_saved_hash(file_path)
    if saved_hash:
        return saved_hash

    # Вычисляем хеш и сохраняем
    file_hash = get_file_hash(file_path)
    save_hash(file_path, file_hash)
    return file_hash


def get_slot_info(log: logging.Logger) -> Optional[dict[str, Any]]:
    """Получает информацию о слоте через API"""
    try:
        url = f"{LLAMA_URL}/slots/{SLOT_ID}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            log.debug(f"Не удалось получить информацию о слоте: статус {response.status_code}")
            return None
    except Exception as e:
        log.debug(f"Ошибка при получении информации о слоте: {e}")
        return None


def is_cache_valid(log: logging.Logger) -> bool:
    """Проверяет валидность кеша через API (наличие токенов в контексте)"""
    slot_info = get_slot_info(log)
    if slot_info is None:
        # Если не можем проверить через API, считаем валидным
        return True

    # Проверяем наличие токенов в контексте
    n_ctx_used = slot_info.get("n_ctx_used", 0)
    n_prompt_tokens = slot_info.get("n_prompt_tokens", 0)

    # Считаем кеш валидным, если есть хотя бы несколько токенов в контексте
    # или если есть промпт-токены (это означает, что есть активный контекст)
    is_valid = n_ctx_used > 0 or n_prompt_tokens > 0

    if not is_valid:
        log.debug(f"Кеш пустой: n_ctx_used={n_ctx_used}, n_prompt_tokens={n_prompt_tokens}")

    return is_valid


def rotate_backups(log: logging.Logger):
    backup_files = list(SAVE_DIR.glob(get_backup_pattern()))
    if len(backup_files) <= MAX_BACKUPS:
        return
    backup_files.sort(key=lambda x: x.stat().st_mtime)
    files_to_delete = backup_files[:-MAX_BACKUPS]
    for file_path in files_to_delete:
        try:
            file_path.unlink(missing_ok=True)
            hash_file = file_path.with_suffix(file_path.suffix + ".hash")
            hash_file.unlink(missing_ok=True)
            log.info(f"Удален старый бекап: {file_path.name}")
        except Exception as e:
            log.warning(f"Не удалось удалить бекап {file_path.name}: {e}")


def save_cache(log: logging.Logger) -> bool:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{get_base_name()}_{timestamp}.bin"
    log.info(f"Сохранение кеша в файл: {filename}")
    try:
        # Проверяем валидность кеша перед сохранением
        if not is_cache_valid(log):
            log.debug("Кеш пустой или невалидный, сохранение пропущено")
            return False

        payload = {"filename": filename}
        url = f"{LLAMA_URL}/slots/{SLOT_ID}?action=save"
        response = requests.post(url, json=payload, timeout=300)
        if response.status_code != 200:
            log.error(f"Ошибка сохранения кеша: статус {response.status_code}, {response.text}")
            return False

        # Проверяем, что файл создан
        cache_file_path = SAVE_DIR / filename
        if not cache_file_path.exists():
            log.warning(f"Файл {filename} не найден после сохранения")
            return False

        file_size = cache_file_path.stat().st_size
        log.info(f"Кеш успешно сохранен в {filename} (размер: {file_size} байт)")
        rotate_cache_files(log)
        return True
    except Exception as e:
        log.error(f"Ошибка при сохранении кеша: {e}", exc_info=True)
        return False


def create_backup_with_name(log: logging.Logger, name: str) -> bool:
    """Создает бекап последнего сохраненного кеша (бекапы - копии уже валидных кешей)"""
    latest_cache = get_latest_cache_file()
    if latest_cache is None:
        log.warning("Нет файлов кеша для создания бекапа")
        return False

    # Берем имя кеш-файла и формируем имя: {name}_{cache_name}
    cache_name = latest_cache.name
    backup_filename = f"{name}_{cache_name}"
    backup_path = SAVE_DIR / backup_filename

    try:
        shutil.copy2(latest_cache, backup_path)
        file_size = backup_path.stat().st_size

        # Вычисляем и сохраняем хеш для быстрого сравнения в будущем
        log.debug(f"Вычисление хеша для бекапа {backup_filename}...")
        get_file_hash_cached(backup_path)

        log.info(f"Создан бекап: {backup_filename} (размер: {file_size} байт)")
        return True
    except Exception as e:
        log.error(f"Ошибка при создании бекапа: {e}", exc_info=True)
        if backup_path.exists():
            try:
                backup_path.unlink(missing_ok=True)
            except Exception:
                pass
        return False


def create_backup(log: logging.Logger) -> bool:
    latest_cache = get_latest_cache_file()
    if latest_cache is None:
        log.debug("Нет файлов кеша для создания бекапа")
        return False

    latest_backup = get_latest_backup()
    if latest_backup is not None:
        try:
            latest_backup_hash = get_file_hash_cached(latest_backup)
            latest_cache_hash = get_file_hash_cached(latest_cache)
            if latest_backup_hash == latest_cache_hash:
                log.info("Бекап не создан: содержимое совпадает с последним бекапом")
                return False
        except Exception as e:
            log.debug(f"Ошибка при сравнении хешей: {e}, создаем бекап")

    # Используем общую функцию с именем "backup"
    if create_backup_with_name(log, "backup"):
        rotate_backups(log)
        return True
    return False


def process_command(command: str, log: logging.Logger) -> None:
    """Обрабатывает команды из консоли"""
    command = command.strip()
    if not command:
        return

    parts = command.split(None, 1)
    cmd = parts[0].lower()

    if cmd == "backup" and len(parts) > 1:
        name = parts[1].strip()
        if not name:
            log.warning("Не указано имя для бекапа. Использование: backup <name>")
            return
        # Валидация имени (только буквы, цифры, подчеркивания и дефисы)
        if not all(c.isalnum() or c in ('_', '-') for c in name):
            log.warning("Имя может содержать только буквы, цифры, подчеркивания и дефисы")
            return
        log.info(f"Создание бекапа с именем '{name}'...")
        if create_backup_with_name(log, name):
            log.info(f"Бекап успешно создан с именем '{name}'")
        else:
            log.error(f"Не удалось создать бекап с именем '{name}'")
    elif cmd == "restore":
        log.info("Загрузка кеша...")
        cache_file = choose_cache_file(log, interactive=True, timeout=None)
        if cache_file is not None:
            load_cache_from_file(log, cache_file)
        else:
            log.info("Загрузка кеша отменена")
    elif cmd == "help":
        log.info("Доступные команды:")
        log.info("  backup <name> - создать бекап последнего кеша с указанным именем")
        log.info("  restore - загрузить кеш из файла (с выбором)")
        log.info("  help - показать эту справку")
    else:
        log.warning(f"Неизвестная команда: {cmd}. Введите 'help' для справки")


def signal_handler(signum: int, frame: Any) -> None:
    global running, shutdown_signals_count
    shutdown_signals_count += 1
    if shutdown_signals_count >= 2:
        if logger is not None:
            logger.warning(f"Получен второй сигнал {signum}, немедленное завершение без финального сохранения")
        sys.exit(0)
    if logger is not None:
        logger.info(f"Получен сигнал {signum}, завершение работы...")
    running = False


def main():
    global logger, running, shutdown_signals_count
    choose_base_name()
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Запуск kv_cache_saver")
    logger.info(f"LLAMA_URL: {LLAMA_URL}")
    logger.info(f"SAVE_DIR: {SAVE_DIR}")
    logger.info(f"SAVE_INTERVAL: {SAVE_INTERVAL} секунд")
    logger.info(f"MAX_FILES: {MAX_FILES}")
    logger.info(f"MAX_BACKUPS: {MAX_BACKUPS}")
    logger.info(f"BACKUP_INTERVAL: {BACKUP_INTERVAL} секунд ({BACKUP_INTERVAL / 3600:.1f} часов)")
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
    # Предлагаем выбор кеша для загрузки при старте (10 секунд на выбор)
    cache_file = choose_cache_file(logger, interactive=True, timeout=10.0)
    if cache_file is not None:
        load_cache_from_file(logger, cache_file)
    logger.info(f"Начало периодического сохранения (интервал: {SAVE_INTERVAL}с)")
    logger.info(f"Бекапы будут создаваться раз в {BACKUP_INTERVAL}с ({BACKUP_INTERVAL / 3600:.1f} часов)")
    logger.info("Для создания бекапа вручную введите: backup <name>")
    # Создаем бекап при запуске
    logger.info("Создание бекапа при запуске...")
    create_backup(logger)
    last_save_time = time.time()
    last_backup_time = time.time()
    while running:
        try:
            # Проверяем наличие ввода в stdin (неблокирующий режим)
            if sys.stdin.isatty() and hasattr(select, 'select'):
                if select.select([sys.stdin], [], [], 0)[0]:
                    try:
                        line = sys.stdin.readline().strip()
                        if line:
                            process_command(line, logger)
                    except (EOFError, OSError):
                        pass

            current_time = time.time()
            if current_time - last_save_time >= SAVE_INTERVAL:
                save_cache(logger)
                last_save_time = current_time
            if current_time - last_backup_time >= BACKUP_INTERVAL:
                create_backup(logger)
                last_backup_time = current_time
            time.sleep(1)
        except KeyboardInterrupt:
            shutdown_signals_count += 1
            if shutdown_signals_count >= 2:
                logger.warning("Получен второй KeyboardInterrupt, немедленное завершение без финального сохранения")
                sys.exit(0)
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
