// ==============================================================================
// Non-blocking Toast Notification Helper
// ==============================================================================
function showToast(message, type = 'info') {
    const toast = document.getElementById('toastNotification');
    if (!toast) {
        console.log(`[${type}] ${message}`);
        return;
    }
    toast.textContent = message;
    toast.className = `toast-notification show ${type}`;
    if (window._toastTimer) clearTimeout(window._toastTimer);
    window._toastTimer = setTimeout(() => {
        toast.classList.remove('show');
    }, 3200);
}

// Bulletproof copy helper (works in HTTPS, HTTP, and local network IPs)
async function copyToClipboard(text) {
    if (!text) return false;
    // 1. Try modern clipboard API if available in secure context
    if (window.isSecureContext && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {
            console.warn('navigator.clipboard.writeText failed, using fallback', e);
        }
    }

    // 2. Reliable fallback using document.execCommand('copy')
    try {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.top = '0';
        textarea.style.left = '0';
        textarea.style.position = 'fixed';
        textarea.style.width = '2em';
        textarea.style.height = '2em';
        textarea.style.padding = '0';
        textarea.style.border = 'none';
        textarea.style.outline = 'none';
        textarea.style.boxShadow = 'none';
        textarea.style.background = 'transparent';
        textarea.setAttribute('readonly', '');
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        textarea.setSelectionRange(0, textarea.value.length);
        const successful = document.execCommand('copy');
        document.body.removeChild(textarea);
        return successful;
    } catch (err) {
        console.error('execCommand copy failed', err);
        return false;
    }
}

// Global ESC key listener to close modals
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (typeof closeClientModal === 'function') closeClientModal();
        if (typeof closeCredentialsModal === 'function') closeCredentialsModal();
        if (typeof closeImportModal === 'function') closeImportModal();
    }
});

// ==============================================================================
// L2TP Web Hub — Frontend Application Logic (Styled exactly as OpenVPN Web)
// ==============================================================================

let allClients = [];
let currentTab = 'clients';
let activeConfigKey = 'ipsec.conf';

document.addEventListener('DOMContentLoaded', () => {
    initThemeAndUI();
    loadClients();
    loadServiceStatus();
    fetchNextIp('inlineClientIp');

    // Auto-refresh stats and clients every 8 seconds
    setInterval(() => {
        loadClients(true);
        loadServiceStatus(true);
    }, 8000);
});

// ==============================================================================
// Theme, Palette & Layout Switchers (from OpenVPN Web)
// ==============================================================================
function initThemeAndUI() {
    // Theme toggle
    const themeToggleBtn = document.getElementById('theme-toggle');
    if (themeToggleBtn) {
        const sunIcon = themeToggleBtn.querySelector('.sun-icon');
        const moonIcon = themeToggleBtn.querySelector('.moon-icon');

        if (document.documentElement.classList.contains('light-theme')) {
            if (sunIcon) sunIcon.classList.add('hidden');
            if (moonIcon) moonIcon.classList.remove('hidden');
        }

        themeToggleBtn.addEventListener('click', () => {
            document.documentElement.classList.toggle('light-theme');
            const isLight = document.documentElement.classList.contains('light-theme');
            localStorage.setItem('theme', isLight ? 'light' : 'dark');
            if (isLight) {
                if (sunIcon) sunIcon.classList.add('hidden');
                if (moonIcon) moonIcon.classList.remove('hidden');
            } else {
                if (sunIcon) sunIcon.classList.remove('hidden');
                if (moonIcon) moonIcon.classList.add('hidden');
            }
        });
    }

    // Palette picker
    const toggleBtn = document.getElementById('palette-toggle-btn');
    const menu = document.getElementById('paletteMenu');
    if (toggleBtn && menu) {
        const savedPalette = localStorage.getItem('theme_palette') || 'classic';
        menu.querySelectorAll('.palette-option').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.paletteVal === savedPalette);
            btn.addEventListener('click', () => {
                const palette = btn.dataset.paletteVal;
                document.documentElement.setAttribute('data-palette', palette);
                localStorage.setItem('theme_palette', palette);
                menu.querySelectorAll('.palette-option').forEach(b => b.classList.toggle('active', b === btn));
                menu.classList.add('hidden');
            });
        });

        toggleBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            menu.classList.toggle('hidden');
        });

        document.addEventListener('click', (e) => {
            if (!menu.contains(e.target) && !toggleBtn.contains(e.target)) {
                menu.classList.add('hidden');
            }
        });
    }

    // Layout toggle (Full width vs 1440px)
    const layoutToggle = document.getElementById('layout-toggle');
    if (layoutToggle) {
        const expandIcon = layoutToggle.querySelector('.expand-icon');
        const compressIcon = layoutToggle.querySelector('.compress-icon');

        function updateLayoutIcons(isFull) {
            if (isFull) {
                layoutToggle.title = 'Компактный вид';
                if (expandIcon) expandIcon.classList.add('hidden');
                if (compressIcon) compressIcon.classList.remove('hidden');
            } else {
                layoutToggle.title = 'На всю ширину (100%)';
                if (expandIcon) expandIcon.classList.remove('hidden');
                if (compressIcon) compressIcon.classList.add('hidden');
            }
        }

        const isFullInitial = localStorage.getItem('layout_width') === 'full';
        updateLayoutIcons(isFullInitial);

        layoutToggle.addEventListener('click', () => {
            const isFull = document.documentElement.classList.toggle('layout-fullwidth');
            localStorage.setItem('layout_width', isFull ? 'full' : 'compact');
            updateLayoutIcons(isFull);
        });
    }
}

