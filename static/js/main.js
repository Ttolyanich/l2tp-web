// ==============================================================================
// L2TP Web Hub — Frontend Application Logic
// ==============================================================================

let allClients = [];
let currentFilter = 'all';
let activeConfigKey = 'ipsec.conf';

document.addEventListener('DOMContentLoaded', () => {
    loadClients();
    loadServiceStatus();
    // Auto-refresh stats and clients every 8 seconds
    setInterval(() => {
        loadClients(true);
        loadServiceStatus(true);
    }, 8000);
});

// ==============================================================================
// Tab Navigation
// ==============================================================================
function switchTab(tabId, el) {
    document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));

    const targetTab = document.getElementById(`tab-${tabId}`);
    if (targetTab) {
        targetTab.classList.add('active');
    }
    if (el) {
        el.classList.add('active');
    }

    if (tabId === 'configs') {
        loadConfigFile(activeConfigKey);
    } else if (tabId === 'audit') {
        loadAuditLogs();
    } else if (tabId === 'users') {
        loadUsers();
    } else if (tabId === 'services') {
        loadServiceStatus();
    }
    toggleMobileSidebar(false);
}

function toggleMobileSidebar(show) {
    const sidebar = document.getElementById('sidebarNav');
    const backdrop = document.getElementById('mobileBackdrop');
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

    document.getElementById('statTotalClients').textContent = total;
    document.getElementById('statOnlineClients').textContent = online;
    const badge = document.getElementById('sidebarOnlineBadge');
    if (badge) badge.textContent = online;
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
    const searchVal = document.getElementById('clientSearchInput').value.toLowerCase().trim();

    let filtered = allClients.filter(c => {
        // Status filter
        if (currentFilter === 'online' && !c.online) return false;
        if (currentFilter === 'offline' && c.online) return false;
        if (currentFilter === 'disabled' && c.enabled) return false;

        // Search text
        if (searchVal) {
            const matchUser = c.username.toLowerCase().includes(searchVal);
            const matchDesc = (c.description || '').toLowerCase().includes(searchVal);
            const matchIp = (c.assigned_ip || '').toLowerCase().includes(searchVal);
            return matchUser || matchDesc || matchIp;
        }
        return true;
    });

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center" style="padding: 32px; color: var(--text-muted);">Клиенты не найдены</td></tr>`;
        return;
    }

    tbody.innerHTML = filtered.map(c => {
        const statusHtml = c.online 
            ? `<span class="status-indicator-dot online" title="Подключен"></span>` 
            : (!c.enabled ? `<span class="status-indicator-dot disabled" title="Отключен"></span>` : `<span class="status-indicator-dot offline" title="Оффлайн"></span>`);

        const rxFormatted = formatBytes(c.rx_bytes);
        const txFormatted = formatBytes(c.tx_bytes);

        return `
            <tr>
                <td>${statusHtml}</td>
                <td>
                    <strong>${escapeHtml(c.username)}</strong>
                    ${c.device_type === 'synology' ? '<span class="badge-tag">Synology</span>' : ''}
                </td>
                <td><span class="text-muted">${escapeHtml(c.description || '—')}</span></td>
                <td><code class="ip-code">${escapeHtml(c.assigned_ip)}</code></td>
                <td><span class="text-muted" style="font-size: 12px;">${c.online ? c.interface : '—'}</span></td>
                <td>
                    <span style="font-size: 12px; font-family: monospace;">
                        <span class="text-success">&darr; ${rxFormatted}</span> / <span class="text-info">&uarr; ${txFormatted}</span>
                    </span>
                </td>
                <td style="text-align: right;">
                    <div class="action-buttons-wrap">
                        <button class="btn-action-icon" title="Реквизиты и памятка DSM" onclick="showCredentials('${escapeHtml(c.username)}')">🔑</button>
                        ${c.online ? `<button class="btn-action-icon text-warning" title="Сбросить туннель" onclick="disconnectTunnel('${escapeHtml(c.assigned_ip)}')">⚡</button>` : ''}
                        <button class="btn-action-icon" title="Редактировать" onclick="editClient(${c.id})">✏️</button>
                        <button class="btn-action-icon" title="${c.enabled ? 'Отключить' : 'Включить'}" onclick="toggleClient(${c.id})">${c.enabled ? '⏸️' : '▶️'}</button>
                        <button class="btn-action-icon text-danger" title="Удалить" onclick="deleteClient(${c.id}, '${escapeHtml(c.username)}')">🗑️</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function setFilter(filterType, btn) {
    currentFilter = filterType;
    document.querySelectorAll('.filter-pill').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    renderClientsTable();
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

async function fetchNextIp() {
    try {
        const res = await fetch('/api/clients/next-ip');
        const data = await res.json();
        if (data.next_ip) {
            document.getElementById('clientAssignedIp').value = data.next_ip;
        }
    } catch (err) {
        console.error('Failed to get next IP:', err);
    }
}

function generatePasswordInput() {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%^&*';
    let pass = '';
    for (let i = 0; i < 16; i++) {
        pass += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    document.getElementById('clientPassword').value = pass;
}

function editClient(id) {
    const client = allClients.find(c => c.id === id);
    if (!client) return;

    document.getElementById('editClientId').value = client.id;
    document.getElementById('clientModalTitle').textContent = `Редактировать: ${client.username}`;
    document.getElementById('clientUsername').value = client.username;
    document.getElementById('clientUsername').disabled = true; // username cannot change
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
        alert('Ошибка сети или сервера: ' + err);
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
        alert('Ошибка удаления клиента: ' + err);
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
        alert('Ошибка получения реквизитов: ' + err);
    }
}

function closeCredentialsModal() {
    document.getElementById('credentialsModal').classList.add('hidden');
}

function copyText(elemId) {
    const text = document.getElementById(elemId).textContent;
    navigator.clipboard.writeText(text);
    alert('Скопировано в буфер обмена: ' + text);
}

function copyAllCredentials() {
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

    navigator.clipboard.writeText(text);
    alert('Все реквизиты и инструкция скопированы в буфер обмена!');
}

// ==============================================================================
// Config Editor
// ==============================================================================
async function loadConfigFile(key, btn) {
    activeConfigKey = key;
    document.querySelectorAll('.config-tab-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');

    const textarea = document.getElementById('configEditorTextarea');
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
        textarea.value = `# Ошибка связи с сервером: ${err}`;
    }
}

