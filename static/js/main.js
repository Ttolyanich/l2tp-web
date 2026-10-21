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
        loadConfigFile(activeConfigKey);
    } else if (tabName === 'audit') {
        loadAuditLogs();
    } else if (tabName === 'users') {
        loadUsers();
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
                    <strong style="font-size: 14px;">${escapeHtml(c.username)}</strong>
                    ${c.device_type === 'synology' ? '<span class="badge-role" style="background: rgba(16, 185, 129, 0.15); color: #10b981; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">Synology</span>' : ''}
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
                        <button type="button" class="btn-service-restart" style="width: auto; padding: 4px 8px; font-size: 12px;" title="Реквизиты и памятка DSM" onclick="showCredentials('${escapeHtml(c.username)}')">🔑</button>
                        ${c.online ? `<button type="button" class="btn-service-restart" style="width: auto; padding: 4px 8px; font-size: 12px; color: var(--warning);" title="Сбросить туннель" onclick="disconnectTunnel('${escapeHtml(c.assigned_ip)}')">⚡</button>` : ''}
                        <button type="button" class="btn-service-restart" style="width: auto; padding: 4px 8px; font-size: 12px;" title="Редактировать" onclick="editClient(${c.id})">✏️</button>
                        <button type="button" class="btn-service-restart" style="width: auto; padding: 4px 8px; font-size: 12px;" title="${c.enabled ? 'Отключить' : 'Включить'}" onclick="toggleClient(${c.id})">${c.enabled ? '⏸️' : '▶️'}</button>
                        <button type="button" class="btn-service-restart" style="width: auto; padding: 4px 8px; font-size: 12px; color: var(--danger);" title="Удалить" onclick="deleteClient(${c.id}, '${escapeHtml(c.username)}')">🗑️</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
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

function copyText(elemId) {
    const text = document.getElementById(elemId).textContent;
    navigator.clipboard.writeText(text);
    alert('Скопировано: ' + text);
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
    alert('Все реквизиты скопированы в буфер обмена!');
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
            const mobileDot = document.getElementById('mobileServiceStatusDot');

            const isAllOk = s.ipsec.running && s.xl2tpd.running;

            if (indicatorEl) {
                indicatorEl.className = isAllOk ? 'service-status-badge status-active' : 'service-status-badge status-loading';
                indicatorEl.textContent = isAllOk ? 'IPsec / L2TP Онлайн' : 'Службы не готовы';
            }
            if (mobileDot) {
                mobileDot.className = isAllOk ? 'status-indicator-dot online' : 'status-indicator-dot offline';
            }

            if (plutoEl) {
                plutoEl.className = s.ipsec.running ? 'service-status-badge status-active' : 'service-status-badge status-stopped';
                plutoEl.textContent = s.ipsec.running ? 'АКТИВЕН' : 'ОСТАНОВЛЕН';
            }
            if (xl2tpdEl) {
                xl2tpdEl.className = s.xl2tpd.running ? 'service-status-badge status-active' : 'service-status-badge status-stopped';
                xl2tpdEl.textContent = s.xl2tpd.running ? 'АКТИВЕН' : 'ОСТАНОВЛЕН';
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
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding: 24px; color: var(--text-muted);">Загрузка...</td></tr>`;

    try {
        const res = await fetch('/api/audit');
        const data = await res.json();
        if (res.ok && data.logs) {
            if (data.logs.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding: 24px; color: var(--text-muted);">Журнал пуст</td></tr>`;
                return;
            }
            tbody.innerHTML = data.logs.map(l => `
                <tr>
                    <td style="font-size: 12px; font-family: 'JetBrains Mono', monospace;">${escapeHtml(l.timestamp)}</td>
                    <td><strong>${escapeHtml(l.username)}</strong></td>
                    <td><span class="badge-role" style="font-size: 11px;">${escapeHtml(l.action)}</span></td>
                    <td>${escapeHtml(l.details || '—')}</td>
                    <td><code style="font-size: 12px;">${escapeHtml(l.ip_address || '—')}</code></td>
                </tr>
            `).join('');
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" style="color: var(--danger);">Ошибка загрузки: ${err}</td></tr>`;
    }
}

async function loadUsers() {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="7" class="text-center" style="padding: 24px; color: var(--text-muted);">Загрузка...</td></tr>`;

    try {
        const res = await fetch('/api/users');
        const data = await res.json();
        if (res.ok && data.users) {
            tbody.innerHTML = data.users.map(u => `
                <tr>
                    <td>${u.id}</td>
                    <td><strong>${escapeHtml(u.username)}</strong></td>
                    <td>${escapeHtml(u.display_name || '—')}</td>
                    <td><span class="badge-source">${escapeHtml(u.auth_source)}</span></td>
                    <td><span class="badge-role badge-role-${u.role}">${u.role}</span></td>
                    <td style="font-size: 12px;">${escapeHtml(u.last_login || '—')}</td>
                    <td class="text-right">
                        <select onchange="updateUserRole(${u.id}, this.value)" style="background: var(--bg-card); color: var(--text-main); border: 1px solid var(--border-color); border-radius: 4px; padding: 2px 6px; font-size: 12px;">
                            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>admin</option>
                            <option value="operator" ${u.role === 'operator' ? 'selected' : ''}>operator</option>
                            <option value="viewer" ${u.role === 'viewer' ? 'selected' : ''}>viewer</option>
                        </select>
                    </td>
                </tr>
            `).join('');
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" style="color: var(--danger);">Ошибка: ${err}</td></tr>`;
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
                        description: lastComment || 'Импортировано',
                        device_type: username.toLowerCase().includes('synology') || username.toLowerCase().includes('nas') ? 'synology' : 'generic'
                    })
                });
                if (res.ok) imported++;
            } catch (e) {
                console.error(e);
            }
        }
    }

    alert(`Импорт завершен! Добавлено: ${imported}`);
    closeImportModal();
    loadClients();
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