// ==============================================================================
// Tab Navigation (matches OpenVPN Web switchNavTab)
// ==============================================================================
function switchNavTab(tabName) {
    currentTab = tabName;
    toggleMobileSidebar(false);

    // Sidebar nav buttons
    const navClientsBtn = document.getElementById('navClientsBtn');
    const navAuditBtn = document.getElementById('navAuditBtn');
    const navServicesBtn = document.getElementById('navServicesBtn');
    const navServerConfigBtn = document.getElementById('navServerConfigBtn');
    const navUsersBtn = document.getElementById('navUsersBtn');

    if (navClientsBtn) navClientsBtn.classList.toggle('active', tabName === 'clients');
    if (navAuditBtn) navAuditBtn.classList.toggle('active', tabName === 'audit');
    if (navServicesBtn) navServicesBtn.classList.toggle('active', tabName === 'services');
    if (navServerConfigBtn) navServerConfigBtn.classList.toggle('active', tabName === 'server-config');
    if (navUsersBtn) navUsersBtn.classList.toggle('active', tabName === 'users');

    // Tab sections (using .hidden)
    const clientsSec = document.getElementById('tabSectionClients');
    const auditSec = document.getElementById('tabSectionAudit');
    const servicesSec = document.getElementById('tabSectionServices');
    const serverConfigSec = document.getElementById('tabSectionServerConfig');
    const usersSec = document.getElementById('tabSectionUsers');

    if (clientsSec) clientsSec.classList.toggle('hidden', tabName !== 'clients');
    if (auditSec) auditSec.classList.toggle('hidden', tabName !== 'audit');
    if (servicesSec) servicesSec.classList.toggle('hidden', tabName !== 'services');
    if (serverConfigSec) serverConfigSec.classList.toggle('hidden', tabName !== 'server-config');
    if (usersSec) usersSec.classList.toggle('hidden', tabName !== 'users');

    if (tabName === 'server-config') {
        loadServerConfigForm();
        loadConfigFile(activeConfigKey);
    } else if (tabName === 'audit') {
        loadAuditLogs();
    } else if (tabName === 'users') {
        loadUsersTable();
    } else if (tabName === 'services') {
        loadServiceStatus();
    }
}

function toggleMobileSidebar(show) {
    const sidebar = document.getElementById('sidebarNav');
    const backdrop = document.getElementById('mobileBackdrop');
    if (!sidebar || !backdrop) return;
    if (show) {
        sidebar.classList.add('open');
        backdrop.classList.add('open');
    } else {
        sidebar.classList.remove('open');
        backdrop.classList.remove('open');
    }
}

// ==============================================================================
// Clients Management
// ==============================================================================
async function loadClients(silent = false) {
    try {
        const res = await fetch('/api/clients');
        const data = await res.json();
        if (res.ok) {
            allClients = data.clients || [];
            updateClientStats();
            renderClientsTable();
        }
    } catch (err) {
        if (!silent) console.error('Error loading clients:', err);
    }
}

