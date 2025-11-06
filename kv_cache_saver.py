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
INITIAL_TIMEOUT = int(os.getenv("KV_INITIAL_TIMEOUT", "30"))  # таймаут выборов при запуске скрипта
MAX_SLOTS_TO_CHECK = int(os.getenv("KV_MAX_SLOTS_TO_CHECK", "4"))  # максимальное количество слотов для проверки

_base_name_var = ""
_cache_pattern_var = ""
_slot_id_var = SLOT_ID  # Изменяемая переменная для слота


def get_base_name() -> str:
    return _base_name_var


def set_base_name(value: str) -> None:
    global _base_name_var, _cache_pattern_var
    _base_name_var = value
    _cache_pattern_var = f"{value}_slot*_*.bin"


def get_slot_id() -> int:
    return _slot_id_var


def set_slot_id(value: int) -> None:
    global _slot_id_var
    _slot_id_var = value


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
        # Парсим имя файла: base_name_slot{slot_id}_{timestamp}.bin
        name = cache_file.stem
        # Если есть _slot в имени, берем часть до _slot
        if "_slot" in name:
            base_name = name.split("_slot")[0]
            if base_name:
                base_names.add(base_name)
    return sorted(
        list(base_names),
        key=lambda x: max((f.stat().st_mtime for f in SAVE_DIR.glob(f"{x}_slot*_*.bin") if f.exists()), default=0),
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
        files_count = len(list(SAVE_DIR.glob(f"{name}_slot*_*.bin")))
        print(f"  {i}. {name} ({files_count} файлов)")
    print(f"  {len(available_names) + 1}. Добавить новую")
    print(f"\nВыберите вариант ({INITIAL_TIMEOUT} секунд на выбор, Enter для автовыбора последней):")

    global user_input_ready, user_choice
    user_input_ready = False
    user_choice = None

    def timeout_handler():
        global user_input_ready, user_choice
        if not user_input_ready:
            user_choice = "0"
            user_input_ready = True

    timer = threading.Timer(INITIAL_TIMEOUT, timeout_handler)
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


def get_latest_cache_file(slot_id: Optional[int] = None) -> Optional[Path]:
    cache_files = get_cache_files(slot_id)
    if not cache_files:
        return None
    return cache_files[0]


def get_cache_files(slot_id: Optional[int] = None) -> List[Path]:
    """Получает список всех файлов кеша в текущей сессии, опционально фильтруя по слоту"""
    cache_files = list(SAVE_DIR.glob(get_cache_pattern()))
    if slot_id is not None:
        # Фильтруем файлы по номеру слота в имени
        slot_pattern = f"_slot{slot_id}_"
        cache_files = [f for f in cache_files if slot_pattern in f.name]
    cache_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return cache_files


def extract_slot_id_from_filename(filename: str) -> Optional[int]:
    """Извлекает номер слота из имени файла формата base_name_slot{slot_id}_{timestamp}.bin"""
    try:
        # Ищем паттерн _slot{number}_ в имени файла
        parts = filename.split("_slot")
        if len(parts) >= 2:
            slot_part = parts[1].split("_")[0]
            return int(slot_part)
    except (ValueError, IndexError):
        pass
    return None


def extract_timestamp_from_filename(filename: str) -> Optional[str]:
    """Извлекает timestamp из имени файла формата base_name_slot{slot_id}_{timestamp}.bin"""
    try:
        # Ищем паттерн _slot{number}_{timestamp}.bin
        parts = filename.split("_slot")
        if len(parts) >= 2:
            # Берем часть после _slot{number}_
            after_slot = parts[1].split("_", 1)
            if len(after_slot) >= 2:
                # Убираем расширение .bin
                timestamp = after_slot[1].replace(".bin", "")
                # Проверяем, что это валидный timestamp (14 цифр: YYYYMMDDHHMMSS)
                if timestamp.isdigit() and len(timestamp) == 14:
                    return timestamp
    except (ValueError, IndexError):
        pass
    return None


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


def load_cache_from_file(log: logging.Logger, cache_file: Path, slot_id: Optional[int] = None) -> bool:
    """Загружает кеш из файла в указанный слот (или определяет слот из имени файла)"""
    if slot_id is None:
        slot_id = extract_slot_id_from_filename(cache_file.name)
        if slot_id is None:
            log.warning(
                f"Не удалось определить номер слота из имени файла {cache_file.name}, используется текущий слот {get_slot_id()}"
            )
            slot_id = get_slot_id()

    log.info(f"Загрузка кеша из файла {cache_file.name} в слот {slot_id}")
    try:
        payload = {"filename": str(cache_file.name)}
        url = f"{LLAMA_URL}/slots/{slot_id}?action=restore"
        response = requests.post(url, json=payload, timeout=300)
        if response.status_code == 200:
            log.info(f"Кеш успешно загружен из {cache_file.name} в слот {slot_id}")
            return True
        else:
            log.warning(f"Ошибка загрузки кеша в слот {slot_id}: статус {response.status_code}, {response.text}")
            return False
    except Exception as e:
        log.error(f"Ошибка при загрузке кеша в слот {slot_id}: {e}", exc_info=True)
        return False


def load_cache(log: logging.Logger, interactive: bool = True) -> bool:
    """Загружает сохраненные слоты для выбранного timestamp"""
    # Получаем все файлы кеша для текущей сессии
    cache_files = get_cache_files()

    if not cache_files:
        log.info(f"Файлы кеша для сессии '{get_base_name()}' не найдены")
        return False

    # Группируем файлы по timestamp (времени сохранения)
    timestamp_groups: dict[str, List[Path]] = {}

    for cache_file in cache_files:
        timestamp = extract_timestamp_from_filename(cache_file.name)
        if timestamp is not None:
            if timestamp not in timestamp_groups:
                timestamp_groups[timestamp] = []
            timestamp_groups[timestamp].append(cache_file)

    if not timestamp_groups:
        log.warning(f"Не удалось определить timestamp из файлов кеша для сессии '{get_base_name()}'")
        return False

    if interactive:
        # Интерактивный выбор: показываем файлы сгруппированные по времени сохранения
        selected_timestamp = load_cache_interactive(log, timestamp_groups)
        if selected_timestamp is None:
            return False
    else:
        # Автоматическая загрузка: выбираем самое последнее сохранение (самый новый timestamp)
        selected_timestamp = max(timestamp_groups.keys())

    # Загружаем только те слоты, для которых есть файлы с выбранным timestamp
    files_to_load = timestamp_groups[selected_timestamp]

    # Форматируем timestamp для отображения
    try:
        dt = datetime.strptime(selected_timestamp, "%Y%m%d%H%M%S")
        formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        formatted_time = selected_timestamp

    log.info(f"Загрузка сохранения от {formatted_time} ({len(files_to_load)} файлов)")
    success_count = 0
    loaded_slots: List[int] = []

    for cache_file in files_to_load:
        slot_id = extract_slot_id_from_filename(cache_file.name)
        if slot_id is not None:
            if load_cache_from_file(log, cache_file, slot_id):
                success_count += 1
                loaded_slots.append(slot_id)

    log.info(f"Успешно загружено {success_count} из {len(files_to_load)} файлов (слоты: {sorted(loaded_slots)})")
    return success_count > 0


def load_cache_interactive(log: logging.Logger, timestamp_groups: dict[str, List[Path]]) -> Optional[str]:
    """Интерактивный выбор timestamp для загрузки, возвращает выбранный timestamp или None"""
    # Сортируем timestamp по убыванию (самые новые первыми)
    sorted_timestamps = sorted(timestamp_groups.keys(), reverse=True)

    if not sorted_timestamps:
        log.warning("Не найдено файлов для загрузки")
        return None

    print(f"\nДоступные сохранения для загрузки (сгруппированы по времени):")
    group_index = 1
    timestamp_map: List[str] = []  # Индекс -> timestamp

    for timestamp in sorted_timestamps:
        files = timestamp_groups[timestamp]
        # Форматируем timestamp для отображения: YYYYMMDDHHMMSS -> YYYY-MM-DD HH:MM:SS
        try:
            dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
            formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            formatted_time = timestamp

        # Получаем список слотов для этого timestamp
        slots: List[int] = []
        for cache_file in files:
            slot_id = extract_slot_id_from_filename(cache_file.name)
            if slot_id is not None:
                slots.append(slot_id)
        slots_str = ", ".join(map(str, sorted(slots))) if slots else "неизвестно"

        print(f"  {group_index}. Сохранение от {formatted_time} ({len(files)} файлов, слоты: {slots_str})")
        timestamp_map.append(timestamp)
        group_index += 1

    # Также показываем бекапы, сгруппированные по времени
    backup_files = list(SAVE_DIR.glob(get_backup_pattern()))
    backup_timestamp_groups: dict[str, List[Path]] = {}

    for backup_file in backup_files:
        # Для бекапов формат: backup_{base_name}_slot{slot_id}_{timestamp}.bin
        # Нужно извлечь timestamp после backup_
        backup_name = backup_file.name
        if backup_name.startswith("backup_"):
            # Убираем "backup_" и ищем timestamp
            name_without_backup = backup_name[7:]  # убираем "backup_"
            timestamp = extract_timestamp_from_filename(name_without_backup)
            if timestamp is not None:
                if timestamp not in backup_timestamp_groups:
                    backup_timestamp_groups[timestamp] = []
                backup_timestamp_groups[timestamp].append(backup_file)

    if backup_timestamp_groups:
        sorted_backup_timestamps = sorted(backup_timestamp_groups.keys(), reverse=True)
        print(f"\nБекапы:")
        for timestamp in sorted_backup_timestamps:
            files = backup_timestamp_groups[timestamp]
            try:
                dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
                formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                formatted_time = timestamp

            # Получаем список слотов для этого бекапа
            backup_slots: List[int] = []
            for backup_file in files:
                backup_name_without_prefix = backup_file.name.replace("backup_", "")
                slot_id = extract_slot_id_from_filename(backup_name_without_prefix)
                if slot_id is not None:
                    backup_slots.append(slot_id)
            slots_str = ", ".join(map(str, sorted(backup_slots))) if backup_slots else "неизвестно"

            print(f"  {group_index}. Бекап от {formatted_time} ({len(files)} файлов, слоты: {slots_str})")
            timestamp_map.append(timestamp)
            group_index += 1

    print(f"\n  {group_index}. Пропустить загрузку")
    print(f"\nВыберите сохранение для загрузки (Enter для автовыбора - загрузить самое последнее):")

    try:
        choice_str = input("> ").strip()
        if not choice_str:
            # Автовыбор - самое последнее сохранение (первый в списке)
            return sorted_timestamps[0]
    except (EOFError, KeyboardInterrupt):
        return None

    if choice_str == str(group_index):
        return None  # Пропустить загрузку
    else:
        try:
            choice_num = int(choice_str)
            if 1 <= choice_num <= len(timestamp_map):
                selected_timestamp = timestamp_map[choice_num - 1]
                return selected_timestamp
            else:
                log.warning("Неверный выбор")
                return None
        except ValueError:
            log.warning("Неверный выбор")
            return None


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


def get_slots_with_latest_timestamp(log: logging.Logger) -> List[int]:
    """Получает список слотов, для которых есть кеш с последним (самым новым) таймстемпом"""
    cache_files = get_cache_files()
    if not cache_files:
        log.debug("Нет файлов кеша для определения последнего таймстемпа")
        return []

    # Группируем файлы по timestamp
    timestamp_groups: dict[str, List[Path]] = {}
    for cache_file in cache_files:
        timestamp = extract_timestamp_from_filename(cache_file.name)
        if timestamp is not None:
            if timestamp not in timestamp_groups:
                timestamp_groups[timestamp] = []
            timestamp_groups[timestamp].append(cache_file)

    if not timestamp_groups:
        log.debug("Не удалось определить timestamp из файлов кеша")
        return []

    # Находим последний (самый новый) timestamp
    latest_timestamp = max(timestamp_groups.keys())

    # Получаем список слотов для этого timestamp
    files_with_latest_timestamp = timestamp_groups[latest_timestamp]
    slots: List[int] = []
    for cache_file in files_with_latest_timestamp:
        slot_id = extract_slot_id_from_filename(cache_file.name)
        if slot_id is not None:
            slots.append(slot_id)

    # Убираем дубликаты и сортируем
    return sorted(list(set(slots)))


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


def get_slot_info(log: logging.Logger, slot_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Получает информацию о слоте через API"""
    if slot_id is None:
        slot_id = get_slot_id()
    try:
        url = f"{LLAMA_URL}/slots/{slot_id}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            log.debug(f"Не удалось получить информацию о слоте {slot_id}: статус {response.status_code}")
            return None
    except Exception as e:
        log.debug(f"Ошибка при получении информации о слоте {slot_id}: {e}")
        return None


def is_cache_valid(log: logging.Logger, slot_id: Optional[int] = None) -> bool:
    """Проверяет валидность кеша через API (наличие токенов в контексте)"""
    slot_info = get_slot_info(log, slot_id)
    if slot_info is None:
        # Если не можем проверить через API, считаем невалидным
        return False

    # Проверяем наличие токенов в контексте
    n_ctx_used = slot_info.get("n_ctx_used", 0)
    n_prompt_tokens = slot_info.get("n_prompt_tokens", 0)

    # Считаем кеш валидным, если есть хотя бы несколько токенов в контексте
    # или если есть промпт-токены (это означает, что есть активный контекст)
    is_valid = n_ctx_used > 0 or n_prompt_tokens > 0

    if not is_valid:
        log.debug(
            f"Кеш слота {slot_id or get_slot_id()} пустой: n_ctx_used={n_ctx_used}, n_prompt_tokens={n_prompt_tokens}"
        )

    return is_valid


def get_all_slots_with_data(log: logging.Logger) -> List[int]:
    """Получает список всех слотов, которые содержат данные"""
    slots_with_data: List[int] = []

    # Сначала пробуем получить список всех слотов через эндпоинт /slots
    try:
        url = f"{LLAMA_URL}/slots"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            slots_data = response.json()
            # Если это список слотов
            if isinstance(slots_data, list):
                for slot_data in slots_data:  # type: ignore
                    slot_id: Optional[int] = None
                    if isinstance(slot_data, dict):
                        slot_id_val = slot_data.get("id")  # type: ignore
                        if isinstance(slot_id_val, int):
                            slot_id = slot_id_val
                    elif isinstance(slot_data, int):
                        slot_id = slot_data
                    if slot_id is not None and is_cache_valid(log, slot_id):
                        slots_with_data.append(slot_id)
            # Если это словарь с ключами-номерами слотов
            elif isinstance(slots_data, dict):
                for slot_id_str, _slot_data in slots_data.items():  # type: ignore
                    try:
                        slot_id = int(slot_id_str)  # type: ignore
                        if is_cache_valid(log, slot_id):
                            slots_with_data.append(slot_id)
                    except (ValueError, TypeError):
                        continue
            return sorted(slots_with_data)
    except Exception as e:
        log.debug(f"Не удалось получить список слотов через /slots: {e}, перебираем вручную")

    # Если не получилось через /slots, перебираем слоты вручную
    for slot_id in range(MAX_SLOTS_TO_CHECK):
        if is_cache_valid(log, slot_id):
            slots_with_data.append(slot_id)

    return sorted(slots_with_data)


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
    """Сохраняет все слоты с данными в текущей сессии"""
    slots_with_data = get_all_slots_with_data(log)
    if not slots_with_data:
        log.debug("Нет слотов с данными для сохранения")
        return False

    log.info(f"Найдено {len(slots_with_data)} слотов с данными для сохранения: {slots_with_data}")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    success_count = 0

    for slot_id in slots_with_data:
        filename = f"{get_base_name()}_slot{slot_id}_{timestamp}.bin"
        log.info(f"Сохранение кеша слота {slot_id} в файл: {filename}")
        try:
            payload = {"filename": filename}
            url = f"{LLAMA_URL}/slots/{slot_id}?action=save"
            response = requests.post(url, json=payload, timeout=300)
            if response.status_code != 200:
                log.error(f"Ошибка сохранения кеша слота {slot_id}: статус {response.status_code}, {response.text}")
                continue

            # Проверяем, что файл создан
            cache_file_path = SAVE_DIR / filename
            if not cache_file_path.exists():
                log.warning(f"Файл {filename} не найден после сохранения")
                continue

            file_size = cache_file_path.stat().st_size
            log.info(f"Кеш слота {slot_id} успешно сохранен в {filename} (размер: {file_size} байт)")
            success_count += 1
        except Exception as e:
            log.error(f"Ошибка при сохранении кеша слота {slot_id}: {e}", exc_info=True)

    if success_count > 0:
        rotate_cache_files(log)
        log.info(f"Успешно сохранено {success_count} из {len(slots_with_data)} слотов")

    return success_count > 0


def get_cache_file_for_timestamp(slot_id: int, timestamp: str) -> Optional[Path]:
    """Получает файл кеша для слота с указанным таймстемпом"""
    cache_files = get_cache_files(slot_id)
    for cache_file in cache_files:
        file_timestamp = extract_timestamp_from_filename(cache_file.name)
        if file_timestamp == timestamp:
            return cache_file
    return None


def create_backup_with_name(log: logging.Logger, name: str) -> bool:
    """Создает бекапы для всех слотов с кешем на последний таймстемп (бекапы - копии уже валидных кешей)

    Args:
        log: Логгер
        name: Имя для префикса бекапа
    """
    # Получаем слоты с кешем на последний таймстемп
    slots = get_slots_with_latest_timestamp(log)
    if not slots:
        log.warning("Нет слотов с кешем на последний таймстемп для создания бекапа")
        return False

    # Получаем последний таймстемп
    cache_files = get_cache_files()
    timestamp_groups: dict[str, List[Path]] = {}
    for cache_file in cache_files:
        timestamp = extract_timestamp_from_filename(cache_file.name)
        if timestamp is not None:
            if timestamp not in timestamp_groups:
                timestamp_groups[timestamp] = []
            timestamp_groups[timestamp].append(cache_file)

    if not timestamp_groups:
        log.warning("Не удалось определить таймстемпы из файлов кеша")
        return False

    latest_timestamp = max(timestamp_groups.keys())

    success_count = 0
    for slot_id in slots:
        # Получаем файл кеша для слота с последним таймстемпом
        cache_file = get_cache_file_for_timestamp(slot_id, latest_timestamp)
        if cache_file is None:
            log.debug(f"Не найден файл кеша для слота {slot_id} с таймстемпом {latest_timestamp}")
            continue

        # Берем имя кеш-файла и формируем имя: {name}_{cache_name}
        cache_name = cache_file.name
        backup_filename = f"{name}_{cache_name}"
        backup_path = SAVE_DIR / backup_filename

        try:
            shutil.copy2(cache_file, backup_path)
            file_size = backup_path.stat().st_size

            # Вычисляем и сохраняем хеш для быстрого сравнения в будущем
            log.debug(f"Вычисление хеша для бекапа {backup_filename}...")
            get_file_hash_cached(backup_path)

            log.info(f"Создан бекап для слота {slot_id}: {backup_filename} (размер: {file_size} байт)")
            success_count += 1
        except Exception as e:
            log.error(f"Ошибка при создании бекапа для слота {slot_id}: {e}", exc_info=True)
            if backup_path.exists():
                try:
                    backup_path.unlink(missing_ok=True)
                except Exception:
                    pass

    return success_count > 0


def create_backup(log: logging.Logger) -> bool:
    """Создает бекапы для всех слотов с кешем на последний таймстемп, если есть изменения хотя бы в одном слоте"""
    # Получаем слоты с кешем на последний таймстемп
    slots_with_latest_timestamp = get_slots_with_latest_timestamp(log)
    if not slots_with_latest_timestamp:
        log.debug("Нет слотов с кешем на последний таймстемп для создания бекапа")
        return False

    # Получаем последний таймстемп
    cache_files = get_cache_files()
    timestamp_groups: dict[str, List[Path]] = {}
    for cache_file in cache_files:
        timestamp = extract_timestamp_from_filename(cache_file.name)
        if timestamp is not None:
            if timestamp not in timestamp_groups:
                timestamp_groups[timestamp] = []
            timestamp_groups[timestamp].append(cache_file)

    if not timestamp_groups:
        log.debug("Не удалось определить таймстемпы из файлов кеша")
        return False

    latest_timestamp = max(timestamp_groups.keys())

    # Проверяем, есть ли изменения хотя бы в одном слоте
    has_changes = False
    for slot_id in slots_with_latest_timestamp:
        cache_file = get_cache_file_for_timestamp(slot_id, latest_timestamp)
        if cache_file is None:
            continue

        # Ищем последний бекап для этого слота
        backup_files = list(SAVE_DIR.glob(get_backup_pattern()))
        slot_backups = [f for f in backup_files if f"_slot{slot_id}_" in f.name]
        if slot_backups:
            slot_backups.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            latest_backup = slot_backups[0]
            try:
                latest_backup_hash = get_file_hash_cached(latest_backup)
                cache_hash = get_file_hash_cached(cache_file)
                if latest_backup_hash != cache_hash:
                    has_changes = True
                    log.debug(f"Обнаружены изменения в слоте {slot_id}")
                    break
            except Exception as e:
                log.debug(f"Ошибка при сравнении хешей для слота {slot_id}: {e}, считаем что есть изменения")
                has_changes = True
                break
        else:
            # Если нет бекапов для этого слота, значит есть изменения
            has_changes = True
            log.debug(f"Нет бекапов для слота {slot_id}, считаем что есть изменения")
            break

    if not has_changes:
        log.info("Бекапы не созданы: содержимое всех слотов совпадает с последними бекапами")
        return False

    # Если есть изменения, создаем бекапы для всех слотов с последним таймстемпом
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
        # Функция автоматически использует слоты с кешем на последний таймстемп
        slots_count = len(get_slots_with_latest_timestamp(log))
        if create_backup_with_name(log, name):
            log.info(f"Бекап успешно создан с именем '{name}' для {slots_count} слотов")
        else:
            log.error(f"Не удалось создать бекап с именем '{name}'")
    elif cmd == "load":
        log.info("Загрузка кеша...")
        # Интерактивный выбор файлов для загрузки
        load_cache(log, interactive=True)
    elif cmd == "slot" and len(parts) > 1:
        try:
            new_slot_id = int(parts[1].strip())
            if new_slot_id < 0:
                log.warning("Номер слота должен быть неотрицательным числом")
                return
            old_slot_id = get_slot_id()
            set_slot_id(new_slot_id)
            log.info(f"Слот изменен с {old_slot_id} на {new_slot_id}")
        except ValueError:
            log.warning("Номер слота должен быть числом. Использование: slot <номер>")
    elif cmd == "help":
        log.info("Доступные команды:")
        log.info("  backup <name> - создать бекап последнего кеша с указанным именем")
        log.info("  load - загрузить кеш из файла (с выбором)")
        log.info("  slot <номер> - сменить номер слота")
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
    logger.debug(f"LLAMA_URL: {LLAMA_URL}")
    logger.debug(f"SAVE_DIR: {SAVE_DIR}")
    logger.debug(f"SAVE_INTERVAL: {SAVE_INTERVAL} секунд")
    logger.debug(f"MAX_FILES: {MAX_FILES}")
    logger.debug(f"MAX_BACKUPS: {MAX_BACKUPS}")
    logger.debug(f"BACKUP_INTERVAL: {BACKUP_INTERVAL} секунд ({BACKUP_INTERVAL / 3600:.1f} часов)")
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
    # Загружаем все сохраненные слоты для текущей сессии
    logger.info("Попытка загрузки сохраненных слотов...")
    load_cache(logger, interactive=False)
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
