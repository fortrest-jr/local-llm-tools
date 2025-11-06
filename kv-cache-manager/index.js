// KV Cache Manager для SillyTavern
// Расширение для управления KV-кешем llama.cpp

(function() {
    'use strict';

    const extensionName = 'kv-cache-manager';
    const defaultSettings = {
        enabled: true,
        saveInterval: 5,
        autoLoadOnChatSwitch: true,
        maxFiles: 10,
        showNotifications: true,
        validateCache: true
    };

    let settings = { ...defaultSettings };
    let messageCounters = {}; // { chatId: count }
    let currentChatId = null;

    // Инициализация расширения
    function init() {
        console.log('[KV Cache Manager] Инициализация расширения');
        
        // Загрузка настроек
        loadSettings();
        
        // Инициализация UI
        initUI();
        
        // Подписка на события
        subscribeToEvents();
        
        console.log('[KV Cache Manager] Расширение инициализировано');
    }

    // Инициализация UI
    function initUI() {
        // Загружаем settings.html при открытии настроек
        if (typeof extension_prompt !== 'undefined') {
            // Хук для загрузки settings.html
            extension_prompt.registerExtension(extensionName, {
                onSettingsLoad: function() {
                    loadSettingsToUI();
                }
            });
        }

        // Инициализация обработчиков событий UI
        setupUIHandlers();
    }

    // Настройка обработчиков событий UI
    function setupUIHandlers() {
        // Используем делегирование событий для элементов, которые могут быть еще не загружены
        document.addEventListener('change', function(e) {
            if (e.target.id === 'kv-cache-enabled') {
                settings.enabled = e.target.checked;
                saveSettings();
                updateUI();
            } else if (e.target.id === 'kv-cache-save-interval') {
                settings.saveInterval = parseInt(e.target.value) || 5;
                saveSettings();
                updateUI();
            } else if (e.target.id === 'kv-cache-max-files') {
                settings.maxFiles = parseInt(e.target.value) || 10;
                saveSettings();
            } else if (e.target.id === 'kv-cache-auto-load') {
                settings.autoLoadOnChatSwitch = e.target.checked;
                saveSettings();
            } else if (e.target.id === 'kv-cache-show-notifications') {
                settings.showNotifications = e.target.checked;
                saveSettings();
            } else if (e.target.id === 'kv-cache-validate') {
                settings.validateCache = e.target.checked;
                saveSettings();
            }
        });

        document.addEventListener('click', function(e) {
            if (e.target.id === 'kv-cache-save-button') {
                e.preventDefault();
                const userName = document.getElementById('kv-cache-save-name')?.value;
                if (userName) {
                    manualSaveCache(userName).then(() => {
                        updateStatistics();
                    });
                } else {
                    if (settings.showNotifications) {
                        toastr.error('Введите имя для сохранения');
                    }
                }
            } else if (e.target.id === 'kv-cache-load-button') {
                e.preventDefault();
                loadCacheDialog();
            }
        });
    }

    // Загрузка настроек в UI
    function loadSettingsToUI() {
        const enabledCheckbox = document.getElementById('kv-cache-enabled');
        if (enabledCheckbox) enabledCheckbox.checked = settings.enabled;

        const saveIntervalInput = document.getElementById('kv-cache-save-interval');
        if (saveIntervalInput) saveIntervalInput.value = settings.saveInterval;

        const maxFilesInput = document.getElementById('kv-cache-max-files');
        if (maxFilesInput) maxFilesInput.value = settings.maxFiles;

        const autoLoadCheckbox = document.getElementById('kv-cache-auto-load');
        if (autoLoadCheckbox) autoLoadCheckbox.checked = settings.autoLoadOnChatSwitch;

        const showNotificationsCheckbox = document.getElementById('kv-cache-show-notifications');
        if (showNotificationsCheckbox) showNotificationsCheckbox.checked = settings.showNotifications;

        const validateCheckbox = document.getElementById('kv-cache-validate');
        if (validateCheckbox) validateCheckbox.checked = settings.validateCache;

        updateUI();
        updateStatistics();
    }

    // Обновление UI
    function updateUI() {
        const chatId = getCurrentChatId();
        const count = messageCounters[chatId] || 0;
        const remaining = Math.max(0, settings.saveInterval - count);
        
        const nextSaveElement = document.getElementById('kv-cache-next-save');
        if (nextSaveElement) {
            if (settings.enabled) {
                nextSaveElement.textContent = `Следующее сохранение через: ${remaining} сообщений`;
            } else {
                nextSaveElement.textContent = 'Автосохранение отключено';
            }
        }
    }

    // Получение всех файлов кеша для текущего чата
    async function getAllCacheFiles() {
        const chatName = getCurrentChatName().replace(/[^a-zA-Z0-9_-]/g, '_');
        const pattern = `.*${chatName}.*_slot\\d+_\\d+\\.bin$`;
        const regex = new RegExp(pattern);
        
        try {
            // Пытаемся получить список файлов через API SillyTavern
            if (typeof extension_api !== 'undefined' && extension_api.getCacheFiles) {
                const files = await extension_api.getCacheFiles();
                return files.filter(f => regex.test(f.name));
            }
        } catch (e) {
            console.debug('[KV Cache Manager] Не удалось получить список файлов через API:', e);
        }
        
        // Fallback: возвращаем пустой массив
        return [];
    }

    // Обновление статистики
    async function updateStatistics() {
        try {
            const files = await getAllCacheFiles();
            
            // Разделяем на автосохранения и ручные сохранения
            const autoSaveFiles = files.filter(f => {
                const chatName = getCurrentChatName().replace(/[^a-zA-Z0-9_-]/g, '_');
                return f.name.startsWith(`${chatName}_slot`) && !f.name.includes('_');
            });
            
            // Находим последнее сохранение
            let lastSave = null;
            if (files.length > 0) {
                // Сортируем по timestamp
                files.sort((a, b) => {
                    const timestampA = extractTimestampFromFilename(a.name);
                    const timestampB = extractTimestampFromFilename(b.name);
                    if (!timestampA || !timestampB) return 0;
                    return timestampB.localeCompare(timestampA); // Новые первыми
                });
                lastSave = files[0];
            }
            
            // Обновляем информацию о последнем сохранении
            const lastSaveInfo = document.getElementById('kv-cache-last-save-info');
            if (lastSaveInfo) {
                if (lastSave) {
                    const timestamp = extractTimestampFromFilename(lastSave.name);
                    const slotIds = [];
                    // Подсчитываем количество уникальных слотов в последнем сохранении
                    const timestampFiles = files.filter(f => {
                        const fTimestamp = extractTimestampFromFilename(f.name);
                        return fTimestamp === timestamp;
                    });
                    for (const file of timestampFiles) {
                        const slotId = extractSlotIdFromFilename(file.name);
                        if (slotId !== null && !slotIds.includes(slotId)) {
                            slotIds.push(slotId);
                        }
                    }
                    const dateStr = timestamp ? formatTimestamp(timestamp) : 'Неизвестно';
                    const sizeStr = lastSave.size ? formatFileSize(lastSave.size) : 'Неизвестно';
                    lastSaveInfo.innerHTML = `
                        <strong>Имя файла:</strong> ${lastSave.name}<br>
                        <strong>Количество слотов:</strong> ${slotIds.length > 0 ? slotIds.length : 'Неизвестно'}<br>
                        <strong>Размер:</strong> ${sizeStr}<br>
                        <strong>Дата/время:</strong> ${dateStr}
                    `;
                } else {
                    lastSaveInfo.textContent = 'Нет сохранений';
                }
            }
            
            // Обновляем общую статистику
            const statsInfo = document.getElementById('kv-cache-stats-info');
            if (statsInfo) {
                const totalSize = files.reduce((sum, f) => sum + (f.size || 0), 0);
                statsInfo.innerHTML = `
                    <strong>Всего файлов:</strong> ${files.length}<br>
                    <strong>Общий размер:</strong> ${formatFileSize(totalSize)}
                `;
            }
        } catch (e) {
            console.error('[KV Cache Manager] Ошибка при обновлении статистики:', e);
            const lastSaveInfo = document.getElementById('kv-cache-last-save-info');
            if (lastSaveInfo) {
                lastSaveInfo.textContent = 'Ошибка загрузки данных';
            }
            const statsInfo = document.getElementById('kv-cache-stats-info');
            if (statsInfo) {
                statsInfo.textContent = 'Ошибка загрузки данных';
            }
        }
    }

    // Форматирование timestamp (формат: YYYYMMDDHHMMSS)
    function formatTimestamp(timestamp) {
        if (!timestamp || timestamp.length !== 14) {
            return timestamp || 'Неизвестно';
        }
        try {
            const year = timestamp.substring(0, 4);
            const month = timestamp.substring(4, 6);
            const day = timestamp.substring(6, 8);
            const hour = timestamp.substring(8, 10);
            const minute = timestamp.substring(10, 12);
            const second = timestamp.substring(12, 14);
            return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
        } catch (e) {
            return timestamp;
        }
    }

    // Форматирование размера файла
    function formatFileSize(bytes) {
        if (!bytes || bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }

    // Диалог загрузки кеша
    async function loadCacheDialog() {
        try {
            const files = await getAllCacheFiles();
            
            if (files.length === 0) {
                if (settings.showNotifications) {
                    toastr.warning('Нет сохраненных файлов кеша для этого чата');
                }
                return;
            }
            
            // Сортируем файлы по timestamp (новые первыми)
            files.sort((a, b) => {
                const timestampA = extractTimestampFromFilename(a.name);
                const timestampB = extractTimestampFromFilename(b.name);
                if (!timestampA || !timestampB) return 0;
                return timestampB.localeCompare(timestampA);
            });
            
            // Группируем файлы по timestamp (все слоты одного сохранения)
            const timestampGroups = {};
            for (const file of files) {
                const timestamp = extractTimestampFromFilename(file.name);
                if (timestamp) {
                    if (!timestampGroups[timestamp]) {
                        timestampGroups[timestamp] = [];
                    }
                    timestampGroups[timestamp].push(file);
                }
            }
            
            // Показываем диалог выбора
            const timestamps = Object.keys(timestampGroups).sort().reverse();
            if (timestamps.length === 0) {
                if (settings.showNotifications) {
                    toastr.warning('Не удалось определить сохранения');
                }
                return;
            }
            
            // Если только одно сохранение, загружаем его автоматически
            if (timestamps.length === 1) {
                const filesToLoad = timestampGroups[timestamps[0]];
                await loadCacheFiles(filesToLoad);
                return;
            }
            
            // Показываем список для выбора (упрощенная версия - загружаем последнее)
            // В полной версии можно использовать модальное окно
            const latestTimestamp = timestamps[0];
            const filesToLoad = timestampGroups[latestTimestamp];
            await loadCacheFiles(filesToLoad);
            
        } catch (e) {
            console.error('[KV Cache Manager] Ошибка при загрузке диалога:', e);
            if (settings.showNotifications) {
                toastr.error('Ошибка при загрузке списка файлов');
            }
        }
    }

    // Загрузка файлов кеша
    async function loadCacheFiles(files) {
        if (!files || files.length === 0) {
            if (settings.showNotifications) {
                toastr.warning('Нет файлов для загрузки');
            }
            return false;
        }
        
        console.log(`[KV Cache Manager] Загрузка ${files.length} файлов кеша`);
        
        let loadedCount = 0;
        const slotMap = {}; // Группируем файлы по слотам
        
        // Группируем файлы по слотам
        for (const file of files) {
            const slotId = extractSlotIdFromFilename(file.name);
            if (slotId !== null) {
                if (!slotMap[slotId]) {
                    slotMap[slotId] = [];
                }
                slotMap[slotId].push(file);
            }
        }
        
        // Загружаем файлы для каждого слота
        for (const slotId in slotMap) {
            const slotFiles = slotMap[slotId];
            // Берем последний файл для слота (самый новый)
            const fileToLoad = slotFiles[slotFiles.length - 1];
            if (await loadSlotCache(parseInt(slotId), fileToLoad.name)) {
                loadedCount++;
                console.log(`[KV Cache Manager] Загружен кеш для слота ${slotId}: ${fileToLoad.name}`);
            }
        }
        
        if (loadedCount > 0) {
            if (settings.showNotifications) {
                toastr.success(`Загружено ${loadedCount} из ${Object.keys(slotMap).length} слотов`);
            }
            await updateStatistics();
            return true;
        } else {
            if (settings.showNotifications) {
                toastr.error('Не удалось загрузить кеш');
            }
            return false;
        }
    }

    // Загрузка настроек из SillyTavern
    function loadSettings() {
        if (typeof extension_settings !== 'undefined' && extension_settings[extensionName]) {
            settings = { ...defaultSettings, ...extension_settings[extensionName] };
        }
    }

    // Сохранение настроек в SillyTavern
    function saveSettings() {
        if (typeof extension_settings !== 'undefined') {
            extension_settings[extensionName] = settings;
            if (typeof saveSettingsDebounced !== 'undefined') {
                saveSettingsDebounced();
            }
        }
    }

    // Получение URL llama.cpp сервера из настроек SillyTavern
    function getLlamaUrl() {
        // Пытаемся получить URL из настроек подключения SillyTavern
        if (typeof main_api !== 'undefined' && main_api) {
            // Если есть main_api, берем его URL
            const apiUrl = main_api;
            // Извлекаем базовый URL (без /api)
            if (apiUrl.includes('/api')) {
                return apiUrl.replace('/api', '');
            }
            return apiUrl;
        }
        
        // Пытаемся получить из настроек API
        if (typeof api_server !== 'undefined' && api_server) {
            return api_server;
        }
        
        // Пытаемся получить из extension_settings
        if (typeof extension_settings !== 'undefined' && extension_settings.api_server) {
            return extension_settings.api_server;
        }
        
        // Fallback на стандартный URL
        return 'http://127.0.0.1:8080';
    }

    // Проверка доступности llama.cpp сервера
    async function checkServerAvailability() {
        const llamaUrl = getLlamaUrl();
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 5000); // 5 секунд таймаут
            
            const response = await fetch(`${llamaUrl}/health`, {
                method: 'GET',
                signal: controller.signal
            });
            
            clearTimeout(timeoutId);
            return response.ok;
        } catch (e) {
            if (e.name !== 'AbortError') {
                console.debug('[KV Cache Manager] Сервер недоступен:', e);
            }
            return false;
        }
    }

    // Получение имени текущего чата
    function getCurrentChatName() {
        if (typeof chat !== 'undefined' && chat) {
            return chat.name || chat.title || 'chat';
        }
        return 'chat';
    }

    // Получение ID текущего чата
    function getCurrentChatId() {
        if (typeof chat !== 'undefined' && chat && chat.id) {
            return String(chat.id);
        }
        return 'default';
    }

    // Получение всех активных слотов
    async function getActiveSlots() {
        const llamaUrl = getLlamaUrl();
        const slots = [];
        
        try {
            // Пытаемся получить список слотов через /slots
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 10000);
            
            const response = await fetch(`${llamaUrl}/slots`, {
                method: 'GET',
                signal: controller.signal
            });
            
            clearTimeout(timeoutId);
            
            if (response.ok) {
                const data = await response.json();
                if (Array.isArray(data)) {
                    for (const slot of data) {
                        const slotId = typeof slot === 'object' ? slot.id : slot;
                        if (await isSlotValid(slotId)) {
                            slots.push(slotId);
                        }
                    }
                    return slots;
                } else if (typeof data === 'object') {
                    // Если это объект с ключами-номерами слотов
                    for (const slotIdStr in data) {
                        const slotId = parseInt(slotIdStr);
                        if (!isNaN(slotId) && await isSlotValid(slotId)) {
                            slots.push(slotId);
                        }
                    }
                    return slots;
                }
            }
        } catch (e) {
            if (e.name !== 'AbortError') {
                console.debug('[KV Cache Manager] Не удалось получить список слотов через /slots:', e);
            }
        }
        
        // Fallback: перебираем слоты вручную (до 16 слотов)
        // Для групповых чатов может быть больше слотов, но начинаем с 16
        for (let slotId = 0; slotId < 16; slotId++) {
            if (await isSlotValid(slotId)) {
                slots.push(slotId);
            }
        }
        
        return slots;
    }

    // Проверка валидности слота
    async function isSlotValid(slotId) {
        if (!settings.validateCache) {
            return true; // Если проверка отключена, считаем валидным
        }
        
        const llamaUrl = getLlamaUrl();
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 секунд таймаут
            
            const response = await fetch(`${llamaUrl}/slots/${slotId}`, {
                method: 'GET',
                signal: controller.signal
            });
            
            clearTimeout(timeoutId);
            
            if (response.ok) {
                const slotInfo = await response.json();
                const nCtxUsed = slotInfo.n_ctx_used || 0;
                const nPromptTokens = slotInfo.n_prompt_tokens || 0;
                return nCtxUsed > 0 || nPromptTokens > 0;
            }
        } catch (e) {
            if (e.name !== 'AbortError') {
                console.debug(`[KV Cache Manager] Ошибка проверки слота ${slotId}:`, e);
            }
        }
        
        return false;
    }

    // Сохранение кеша для слота
    async function saveSlotCache(slotId, filename) {
        const llamaUrl = getLlamaUrl();
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 300000); // 5 минут таймаут
            
            const response = await fetch(`${llamaUrl}/slots/${slotId}?action=save`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ filename: filename }),
                signal: controller.signal
            });
            
            clearTimeout(timeoutId);
            
            if (!response.ok) {
                const errorText = await response.text();
                console.error(`[KV Cache Manager] Ошибка сохранения кеша слота ${slotId}: ${response.status} ${errorText}`);
                return false;
            }
            
            return true;
        } catch (e) {
            if (e.name === 'AbortError') {
                console.error(`[KV Cache Manager] Таймаут при сохранении кеша слота ${slotId}`);
            } else {
                console.error(`[KV Cache Manager] Ошибка сохранения кеша слота ${slotId}:`, e);
            }
            return false;
        }
    }

    // Загрузка кеша для слота
    async function loadSlotCache(slotId, filename) {
        const llamaUrl = getLlamaUrl();
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 300000); // 5 минут таймаут
            
            const response = await fetch(`${llamaUrl}/slots/${slotId}?action=restore`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ filename: filename }),
                signal: controller.signal
            });
            
            clearTimeout(timeoutId);
            
            if (!response.ok) {
                const errorText = await response.text();
                console.error(`[KV Cache Manager] Ошибка загрузки кеша слота ${slotId}: ${response.status} ${errorText}`);
                return false;
            }
            
            return true;
        } catch (e) {
            if (e.name === 'AbortError') {
                console.error(`[KV Cache Manager] Таймаут при загрузке кеша слота ${slotId}`);
            } else {
                console.error(`[KV Cache Manager] Ошибка загрузки кеша слота ${slotId}:`, e);
            }
            return false;
        }
    }

    // Извлечение номера слота из имени файла
    function extractSlotIdFromFilename(filename) {
        const match = filename.match(/_slot(\d+)_/);
        return match ? parseInt(match[1]) : null;
    }

    // Формирование имени файла для автосохранения
    function generateAutoSaveFilename(slotId) {
        const chatName = getCurrentChatName().replace(/[^a-zA-Z0-9_-]/g, '_');
        const timestamp = new Date().toISOString().replace(/[-:T]/g, '').split('.')[0].replace(/(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/, '$1$2$3$4$5$6');
        return `${chatName}_slot${slotId}_${timestamp}.bin`;
    }

    // Формирование имени файла для ручного сохранения
    function generateManualSaveFilename(userName, slotId) {
        const chatName = getCurrentChatName().replace(/[^a-zA-Z0-9_-]/g, '_');
        const safeUserName = userName.replace(/[^a-zA-Z0-9_-]/g, '_');
        const timestamp = new Date().toISOString().replace(/[-:T]/g, '').split('.')[0].replace(/(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/, '$1$2$3$4$5$6');
        return `${safeUserName}_${chatName}_slot${slotId}_${timestamp}.bin`;
    }

    // Автоматическое сохранение кеша
    async function autoSaveCache() {
        if (!settings.enabled) {
            return;
        }

        const chatId = getCurrentChatId();
        const count = messageCounters[chatId] || 0;
        
        if (count < settings.saveInterval) {
            return;
        }

        console.log(`[KV Cache Manager] Автосохранение кеша для чата ${chatId} (сообщений: ${count})`);
        
        // Проверка доступности сервера
        const isServerAvailable = await checkServerAvailability();
        if (!isServerAvailable) {
            console.warn('[KV Cache Manager] Сервер llama.cpp недоступен, пропускаем сохранение');
            if (settings.showNotifications) {
                toastr.warning('Сервер llama.cpp недоступен, сохранение пропущено');
            }
            // Не сбрасываем счетчик, чтобы попробовать снова позже
            return;
        }
        
        const slots = await getActiveSlots();
        if (slots.length === 0) {
            console.log('[KV Cache Manager] Нет активных слотов для сохранения');
            // Сбрасываем счетчик даже если нет слотов, чтобы не накапливать
            messageCounters[chatId] = 0;
            updateUI();
            return;
        }

        let savedCount = 0;
        let errors = [];
        
        for (const slotId of slots) {
            try {
                const filename = generateAutoSaveFilename(slotId);
                if (await saveSlotCache(slotId, filename)) {
                    savedCount++;
                } else {
                    errors.push(`Слот ${slotId}`);
                }
            } catch (e) {
                console.error(`[KV Cache Manager] Ошибка при сохранении слота ${slotId}:`, e);
                errors.push(`Слот ${slotId}: ${e.message}`);
            }
        }

        if (savedCount > 0) {
            // Сброс счетчика
            messageCounters[chatId] = 0;
            
            // Ротация файлов
            try {
                await rotateAutoSaveFiles();
            } catch (e) {
                console.error('[KV Cache Manager] Ошибка при ротации файлов:', e);
            }
            
            // Обновление статистики
            try {
                await updateStatistics();
            } catch (e) {
                console.error('[KV Cache Manager] Ошибка при обновлении статистики:', e);
            }
            
            if (settings.showNotifications) {
                if (errors.length > 0) {
                    toastr.warning(`Сохранено ${savedCount} из ${slots.length} слотов. Ошибки: ${errors.join(', ')}`);
                } else {
                    toastr.success(`Сохранено ${savedCount} слотов`);
                }
            }
        } else {
            // Если не удалось сохранить, не сбрасываем счетчик
            // чтобы попробовать снова при следующем сообщении
            if (settings.showNotifications) {
                toastr.error(`Не удалось сохранить кеш. Ошибки: ${errors.join(', ')}`);
            }
        }
        
        updateUI();
    }

    // Ручное сохранение с именем
    async function manualSaveCache(userName) {
        if (!userName || !userName.trim()) {
            if (settings.showNotifications) {
                toastr.error('Необходимо указать имя для сохранения');
            }
            return false;
        }

        console.log(`[KV Cache Manager] Ручное сохранение кеша с именем "${userName}"`);
        
        // Проверка доступности сервера
        const isServerAvailable = await checkServerAvailability();
        if (!isServerAvailable) {
            if (settings.showNotifications) {
                toastr.error('Сервер llama.cpp недоступен');
            }
            return false;
        }
        
        // Получаем все активные слоты (для групповых чатов может быть несколько)
        const slots = await getActiveSlots();
        if (slots.length === 0) {
            if (settings.showNotifications) {
                toastr.warning('Нет активных слотов для сохранения');
            }
            return false;
        }

        console.log(`[KV Cache Manager] Найдено ${slots.length} активных слотов: ${slots.join(', ')}`);

        let savedCount = 0;
        let errors = [];
        
        for (const slotId of slots) {
            try {
                const filename = generateManualSaveFilename(userName.trim(), slotId);
                if (await saveSlotCache(slotId, filename)) {
                    savedCount++;
                } else {
                    errors.push(`Слот ${slotId}`);
                }
            } catch (e) {
                console.error(`[KV Cache Manager] Ошибка при сохранении слота ${slotId}:`, e);
                errors.push(`Слот ${slotId}: ${e.message}`);
            }
        }

        if (savedCount > 0) {
            // Обновление статистики после сохранения
            try {
                await updateStatistics();
            } catch (e) {
                console.error('[KV Cache Manager] Ошибка при обновлении статистики:', e);
            }
            
            if (settings.showNotifications) {
                if (errors.length > 0) {
                    toastr.warning(`Сохранено ${savedCount} из ${slots.length} слотов с именем "${userName}". Ошибки: ${errors.join(', ')}`);
                } else {
                    toastr.success(`Сохранено ${savedCount} из ${slots.length} слотов с именем "${userName}"`);
                }
            }
            return true;
        } else {
            if (settings.showNotifications) {
                toastr.error(`Не удалось сохранить кеш. Ошибки: ${errors.join(', ')}`);
            }
            return false;
        }
    }

    // Получение списка файлов автосохранения для текущего чата
    async function getAutoSaveFiles() {
        const chatName = getCurrentChatName().replace(/[^a-zA-Z0-9_-]/g, '_');
        const pattern = `${chatName}_slot\\d+_\\d+\\.bin$`;
        const regex = new RegExp(pattern);
        
        // Пытаемся получить список файлов через API SillyTavern
        // Если API недоступно, возвращаем пустой массив
        try {
            // В SillyTavern может быть API для получения списка файлов кеша
            // Пока используем заглушку - в реальной реализации нужно будет
            // использовать API SillyTavern для получения списка файлов
            if (typeof extension_api !== 'undefined' && extension_api.getCacheFiles) {
                const files = await extension_api.getCacheFiles();
                return files.filter(f => regex.test(f.name) && !f.name.startsWith('backup_'));
            }
        } catch (e) {
            console.debug('[KV Cache Manager] Не удалось получить список файлов через API:', e);
        }
        
        // Fallback: возвращаем пустой массив
        // В реальной реализации нужно будет использовать API SillyTavern
        return [];
    }

    // Ротация файлов автосохранения
    async function rotateAutoSaveFiles() {
        if (settings.maxFiles <= 0) {
            return; // Ротация отключена
        }

        try {
            const files = await getAutoSaveFiles();
            
            if (files.length <= settings.maxFiles) {
                return; // Лимит не превышен
            }

            // Сортируем файлы по timestamp (из имени файла)
            files.sort((a, b) => {
                const timestampA = extractTimestampFromFilename(a.name);
                const timestampB = extractTimestampFromFilename(b.name);
                if (!timestampA || !timestampB) return 0;
                return timestampA.localeCompare(timestampB);
            });

            // Удаляем самые старые файлы
            const filesToDelete = files.slice(0, files.length - settings.maxFiles);
            
            for (const file of filesToDelete) {
                try {
                    // Удаление через API SillyTavern
                    if (typeof extension_api !== 'undefined' && extension_api.deleteCacheFile) {
                        await extension_api.deleteCacheFile(file.name);
                        console.log(`[KV Cache Manager] Удален старый файл: ${file.name}`);
                    }
                } catch (e) {
                    console.warn(`[KV Cache Manager] Не удалось удалить файл ${file.name}:`, e);
                }
            }
        } catch (e) {
            console.error('[KV Cache Manager] Ошибка при ротации файлов:', e);
        }
    }

    // Извлечение timestamp из имени файла
    function extractTimestampFromFilename(filename) {
        const match = filename.match(/_(\d{14})\.bin$/);
        return match ? match[1] : null;
    }

    // Автозагрузка кеша при переключении на чат
    async function autoLoadCache() {
        if (!settings.autoLoadOnChatSwitch) {
            return;
        }

        const chatId = getCurrentChatId();
        const chatName = getCurrentChatName();
        
        console.log(`[KV Cache Manager] Автозагрузка кеша для чата ${chatId} (${chatName})`);
        
        try {
            const files = await getAllCacheFiles();
            
            if (files.length === 0) {
                console.log('[KV Cache Manager] Нет сохраненных файлов для автозагрузки');
                return;
            }
            
            // Сортируем файлы по timestamp (новые первыми)
            files.sort((a, b) => {
                const timestampA = extractTimestampFromFilename(a.name);
                const timestampB = extractTimestampFromFilename(b.name);
                if (!timestampA || !timestampB) return 0;
                return timestampB.localeCompare(timestampA);
            });
            
            // Группируем файлы по timestamp (все слоты одного сохранения)
            const timestampGroups = {};
            for (const file of files) {
                const timestamp = extractTimestampFromFilename(file.name);
                if (timestamp) {
                    if (!timestampGroups[timestamp]) {
                        timestampGroups[timestamp] = [];
                    }
                    timestampGroups[timestamp].push(file);
                }
            }
            
            // Загружаем последнее сохранение (самый новый timestamp)
            const timestamps = Object.keys(timestampGroups).sort().reverse();
            if (timestamps.length > 0) {
                const latestTimestamp = timestamps[0];
                const filesToLoad = timestampGroups[latestTimestamp];
                await loadCacheFiles(filesToLoad);
            }
        } catch (e) {
            console.error('[KV Cache Manager] Ошибка при автозагрузке кеша:', e);
        }
    }

    // Обработка завершения генерации сообщения
    function handleMessageComplete() {
        const chatId = getCurrentChatId();
        messageCounters[chatId] = (messageCounters[chatId] || 0) + 1;
        updateUI();
        
        // Проверяем, нужно ли сохранять
        const count = messageCounters[chatId] || 0;
        if (count >= settings.saveInterval) {
            autoSaveCache();
        }
    }

    // Подписка на события
    function subscribeToEvents() {
        // Событие завершения генерации сообщения
        // В SillyTavern обычно используется eventSource для SSE
        if (typeof eventSource !== 'undefined') {
            eventSource.addEventListener('message', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    // Проверяем различные типы событий завершения генерации
                    if (data.type === 'streamingComplete' || 
                        data.type === 'messageComplete' ||
                        (data.type === 'message' && data.finish_reason) ||
                        data.event === 'streamingComplete' ||
                        data.event === 'messageComplete') {
                        handleMessageComplete();
                    }
                } catch (e) {
                    // Игнорируем ошибки парсинга
                }
            });
        }

        // Альтернативный способ через события DOM
        document.addEventListener('messageComplete', handleMessageComplete);
        document.addEventListener('streamingComplete', handleMessageComplete);

        // Событие переключения чата
        // Отслеживаем изменения chat.id через polling
        let lastChatId = getCurrentChatId();
        setInterval(() => {
            const currentChatId = getCurrentChatId();
            if (currentChatId !== lastChatId) {
                lastChatId = currentChatId;
                // Сброс счетчика при переключении чата
                messageCounters[currentChatId] = messageCounters[currentChatId] || 0;
                autoLoadCache();
                updateUI();
                updateStatistics();
            }
        }, 1000);

        // Альтернативный способ через jQuery события (если доступны)
        if (typeof jQuery !== 'undefined') {
            jQuery(document).on('chatChanged', () => {
                const chatId = getCurrentChatId();
                messageCounters[chatId] = messageCounters[chatId] || 0;
                autoLoadCache();
                updateUI();
                updateStatistics();
            });
            
            // События завершения генерации через jQuery
            jQuery(document).on('messageComplete streamingComplete', handleMessageComplete);
        }

        // Событие через window
        window.addEventListener('chatChanged', () => {
            const chatId = getCurrentChatId();
            messageCounters[chatId] = messageCounters[chatId] || 0;
            autoLoadCache();
            updateUI();
            updateStatistics();
        });
        
        window.addEventListener('messageComplete', handleMessageComplete);
        window.addEventListener('streamingComplete', handleMessageComplete);
    }

    // Экспорт функций для использования в UI
    window.kvCacheManager = {
        settings: settings,
        saveSettings: saveSettings,
        loadSettings: loadSettings,
        loadSettingsToUI: loadSettingsToUI,
        updateUI: updateUI,
        updateStatistics: updateStatistics,
        manualSaveCache: manualSaveCache,
        autoSaveCache: autoSaveCache,
        autoLoadCache: autoLoadCache,
        getActiveSlots: getActiveSlots,
        getCurrentChatName: getCurrentChatName,
        getCurrentChatId: getCurrentChatId
    };

    // Хук для SillyTavern расширений
    if (typeof registerExtension !== 'undefined') {
        registerExtension({
            name: extensionName,
            settingsHtml: async () => {
                const response = await fetch(`/scripts/extensions/${extensionName}/settings.html`);
                return await response.text();
            },
            onSettingsLoad: () => {
                loadSettingsToUI();
            },
            onSettingsSave: () => {
                // Настройки уже сохранены через обработчики событий
            }
        });
    }

    // Инициализация при загрузке
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();