function updateClientStats() {
    const total = allClients.length;
    const online = allClients.filter(c => c.online).length;

    const elTotal = document.getElementById('statTotalClients');
    const elOnline = document.getElementById('statOnlineClients');
    const elBadge = document.getElementById('clientCounterBadge');

    if (elTotal) elTotal.textContent = total;
    if (elOnline) elOnline.textContent = online;
    if (elBadge) elBadge.textContent = `${total} клиентов (${online} онлайн)`;
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function renderClientsTable() {
    const tbody = document.getElementById('clientsTableBody');
    if (!tbody) return;

    const searchInput = document.getElementById('searchInput');
    const searchVal = searchInput ? searchInput.value.toLowerCase().trim() : '';

    let filtered = allClients.filter(c => {
        if (searchVal) {
            const matchUser = c.username.toLowerCase().includes(searchVal);
            const matchDesc = (c.description || '').toLowerCase().includes(searchVal);
            const matchIp = (c.assigned_ip || '').toLowerCase().includes(searchVal);
            return matchUser || matchDesc || matchIp;
        }
        return true;
    });

    const elBadge = document.getElementById('clientCounterBadge');
    if (elBadge) {
        const onlineCount = allClients.filter(c => c.online).length;
        if (searchVal) {
            elBadge.textContent = `${filtered.length} из ${allClients.length} клиентов`;
        } else {
            elBadge.textContent = `${allClients.length} клиентов (${onlineCount} онлайн)`;
        }
    }

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center" style="padding: 32px; color: var(--text-muted);">${searchVal ? 'По запросу "' + escapeHtml(searchVal) + '" ничего не найдено' : 'Клиенты не найдены'}</td></tr>`;
        return;
    }

    tbody.innerHTML = filtered.map(c => {
        const statusHtml = c.online 
            ? `<span class="status-indicator-dot online" title="Подключен (Онлайн)"></span>` 
            : (!c.enabled ? `<span class="status-indicator-dot disabled" title="Отключен"></span>` : `<span class="status-indicator-dot offline" title="Оффлайн"></span>`);

        const rxFormatted = formatBytes(c.rx_bytes);
        const txFormatted = formatBytes(c.tx_bytes);

        let deviceBadge = '';
        if (c.device_type === 'synology') {
            deviceBadge = '<span class="badge-role" style="background: rgba(16, 185, 129, 0.15); color: #10b981; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">Synology</span>';
        } else if (c.device_type === 'mikrotik') {
            deviceBadge = '<span class="badge-role" style="background: rgba(59, 130, 246, 0.15); color: #3b82f6; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">MikroTik</span>';
        }

        const safeUsername = encodeURIComponent(c.username);
        const safeIp = encodeURIComponent(c.assigned_ip);

        return `
            <tr class="client-row-interactive" onclick="handleClientRowClick(event, decodeURIComponent('${safeUsername}'))" title="Нажмите, чтобы открыть реквизиты Synology DSM" style="${!c.enabled ? 'opacity: 0.65;' : ''}">
                <td>${statusHtml}</td>
                <td>
                    <strong class="client-name-link" style="font-size: 13.5px;">${escapeHtml(c.username)}</strong>
                    ${deviceBadge}
                </td>
                <td><span style="color: var(--text-muted); font-size: 13px;">${escapeHtml(c.description || '—')}</span></td>
                <td><code style="background: var(--bg-offset); padding: 3px 6px; border-radius: 4px; font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main);">${escapeHtml(c.assigned_ip)}</code></td>
                <td><span style="color: var(--text-muted); font-size: 12px; font-family: 'JetBrains Mono', monospace;">${c.online ? c.interface : '—'}</span></td>
                <td>
                    <span style="font-size: 12px; font-family: 'JetBrains Mono', monospace;">
                        <span style="color: var(--success);">&darr; ${rxFormatted}</span> / <span style="color: var(--info);">&uarr; ${txFormatted}</span>
                    </span>
                </td>
                <td class="text-right">
                    <div style="display: inline-flex; gap: 6px; justify-content: flex-end;">
                        <button type="button" class="btn-action-icon" title="Реквизиты и памятка DSM" onclick="showCredentials(decodeURIComponent('${safeUsername}'))"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-1.5 1.5L14 9l-1.5-1.5M10 14a4 4 0 1 1-8 0 4 4 0 0 1 8 0z"/></svg></button>
                        ${c.online ? `<button type="button" class="btn-action-icon" style="color: var(--warning);" title="Сбросить туннель" onclick="disconnectTunnel(decodeURIComponent('${safeIp}'))"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg></button>` : ''}
                        <button type="button" class="btn-action-icon" title="Редактировать" onclick="editClient(${c.id})"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></button>
                        <button type="button" class="btn-action-icon" title="${c.enabled ? 'Отключить' : 'Включить'}" onclick="toggleClient(${c.id})">${c.enabled ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>' : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>'}</button>
                        <button type="button" class="btn-action-icon" style="color: var(--danger);" title="Удалить" onclick="deleteClient(${c.id}, decodeURIComponent('${safeUsername}'))"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function handleClientRowClick(e, username) {
    if (e.target.closest('button, a, input, select, textarea, .btn-action-icon')) {
        return;
    }
    showCredentials(username);
}

function filterClients() {
    renderClientsTable();
}

// ==============================================================================
// Client Modal & Actions
// ==============================================================================
function openCreateClientModal() {
    document.getElementById('editClientId').value = '';
    document.getElementById('clientModalTitle').textContent = 'Создать учетную запись клиента';
    document.getElementById('clientUsername').value = '';
    document.getElementById('clientUsername').disabled = false;
    document.getElementById('clientDescription').value = '';
    document.getElementById('clientDeviceType').value = 'synology';
    document.getElementById('clientPassword').value = '';

    fetchNextIp();
    document.getElementById('clientModal').classList.remove('hidden');
}

function closeClientModal() {
    document.getElementById('clientModal').classList.add('hidden');
}

async function fetchNextIp(targetId = 'clientAssignedIp') {
    try {
        const res = await fetch('/api/clients/next-ip');
        const data = await res.json();
        if (data.next_ip) {
            const el = document.getElementById(targetId);
            if (el) el.value = data.next_ip;
        }
    } catch (err) {
        console.error('Failed to get next IP:', err);
    }
}

function generatePasswordInput(targetId = 'clientPassword') {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%^&*';
    let pass = '';
    for (let i = 0; i < 16; i++) {
        pass += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    const el = document.getElementById(targetId);
    if (el) el.value = pass;
    return pass;
}

function generatePasswordInline() {
    generatePasswordInput('inlineClientPassword');
}

function toggleAdvancedClientOptions() {
    const sec = document.getElementById('inlineAdvancedOptions');
    const btn = document.getElementById('advClientOptionsBtn');
    if (!sec) return;
    const isHidden = sec.classList.toggle('hidden');
    if (btn) {
        btn.textContent = isHidden ? '+ Дополнительные параметры' : '− Скрыть параметры';
    }
}

async function createInlineClient() {
    const userEl = document.getElementById('inlineClientUsername');
    const ipEl = document.getElementById('inlineClientIp');
    const passEl = document.getElementById('inlineClientPassword');
    const descEl = document.getElementById('inlineClientDesc');
    const devEl = document.getElementById('inlineClientDevice');
    const msgEl = document.getElementById('inlineActionMsg');
    const btn = document.getElementById('createInlineClientBtn');

    const username = userEl ? userEl.value.trim() : '';
    const assigned_ip = ipEl ? ipEl.value.trim() : '';
    const password = passEl ? passEl.value.trim() : '';
    const description = descEl ? descEl.value.trim() : '';
    const device_type = devEl ? devEl.value : 'synology';

    if (!username) {
        if (msgEl) {
            msgEl.style.display = 'block';
            msgEl.style.color = 'var(--danger)';
            msgEl.textContent = 'Укажите логин клиента';
        }
        return;
    }

    if (btn) btn.disabled = true;

    try {
        const res = await fetch('/api/clients', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ username, assigned_ip, password, description, device_type })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            if (userEl) userEl.value = '';
            if (passEl) passEl.value = '';
            if (descEl) descEl.value = '';
            if (msgEl) {
                msgEl.style.display = 'block';
                msgEl.style.color = 'var(--success)';
                msgEl.textContent = `Клиент ${username} успешно создан!`;
                setTimeout(() => { msgEl.style.display = 'none'; }, 3000);
            }
            loadClients();
            fetchNextIp('inlineClientIp');
            showCredentials(username);
        } else {
            if (msgEl) {
                msgEl.style.display = 'block';
                msgEl.style.color = 'var(--danger)';
                msgEl.textContent = data.error || 'Ошибка создания клиента';
            }
        }
    } catch (err) {
        if (msgEl) {
            msgEl.style.display = 'block';
            msgEl.style.color = 'var(--danger)';
            msgEl.textContent = 'Ошибка связи: ' + err;
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function editClient(id) {
    const client = allClients.find(c => c.id === id);
    if (!client) return;

    document.getElementById('editClientId').value = client.id;
    document.getElementById('clientModalTitle').textContent = `Редактировать: ${client.username}`;
    document.getElementById('clientUsername').value = client.username;
    document.getElementById('clientUsername').disabled = true;
    document.getElementById('clientDescription').value = client.description || '';
    document.getElementById('clientDeviceType').value = client.device_type || 'synology';
    document.getElementById('clientAssignedIp').value = client.assigned_ip || '';
    document.getElementById('clientPassword').value = client.password || '';

    document.getElementById('clientModal').classList.remove('hidden');
}

async function handleClientSubmit(e) {
    e.preventDefault();
    const saveBtn = document.getElementById('saveClientBtn');
    saveBtn.disabled = true;

    const id = document.getElementById('editClientId').value;
    const username = document.getElementById('clientUsername').value.trim();
    const description = document.getElementById('clientDescription').value.trim();
    const device_type = document.getElementById('clientDeviceType').value;
    const assigned_ip = document.getElementById('clientAssignedIp').value.trim();
    const password = document.getElementById('clientPassword').value.trim();

    const isEdit = !!id;
    const url = isEdit ? '/api/clients/update' : '/api/clients';
    const payload = isEdit 
        ? { id: parseInt(id), description, assigned_ip, password }
        : { username, description, device_type, assigned_ip, password };

    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok && data.success) {
            closeClientModal();
            loadClients();
            if (!isEdit) {
                showCredentials(username);
            }
        } else {
            alert(data.error || 'Ошибка при сохранении данных клиента');
        }
    } catch (err) {
        alert('Ошибка связи с сервером: ' + err);
    } finally {
        saveBtn.disabled = false;
    }
}

async function toggleClient(id) {
    try {
        const res = await fetch('/api/clients/toggle', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id })
        });
        if (res.ok) {
            loadClients();
        }
    } catch (err) {
        alert('Ошибка переключения статуса: ' + err);
    }
}

async function deleteClient(id, username) {
    if (!confirm(`Вы действительно хотите удалить учетную запись ${username}? Туннель будет разорван.`)) {
        return;
    }
    try {
        const res = await fetch('/api/clients/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id })
        });
        if (res.ok) {
            loadClients();
        } else {
            const data = await res.json();
            alert(data.error || 'Ошибка при удалении');
        }
    } catch (err) {
        alert('Ошибка удаления: ' + err);
    }
}

async function disconnectTunnel(assigned_ip) {
    if (!confirm(`Принудительно сбросить туннель для IP ${assigned_ip}?`)) {
        return;
    }
    try {
        const res = await fetch('/api/clients/disconnect', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ assigned_ip })
        });
        const data = await res.json();
        if (data.success) {
            loadClients();
        } else {
            alert('Не удалось сбросить туннель');
        }
    } catch (err) {
        alert('Ошибка: ' + err);
    }
}

// ==============================================================================
// Credentials Modal
// ==============================================================================
async function showCredentials(username) {
    try {
        const res = await fetch(`/api/clients/${encodeURIComponent(username)}/credentials`);
        const data = await res.json();
        if (res.ok) {
            document.getElementById('credServer').textContent = data.server;
            document.getElementById('credPsk').textContent = data.psk;
            document.getElementById('credUsername').textContent = data.username;
            document.getElementById('credPassword').textContent = data.password;
            document.getElementById('credIp').textContent = data.assigned_ip;

            window.currentCredData = data;
            document.getElementById('credentialsModal').classList.remove('hidden');
        } else {
            alert(data.error || 'Клиент не найден');
        }
    } catch (err) {
        alert('Ошибка получения данных: ' + err);
    }
}

function closeCredentialsModal() {
    document.getElementById('credentialsModal').classList.add('hidden');
}

async function copyText(elemId) {
    const el = document.getElementById(elemId);
    if (!el) return;
    const text = el.textContent.trim();
    const ok = await copyToClipboard(text);
    if (ok) {
        showToast('Скопировано: ' + text, 'success');
    } else {
        showToast('Не удалось скопировать', 'error');
    }
}

async function copyAllCredentials() {
    if (!window.currentCredData) return;
    const c = window.currentCredData;
    const text = `Реквизиты подключения L2TP/IPSec:
----------------------------------------
Сервер: ${c.server}
Общий ключ (PSK): ${c.psk}
Пользователь: ${c.username}
Пароль: ${c.password}
Выделенный IP: ${c.assigned_ip}
Аутентификация: MS-CHAP v2
----------------------------------------
Важно: В свойствах подключения Synology DSM снимите галочку "Использовать шлюз удаленной сети".`;

    const ok = await copyToClipboard(text);
    if (ok) {
        showToast('Все реквизиты скопированы в буфер обмена!', 'success');
    } else {
        showToast('Не удалось скопировать реквизиты', 'error');
    }
}

// ==============================================================================
// Config Editor
// ==============================================================================
async function loadConfigFile(key) {
    activeConfigKey = key;
    ['ipsec_conf', 'l2tp_secrets', 'xl2tpd_conf', 'options_xl2tpd', 'iptables_rules'].forEach(id => {
        const btn = document.getElementById(`tabBtn_${id}`);
        if (btn) btn.classList.remove('active');
    });

    const activeBtnKey = key.replace('.', '_');
    const activeBtn = document.getElementById(`tabBtn_${activeBtnKey}`);
    if (activeBtn) activeBtn.classList.add('active');

    const textarea = document.getElementById('configEditorTextarea');
    if (!textarea) return;
    textarea.value = 'Загрузка...';

    try {
        const res = await fetch(`/api/server/config?key=${encodeURIComponent(key)}`);
        const data = await res.json();
        if (res.ok) {
            textarea.value = data.content || '';
        } else {
            textarea.value = `# Ошибка: ${data.error}`;
        }
    } catch (err) {
        textarea.value = `# Ошибка связи: ${err}`;
    }
}