async function saveActiveConfig() {
    const content = document.getElementById('configEditorTextarea').value;
    if (!confirm(`Сохранить изменения в ${activeConfigKey}? Резервная копия будет создана автоматически.`)) {
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
            alert(`Файл ${activeConfigKey} успешно сохранен.`);
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

            if (plutoEl) plutoEl.className = s.ipsec.running ? 'badge-running' : 'badge-stopped';
            if (plutoEl) plutoEl.textContent = s.ipsec.running ? 'АКТИВЕН' : 'ОСТАНОВЛЕН';

            if (xl2tpdEl) xl2tpdEl.className = s.xl2tpd.running ? 'badge-running' : 'badge-stopped';
            if (xl2tpdEl) xl2tpdEl.textContent = s.xl2tpd.running ? 'АКТИВЕН' : 'ОСТАНОВЛЕН';

            if (iptablesEl) iptablesEl.className = s.iptables.configured ? 'badge-running' : 'badge-warning';
            if (iptablesEl) iptablesEl.textContent = s.iptables.configured ? 'АКТИВЕН' : 'НЕ НАСТРОЕН';
        }
    } catch (err) {
        if (!silent) console.error('Failed to load service status:', err);
    }
}

async function restartServicesBtn() {
    if (!confirm('Перезапустить службы IPsec (Libreswan) и xl2tpd? Активные туннели кратковременно переподключатся.')) {
        return;
    }

    try {
        const res = await fetch('/api/service/restart', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
            alert('Службы успешно перезапущены!');
            setTimeout(loadServiceStatus, 2000);
        } else {
            alert(data.error || 'Ошибка при перезапуске служб');
        }
    } catch (err) {
        alert('Ошибка связи с сервером: ' + err);
    }
}