// ==============================================================================
// Server Configuration Management (Structured form matching OpenVPN Web)
// ==============================================================================
async function loadServerConfigForm() {
    const statusMsg = document.getElementById('serverConfigStatusMsg');
    if (statusMsg) {
        statusMsg.style.display = 'none';
        statusMsg.textContent = '';
    }

    try {
        const res = await fetch(`/api/server/config?_=${new Date().getTime()}`);
        const data = await res.json();
        if (res.ok && data.success && data.config) {
            const cfg = data.config;
            const setVal = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.value = val !== undefined && val !== null ? val : '';
            };
            const setChecked = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.checked = !!val;
            };

            setVal('cfgServerHost', cfg.server_host);
            setVal('cfgLocalIp', cfg.local_ip || '10.18.12.1');
            setVal('cfgNetmask', cfg.netmask || '255.255.255.0');
            setVal('cfgIpsecPsk', cfg.ipsec_psk);
            setVal('cfgClientPoolStart', cfg.ip_pool_start || '10.18.15.1');
            setVal('cfgClientPoolEnd', cfg.ip_pool_end || '10.18.15.254');
            setVal('cfgDns1', cfg.dns1 || '10.18.8.90');
            setVal('cfgDns2', cfg.dns2 || '1.1.1.1');
            setVal('cfgMtu', cfg.mtu || 1360);
            setVal('cfgMru', cfg.mru || 1360);
            setChecked('cfgProxyArp', cfg.proxy_arp !== false);
            setVal('cfgZabbixSubnet', cfg.zabbix_subnet || '10.18.8.0/22');
            setVal('cfgTcpMss', cfg.tcp_mss || 1300);
        }
    } catch (err) {
        console.error('Failed to load server config:', err);
    }
}