// ==============================================================================
// Audit & Users
// ==============================================================================
async function loadAuditLogs() {
    const tbody = document.getElementById('auditTableBody');
    tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding: 24px;">Загрузка...</td></tr>`;

    try {
        const res = await fetch('/api/audit');
        const data = await res.json();
        if (res.ok && data.logs) {
            if (data.logs.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding: 24px;">Журнал пуст</td></tr>`;
                return;
            }
            tbody.innerHTML = data.logs.map(l => `
                <tr>
                    <td style="font-size: 12px; font-family: monospace;">${escapeHtml(l.timestamp)}</td>
                    <td><strong>${escapeHtml(l.username)}</strong></td>
                    <td><span class="badge-tag">${escapeHtml(l.action)}</span></td>
                    <td>${escapeHtml(l.details || '—')}</td>
                    <td><code class="ip-code">${escapeHtml(l.ip_address || '—')}</code></td>
                </tr>
            `).join('');
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-danger">Ошибка загрузки журнала: ${err}</td></tr>`;
    }
}

async function loadUsers() {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="7" class="text-center" style="padding: 24px;">Загрузка...</td></tr>`;

    try {
        const res = await fetch('/api/users');
        const data = await res.json();
        if (res.ok && data.users) {
            tbody.innerHTML = data.users.map(u => `
                <tr>
                    <td>${u.id}</td>
                    <td><strong>${escapeHtml(u.username)}</strong></td>
                    <td>${escapeHtml(u.display_name || '—')}</td>
                    <td><span class="badge-tag">${escapeHtml(u.auth_source)}</span></td>
                    <td><span class="user-role badge-${u.role}">${u.role}</span></td>
                    <td><span style="font-size: 12px;">${escapeHtml(u.last_login || '—')}</span></td>
                    <td style="text-align: right;">
                        <select onchange="updateUserRole(${u.id}, this.value)" class="form-control" style="width: auto; display: inline-block; padding: 4px 8px; font-size: 12px;">
                            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>admin</option>
                            <option value="operator" ${u.role === 'operator' ? 'selected' : ''}>operator</option>
                            <option value="viewer" ${u.role === 'viewer' ? 'selected' : ''}>viewer</option>
                        </select>
                    </td>
                </tr>
            `).join('');
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-danger">Ошибка: ${err}</td></tr>`;
    }
}

async function updateUserRole(userId, newRole) {
    try {
        const res = await fetch(`/api/users/update-role/${userId}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ role: newRole })
        });
        if (res.ok) {
            loadUsers();
        } else {
            alert('Не удалось изменить роль');
        }
    } catch (err) {
        alert('Ошибка: ' + err);
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
        alert('Вставьте содержимое chap-secrets');
        return;
    }

    const lines = text.split('\n');
    let imported = 0;
    let lastComment = '';

    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line) continue;
        if (line.startsWith('#')) {
            lastComment = line.replace(/^#\s*/, '');
            continue;
        }

        // Format: client server secret IP
        const parts = line.split(/\s+/);
        if (parts.length >= 3) {
            const username = parts[0];
            const secret = parts[2].replace(/^['"]|['"]$/g, '');
            const ip = parts.length >= 4 ? parts[3] : '';

            try {
                const res = await fetch('/api/clients', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        username,
                        password: secret,
                        assigned_ip: ip,
                        description: lastComment || 'Импортировано со старого сервера',
                        device_type: username.toLowerCase().includes('synology') || username.toLowerCase().includes('nas') ? 'synology' : 'generic'
                    })
                });
                if (res.ok) imported++;
            } catch (e) {
                console.error(e);
            }
        }
    }

    alert(`Успешно обработан импорт! Добавлено учетных записей: ${imported}`);
    closeImportModal();
    loadClients();
}

async function logout() {
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