async function saveServerConfigForm() {
    const statusMsg = document.getElementById('serverConfigStatusMsg');
    const btn = document.getElementById('saveServerConfigBtn');

    if (!confirm('Применить и сохранить новую конфигурацию сервера? Службы IPsec и xl2tpd будут автоматически перезапущены.')) {
        return;
    }

    const payload = {
        server_host: document.getElementById('cfgServerHost')?.value.trim() || '',
        local_ip: document.getElementById('cfgLocalIp')?.value.trim() || '10.18.12.1',
        netmask: document.getElementById('cfgNetmask')?.value.trim() || '255.255.255.0',
        ipsec_psk: document.getElementById('cfgIpsecPsk')?.value.trim() || '',
        ip_pool_start: document.getElementById('cfgClientPoolStart')?.value.trim() || '10.18.15.1',
        ip_pool_end: document.getElementById('cfgClientPoolEnd')?.value.trim() || '10.18.15.254',
        dns1: document.getElementById('cfgDns1')?.value.trim() || '',
        dns2: document.getElementById('cfgDns2')?.value.trim() || '',
        mtu: parseInt(document.getElementById('cfgMtu')?.value || '1360'),
        mru: parseInt(document.getElementById('cfgMru')?.value || '1360'),
        proxy_arp: !!document.getElementById('cfgProxyArp')?.checked,
        zabbix_subnet: document.getElementById('cfgZabbixSubnet')?.value.trim() || '10.18.8.0/22',
        tcp_mss: parseInt(document.getElementById('cfgTcpMss')?.value || '1300')
    };

    if (btn) btn.disabled = true;
    if (statusMsg) {
        statusMsg.style.display = 'block';
        statusMsg.style.color = 'var(--text-muted)';
        statusMsg.textContent = 'Применение конфигурации и перезапуск служб...';
    }

    try {
        const res = await fetch('/api/server/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok && data.success) {
            if (statusMsg) {
                statusMsg.style.color = 'var(--success)';
                statusMsg.textContent = '✓ Конфигурация успешно сохранена и службы обновлены!';
            }
            setTimeout(() => {
                loadServerConfigForm();
                loadServiceStatus();
            }, 1500);
        } else {
            if (statusMsg) {
                statusMsg.style.color = 'var(--danger)';
                statusMsg.textContent = `Ошибка: ${data.error || 'Не удалось сохранить конфигурацию'}`;
            }
        }
    } catch (err) {
        if (statusMsg) {
            statusMsg.style.color = 'var(--danger)';
            statusMsg.textContent = 'Ошибка сети: ' + err;
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function generatePsk(targetInputId = 'cfgIpsecPsk') {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%^*()_+';
    let psk = '';
    for (let i = 0; i < 20; i++) {
        psk += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    const el = document.getElementById(targetInputId);
    if (el) el.value = psk;
}

async function saveActiveConfig() {
    const content = document.getElementById('configEditorTextarea').value;
    if (!confirm(`Сохранить изменения в ${activeConfigKey}? Автоматически будет создан бэкап.`)) {
        return;
    }

    try {
        const res = await fetch('/api/server/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ key: activeConfigKey, content })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            alert(`Файл ${activeConfigKey} успешно сохранен!`);
        } else {
            alert(data.error || 'Ошибка при сохранении конфигурации');
        }
    } catch (err) {
        alert('Ошибка при отправке: ' + err);
    }
}

// ==============================================================================
// Services & Status
// ==============================================================================
async function loadServiceStatus(silent = false) {
    try {
        const res = await fetch('/api/service/status');
        const data = await res.json();
        if (res.ok) {
            const s = data.services;
            const plutoEl = document.getElementById('svcPlutoBadge');
            const xl2tpdEl = document.getElementById('svcXl2tpdBadge');
            const iptablesEl = document.getElementById('svcIptablesBadge');
            const indicatorEl = document.getElementById('serviceIndicator');

            const isAllOk = s.ipsec.running && s.xl2tpd.running;

            if (indicatorEl) {
                indicatorEl.className = isAllOk ? 'service-status-badge status-active' : 'service-status-badge status-loading';
                indicatorEl.textContent = isAllOk ? 'IPsec / L2TP Онлайн' : 'Службы не готовы';
            }

            const plutoErrEl = document.getElementById('plutoErrorAlert');
            const xl2tpdErrEl = document.getElementById('xl2tpdErrorAlert');

            if (plutoEl) {
                plutoEl.className = s.ipsec.running ? 'service-status-badge status-active' : 'service-status-badge status-stopped';
                plutoEl.textContent = s.ipsec.running ? 'АКТИВЕН' : 'ОСТАНОВЛЕН';
            }
            if (plutoErrEl) {
                if (!s.ipsec.running && s.ipsec.error) {
                    plutoErrEl.textContent = 'Ошибка Pluto: ' + s.ipsec.error;
                    plutoErrEl.classList.remove('hidden');
                } else {
                    plutoErrEl.classList.add('hidden');
                }
            }

            if (xl2tpdEl) {
                xl2tpdEl.className = s.xl2tpd.running ? 'service-status-badge status-active' : 'service-status-badge status-stopped';
                xl2tpdEl.textContent = s.xl2tpd.running ? 'АКТИВЕН' : 'ОСТАНОВЛЕН';
            }
            if (xl2tpdErrEl) {
                if (!s.xl2tpd.running && s.xl2tpd.error) {
                    xl2tpdErrEl.textContent = 'Ошибка xl2tpd: ' + s.xl2tpd.error;
                    xl2tpdErrEl.classList.remove('hidden');
                } else {
                    xl2tpdErrEl.classList.add('hidden');
                }
            }

            if (iptablesEl) {
                iptablesEl.className = s.iptables.configured ? 'service-status-badge status-active' : 'service-status-badge status-stopped';
                iptablesEl.textContent = s.iptables.configured ? 'АКТИВЕН' : 'НЕ НАСТРОЕН';
            }
        }
    } catch (err) {
        if (!silent) console.error('Failed to load service status:', err);
    }
}

async function restartServicesBtn() {
    if (!confirm('Перезапустить службы IPsec (Libreswan) и xl2tpd? Туннели кратковременно переподключатся.')) {
        return;
    }

    const btns = [document.getElementById('serviceRestartBtn')].filter(Boolean);
    btns.forEach(b => {
        b.disabled = true;
        b.classList.add('btn-spinning');
    });

    try {
        const res = await fetch('/api/service/restart', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast('Службы IPsec и xl2tpd успешно перезапущены!', 'success');
            setTimeout(loadServiceStatus, 2000);
        } else {
            showToast(data.error || 'Ошибка при перезапуске служб', 'error');
        }
    } catch (err) {
        showToast('Ошибка связи с сервером: ' + err, 'error');
    } finally {
        btns.forEach(b => {
            b.disabled = false;
            b.classList.remove('btn-spinning');
        });
    }
}

// ==============================================================================
// Audit Log & Users RBAC (matching OpenVPN Web)
// ==============================================================================
async function loadAuditLogs() {
    const tbody = document.getElementById('auditTableBody');
    if (!tbody) return;

    try {
        const res = await fetch(`/api/audit?_=${new Date().getTime()}`);
        const data = await res.json();
        const logs = Array.isArray(data) ? data : (data.logs || []);
        renderAuditLogs(logs);
    } catch (err) {
        console.error('Ошибка загрузки журнала аудита:', err);
        tbody.innerHTML = `<tr><td colspan="4" class="text-center" style="padding: 24px; color: var(--danger);">Ошибка связи при загрузке журнала</td></tr>`;
    }
}

function renderAuditLogs(logs) {
    const tbody = document.getElementById('auditTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!Array.isArray(logs) || logs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="text-center" style="padding: 24px; color: var(--text-muted);">Журнал пуст</td></tr>`;
        return;
    }

    logs.forEach(log => {
        let actionBadge = `<span class="log-action info">${escapeHtml(log.action)}</span>`;
        if (log.action.includes('FAIL') || log.action.includes('ERR')) {
            actionBadge = `<span class="log-action fail">${escapeHtml(log.action)}</span>`;
        } else if (log.action.includes('CREATE') || log.action.includes('LOGIN') || log.action.includes('UPDATE')) {
            actionBadge = `<span class="log-action success">${escapeHtml(log.action)}</span>`;
        } else if (log.action.includes('DELETE') || log.action.includes('REVOKE') || log.action.includes('LOGOUT')) {
            actionBadge = `<span class="log-action revoke">${escapeHtml(log.action)}</span>`;
        }

        const ipInfo = log.ip_address ? ` <span style="color:var(--text-muted); font-size:11px; font-family:'JetBrains Mono', monospace;">(${escapeHtml(log.ip_address)})</span>` : '';
        const row = document.createElement('tr');
        row.innerHTML = `
            <td style="color:var(--text-muted); white-space:nowrap; padding:12px 18px; font-size:12px; font-family:'JetBrains Mono', monospace;">${escapeHtml(log.timestamp)}</td>
            <td style="font-weight:600; padding:12px 18px; font-size:13px;">${escapeHtml(log.username)}</td>
            <td style="padding:12px 18px;">${actionBadge}</td>
            <td style="color:var(--text-main); padding:12px 18px; font-size:13px;">${escapeHtml(log.details || '—')}${ipInfo}</td>
        `;
        tbody.appendChild(row);
    });
}

async function loadUsersTable() {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;

    try {
        const res = await fetch(`/api/users?_=${new Date().getTime()}`);
        const data = await res.json();
        const users = Array.isArray(data) ? data : (data.users || []);

        tbody.innerHTML = '';
        if (users.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center" style="padding: 24px; color: var(--text-muted);">Пользователи не найдены</td></tr>`;
            return;
        }

        users.forEach(u => {
            const row = document.createElement('tr');
            const sourceBadge = (u.auth_source === 'central' || u.auth_source === 'ca' || u.auth_source === 'ldap')
                ? `<span class="badge-source" title="Единая точка входа (SSO / Active Directory)">SSO / AD</span>`
                : `<span class="status-badge" style="background:rgba(255,255,255,0.06); color:var(--text-muted); font-size:10px;">Локальный</span>`;

            row.innerHTML = `
                <td style="font-weight:600; padding:12px 18px; font-family:'JetBrains Mono', monospace; font-size:13px;">
                    ${escapeHtml(u.username)}
                </td>
                <td style="padding:12px 18px; color:var(--text-main); font-size:13px;">
                    ${escapeHtml(u.display_name || u.username)}
                </td>
                <td style="padding:12px 18px;">
                    ${sourceBadge}
                </td>
                <td style="padding:12px 18px;">
                    <select class="role-select" onchange="changeUserRole(${u.id}, this.value, '${encodeURIComponent(u.username)}')">
                        <option value="operator" ${u.role === 'operator' ? 'selected' : ''}>Оператор</option>
                        <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Администратор</option>
                        <option value="viewer" ${u.role === 'viewer' ? 'selected' : ''}>Наблюдатель</option>
                    </select>
                </td>
                <td style="padding:12px 18px; color:var(--text-muted); font-size:12px;">
                    ${escapeHtml(u.last_login || '—')}
                </td>
                <td style="padding:12px 18px; color:var(--text-muted); font-size:12px;">
                    ${escapeHtml(u.created_at || '—')}
                </td>
            `;
            tbody.appendChild(row);
        });
    } catch (err) {
        console.error('Ошибка загрузки пользователей:', err);
        tbody.innerHTML = `<tr><td colspan="6" class="text-center" style="padding: 24px; color: var(--danger);">Ошибка связи при загрузке пользователей</td></tr>`;
    }
}

// Алиас для обратной совместимости
function loadUsers() {
    loadUsersTable();
}

async function changeUserRole(userId, newRole, username) {
    try {
        const res = await fetch(`/api/users/update-role/${userId}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ role: newRole })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            loadAuditLogs();
            loadUsersTable();
        } else {
            alert(data.error || 'Не удалось изменить роль');
            loadUsersTable();
        }
    } catch (err) {
        alert('Ошибка связи с сервером при смене роли: ' + err);
        loadUsersTable();
    }
}

// ==============================================================================
// Import Modal & Actions
// ==============================================================================
function openImportModal() {
    document.getElementById('importSecretsText').value = '';
    document.getElementById('importModal').classList.remove('hidden');
}

function closeImportModal() {
    document.getElementById('importModal').classList.add('hidden');
}

async function processImportSecrets() {
    const text = document.getElementById('importSecretsText').value;
    if (!text.trim()) {
        showToast('Вставьте содержимое chap-secrets для импорта', 'error');
        return;
    }

    try {
        const res = await fetch('/api/clients/import', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ content: text })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Импорт завершен! Добавлено: ${data.imported}, пропущено: ${data.skipped}`, 'success');
            closeImportModal();
            loadClients();
        } else {
            showToast(data.error || 'Ошибка при импорте', 'error');
        }
    } catch (e) {
        showToast('Ошибка связи с сервером при импорте', 'error');
    }
}

async function handleLogout() {
    try {
        await fetch('/logout', { method: 'POST' });
        window.location.href = '/login';
    } catch (e) {
        window.location.href = '/login';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return str.toString()
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
