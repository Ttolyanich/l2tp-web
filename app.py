import os
import sys
import json
import sqlite3
import time
import secrets
import string
import socket
import requests
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, abort
from dotenv import load_dotenv

from l2tp_manager import L2TPManager

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Config loading exactly as in openvpn-web
CONFIG_PATH = os.getenv("L2TP_WEB_CONFIG", "/app/data/config.json")
config = {
    "auth_mode": "central", # "central" (SSO / CA) or "standalone"
    "standalone_admin_user": "admin",
    "standalone_admin_password": "admin123",
    "secret_key": "default-secret-key-32-chars-long-please-change",
    "central_auth_url": "http://127.0.0.1:5001",
    "node_api_token": "default-token",
    "node_name": os.getenv("NODE_NAME", socket.gethostname()),
    "bind_host": "0.0.0.0",
    "bind_port": 5000,
    "verify_ssl": False,
    "public_ip": os.getenv("PUBLIC_IP", ""),
    "ip_pool_start": os.getenv("IP_POOL_START", "10.18.15.1"),
    "ip_pool_end": os.getenv("IP_POOL_END", "10.18.15.254"),
    "local_tunnel_ip": os.getenv("LOCAL_TUNNEL_IP", "10.18.12.1"),
    "database_path": os.getenv("DATABASE_PATH", "/app/data/node.db"),
    "admin_users": [],
    "admin_group": "VPN_Admins"
}

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            file_config = json.load(f)
            if "bind_port" in file_config:
                file_config["bind_port"] = int(file_config["bind_port"])
            if "verify_ssl" in file_config:
                if isinstance(file_config["verify_ssl"], str):
                    file_config["verify_ssl"] = file_config["verify_ssl"].lower() == "true"
            config.update(file_config)
    except Exception as e:
        print(f"Error loading config: {e}")

# Support environment variables (.env / Docker)
if os.getenv("AUTH_MODE"):
    config["auth_mode"] = os.getenv("AUTH_MODE").lower().strip()
if os.getenv("STANDALONE_ADMIN_USER"):
    config["standalone_admin_user"] = os.getenv("STANDALONE_ADMIN_USER").strip()
if os.getenv("STANDALONE_ADMIN_PASSWORD"):
    config["standalone_admin_password"] = os.getenv("STANDALONE_ADMIN_PASSWORD")
if os.getenv("SECRET_KEY"):
    config["secret_key"] = os.getenv("SECRET_KEY")
if os.getenv("CENTRAL_AUTH_URL"):
    config["central_auth_url"] = os.getenv("CENTRAL_AUTH_URL")
if os.getenv("NODE_API_TOKEN"):
    config["node_api_token"] = os.getenv("NODE_API_TOKEN")
if os.getenv("NODE_NAME"):
    config["node_name"] = os.getenv("NODE_NAME")
if os.getenv("BIND_HOST"):
    config["bind_host"] = os.getenv("BIND_HOST")
if os.getenv("BIND_PORT"):
    config["bind_port"] = int(os.getenv("BIND_PORT"))
if os.getenv("VERIFY_SSL"):
    config["verify_ssl"] = os.getenv("VERIFY_SSL").lower() in ("true", "1", "yes")

# Admin users list from .env
admin_users_raw = os.getenv("ADMIN_USERS") or os.getenv("L2TP_ADMIN_USERS") or ""
if admin_users_raw:
    config["admin_users"] = [u.strip().lower() for u in admin_users_raw.split(",") if u.strip()]
elif isinstance(config.get("admin_users"), str):
    config["admin_users"] = [u.strip().lower() for u in config["admin_users"].split(",") if u.strip()]
elif isinstance(config.get("admin_users"), list):
    config["admin_users"] = [str(u).strip().lower() for u in config["admin_users"] if str(u).strip()]

if os.getenv("ADMIN_GROUP"):
    config["admin_group"] = os.getenv("ADMIN_GROUP").strip()
elif os.getenv("L2TP_ADMIN_GROUP"):
    config["admin_group"] = os.getenv("L2TP_ADMIN_GROUP").strip()

# Auto-generate secret_key if default or empty
if config.get("secret_key") == "default-secret-key-32-chars-long-please-change" or not config.get("secret_key"):
    config["secret_key"] = secrets.token_hex(16)

app.secret_key = config["secret_key"]

# Auto-detect public IP if not explicitly set
if not config["public_ip"]:
    try:
        r = requests.get("https://api.ipify.org", timeout=3)
        if r.status_code == 200:
            config["public_ip"] = r.text.strip()
    except Exception:
        config["public_ip"] = "10.18.9.190"

manager = L2TPManager(data_dir=os.path.dirname(config["database_path"]))

# ==============================================================================
# Database Initialization & Management
# ==============================================================================
def get_db():
    os.makedirs(os.path.dirname(config["database_path"]), exist_ok=True)
    conn = sqlite3.connect(config["database_path"])
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Clients table (Synology / Router / Devices credentials)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            assigned_ip TEXT UNIQUE NOT NULL,
            description TEXT,
            device_type TEXT DEFAULT 'synology',
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_connected TIMESTAMP
        )
    ''')

    # Users table (Web Admin RBAC - matching openvpn-web)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            display_name TEXT,
            auth_source TEXT NOT NULL DEFAULT 'central',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Audit log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Rate limiting table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rate_key TEXT NOT NULL,
            attempt_time REAL NOT NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rate_key_time ON rate_limits(rate_key, attempt_time)')

    conn.commit()
    conn.close()

# Initialize DB at startup
init_db()

# ==============================================================================
# Helpers: RBAC, Audit, Rate Limiting (copied from openvpn-web)
# ==============================================================================
def log_action(username, action, details):
    try:
        ip = request.remote_addr if request else "system"
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audit_logs (username, action, details, ip_address) VALUES (?, ?, ?, ?)",
            (username or "anonymous", action, details, ip)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Audit log error: {e}")

LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300

def login_rate_limited(ip):
    now = time.time()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rate_limits WHERE attempt_time < ?", (now - LOGIN_WINDOW_SECONDS,))
    cursor.execute("SELECT COUNT(*) FROM rate_limits WHERE rate_key = ? AND attempt_time >= ?", (ip, now - LOGIN_WINDOW_SECONDS))
    count = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return count >= LOGIN_MAX_ATTEMPTS

def register_failed_login(ip):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO rate_limits (rate_key, attempt_time) VALUES (?, ?)", (ip, time.time()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Register failed login error: {e}")

def sync_node_user(username, central_role="operator", display_name=None, auth_source="central"):
    """
    Синхронизирует и кеширует пользователя в таблице users в node.db (по аналогии с openvpn-web).
    Определяет роль: 'admin' или 'operator'.
    Если пользователь указан в ADMIN_USERS (включая вариант без @domain),
    он гарантированно получает роль 'admin'.
    """
    if not username:
        return "operator"
    clean_user = username.strip()
    short_login = clean_user.split('@')[0].split('\\')[-1].strip().lower()

    admin_users_all = set()
    for u in config.get("admin_users", []):
        u_clean = u.strip().lower()
        if u_clean:
            admin_users_all.add(u_clean)
            if "@" in u_clean:
                admin_users_all.add(u_clean.split("@")[0])
            elif "\\" in u_clean:
                admin_users_all.add(u_clean.split("\\")[-1])

    is_explicit_admin = (clean_user.lower() in admin_users_all or short_login in admin_users_all)
    disp_name = display_name or clean_user

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, role, display_name FROM users WHERE LOWER(username) IN (?, ?)",
            (clean_user.lower(), short_login)
        )
        existing = cursor.fetchone()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not existing:
            initial_role = "admin" if (is_explicit_admin or central_role == "admin") else "operator"
            cursor.execute(
                "INSERT INTO users (username, role, display_name, auth_source, last_login) VALUES (?, ?, ?, ?, ?)",
                (clean_user, initial_role, disp_name, auth_source, now_str)
            )
            conn.commit()
            effective_role = initial_role
        else:
            effective_role = existing['role']
            if is_explicit_admin:
                effective_role = "admin"
            cursor.execute(
                "UPDATE users SET role = ?, display_name = ?, last_login = ? WHERE id = ?",
                (effective_role, disp_name or existing['display_name'], now_str, existing['id'])
            )
            conn.commit()
        conn.close()
        return effective_role
    except Exception as e:
        print(f"User sync error: {e}")
        return "operator"

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('login'))
        if session.get("role") != "admin":
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"error": "Forbidden: Требуются права Администратора"}), 403
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

def generate_secure_password(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()_+-="
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# ==============================================================================
# Web Views & Authentication (Copied exactly from openvpn-web)
# ==============================================================================
@app.route('/')
@login_required
def index():
    return render_template(
        'index.html',
        node_name=config["node_name"],
        username=session.get("username"),
        display_name=session.get("display_name"),
        role=session.get("role"),
        public_ip=config["public_ip"],
        auth_mode=config.get("auth_mode", "central")
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.json or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return jsonify({"error": "Заполните все поля"}), 400

        if login_rate_limited(request.remote_addr):
            return jsonify({"error": "Слишком много неудачных попыток входа. Попробуйте через 5 минут."}), 429

        auth_mode = config.get("auth_mode", "central")

        # 1. Автономный режим (standalone)
        if auth_mode == "standalone":
            admin_user = config.get("standalone_admin_user", "admin")
            admin_pass = config.get("standalone_admin_password", "admin123")
            if username == admin_user and password == admin_pass:
                effective_role = sync_node_user(username, "admin", "Локальный Администратор", "standalone")
                session["logged_in"] = True
                session["username"] = username
                session["role"] = effective_role
                session["display_name"] = "Локальный Администратор"
                session["auth_mode"] = "standalone"
                log_action(username, "LOGIN", f"Успешный автономный вход ({effective_role})")
                return jsonify({"success": True})
            else:
                register_failed_login(request.remote_addr)
                log_action(username, "LOGIN_FAILED", "Неверный логин или пароль (Standalone)")
                return jsonify({"error": "Неверный логин или пароль"}), 401

        # 2. Централизованный режим (centralized-auth / CA)
        central_url = f"{config['central_auth_url'].rstrip('/')}/api/auth/verify"
        try:
            resp = requests.post(
                central_url,
                json={"username": username, "password": password},
                headers={"X-Node-Token": config["node_api_token"]},
                timeout=5,
                verify=config.get("verify_ssl", False)
            )
            if resp.status_code == 200:
                res_data = resp.json()
                if res_data.get("success"):
                    c_role = res_data.get("role", "operator")
                    disp_name = res_data.get("display_name") or res_data.get("full_name") or username
                    effective_role = sync_node_user(res_data.get("username", username), c_role, disp_name, "central")
                    session["logged_in"] = True
                    session["username"] = res_data.get("username", username)
                    session["role"] = effective_role
                    session["display_name"] = disp_name
                    session["auth_mode"] = "central"
                    session["last_verified"] = time.time()
                    log_action(username, "LOGIN", f"Успешный вход через Central Auth ({effective_role})")
                    return jsonify({"success": True})

            error_msg = "Неверный логин или пароль"
            if resp.status_code != 200:
                try:
                    error_msg = resp.json().get("error", error_msg)
                except Exception:
                    pass
            register_failed_login(request.remote_addr)
            log_action(username or "unknown", "LOGIN_FAILED", f"Неудачная попытка входа: {error_msg}")
            return jsonify({"error": error_msg}), resp.status_code or 401
        except requests.exceptions.RequestException as e:
            print(f"Central auth connection error: {e}")
            return jsonify({"error": "Центральный сервер авторизации недоступен"}), 503

    return render_template('login.html', node_name=config["node_name"], auth_mode=config.get("auth_mode", "central"))

@app.route('/logout', methods=['POST'])
def logout():
    username = session.get("username", "unknown")
    log_action(username, "LOGOUT", "Выход из системы")
    session.pop("logged_in", None)
    session.pop("username", None)
    session.pop("role", None)
    session.pop("display_name", None)
    return jsonify({"success": True})

# ==============================================================================
# API Routes: Clients & Credentials
# ==============================================================================
@app.route('/api/clients', methods=['GET'])
@login_required
def get_clients():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, assigned_ip, description, device_type, enabled, created_at, updated_at, last_connected FROM clients ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    active_sessions = manager.get_active_sessions()

    clients_list = []
    for r in rows:
        ip = r["assigned_ip"]
        is_online = ip in active_sessions
        session_info = active_sessions.get(ip, {})

        clients_list.append({
            "id": r["id"],
            "username": r["username"],
            "password": r["password"],
            "assigned_ip": r["assigned_ip"],
            "description": r["description"] or "",
            "device_type": r["device_type"] or "synology",
            "enabled": bool(r["enabled"]),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "online": is_online,
            "interface": session_info.get("interface", "-"),
            "rx_bytes": session_info.get("rx_bytes", 0),
            "tx_bytes": session_info.get("tx_bytes", 0)
        })

    return jsonify({"clients": clients_list})

@app.route('/api/clients/next-ip', methods=['GET'])
@login_required
def get_next_ip():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT assigned_ip FROM clients")
    allocated = [r[0] for r in cursor.fetchall()]
    conn.close()

    next_ip = manager.get_next_available_ip(allocated, config["ip_pool_start"], config["ip_pool_end"])
    return jsonify({"next_ip": next_ip})

@app.route('/api/clients', methods=['POST'])
@login_required
def create_client():
    data = request.json or {}
    username = data.get("username", "").strip()
    description = data.get("description", "").strip()
    device_type = data.get("device_type", "synology").strip()
    assigned_ip = data.get("assigned_ip", "").strip()
    password = data.get("password", "").strip()

    if not username:
        return jsonify({"error": "Имя учетной записи (логин) обязательно"}), 400

    if not password:
        password = generate_secure_password()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT assigned_ip FROM clients")
    allocated = [r[0] for r in cursor.fetchall()]

    if not assigned_ip:
        assigned_ip = manager.get_next_available_ip(allocated, config["ip_pool_start"], config["ip_pool_end"])
        if not assigned_ip:
            conn.close()
            return jsonify({"error": "Пул IP-адресов исчерпан"}), 400
    else:
        if assigned_ip in allocated:
            conn.close()
            return jsonify({"error": f"IP-адрес {assigned_ip} уже занят другим клиентом"}), 400

    try:
        cursor.execute(
            "INSERT INTO clients (username, password, assigned_ip, description, device_type, enabled) VALUES (?, ?, ?, ?, ?, 1)",
            (username, password, assigned_ip, description, device_type)
        )
        conn.commit()

        cursor.execute("SELECT username, password, assigned_ip, description, enabled FROM clients")
        all_clients = [dict(r) for r in cursor.fetchall()]
        conn.close()

        manager.sync_chap_secrets(all_clients)
        log_action(session.get("username"), "CLIENT_CREATE", f"Создан клиент {username} ({assigned_ip})")

        return jsonify({
            "success": True,
            "client": {
                "username": username,
                "password": password,
                "assigned_ip": assigned_ip,
                "description": description,
                "device_type": device_type
            }
        })
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Клиент с таким логином или IP уже существует"}), 400
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/clients/update', methods=['POST'])
@login_required
def update_client():
    data = request.json or {}
    client_id = data.get("id")
    password = data.get("password", "").strip()
    description = data.get("description", "").strip()
    assigned_ip = data.get("assigned_ip", "").strip()

    if not client_id:
        return jsonify({"error": "ID клиента не указан"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT username, assigned_ip FROM clients WHERE id = ?", (client_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Клиент не найден"}), 404

    username = row["username"]
    old_ip = row["assigned_ip"]

    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if password:
            cursor.execute(
                "UPDATE clients SET password = ?, description = ?, assigned_ip = ?, updated_at = ? WHERE id = ?",
                (password, description, assigned_ip, now_str, client_id)
            )
        else:
            cursor.execute(
                "UPDATE clients SET description = ?, assigned_ip = ?, updated_at = ? WHERE id = ?",
                (description, assigned_ip, now_str, client_id)
            )
        conn.commit()

        if old_ip != assigned_ip:
            manager.disconnect_peer(old_ip)

        cursor.execute("SELECT username, password, assigned_ip, description, enabled FROM clients")
        all_clients = [dict(r) for r in cursor.fetchall()]
        conn.close()

        manager.sync_chap_secrets(all_clients)
        log_action(session.get("username"), "CLIENT_UPDATE", f"Обновлен клиент {username}")
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/clients/toggle', methods=['POST'])
@login_required
def toggle_client():
    data = request.json or {}
    client_id = data.get("id")
    if not client_id:
        return jsonify({"error": "ID клиента не указан"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT username, assigned_ip, enabled FROM clients WHERE id = ?", (client_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Клиент не найден"}), 404

    new_state = 0 if row["enabled"] else 1
    cursor.execute("UPDATE clients SET enabled = ? WHERE id = ?", (new_state, client_id))
    conn.commit()

    if not new_state:
        manager.disconnect_peer(row["assigned_ip"])

    cursor.execute("SELECT username, password, assigned_ip, description, enabled FROM clients")
    all_clients = [dict(r) for r in cursor.fetchall()]
    conn.close()

    manager.sync_chap_secrets(all_clients)
    state_str = "включен" if new_state else "отключен"
    log_action(session.get("username"), "CLIENT_TOGGLE", f"Клиент {row['username']} {state_str}")
    return jsonify({"success": True, "enabled": bool(new_state)})

@app.route('/api/clients/delete', methods=['POST'])
@admin_required
def delete_client():
    data = request.json or {}
    client_id = data.get("id")
    if not client_id:
        return jsonify({"error": "ID клиента не указан"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT username, assigned_ip FROM clients WHERE id = ?", (client_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Клиент не найден"}), 404

    username = row["username"]
    peer_ip = row["assigned_ip"]

    cursor.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.commit()

    manager.disconnect_peer(peer_ip)

    cursor.execute("SELECT username, password, assigned_ip, description, enabled FROM clients")
    all_clients = [dict(r) for r in cursor.fetchall()]
    conn.close()

    manager.sync_chap_secrets(all_clients)
    log_action(session.get("username"), "CLIENT_DELETE", f"Удален клиент {username}")
    return jsonify({"success": True})

@app.route('/api/clients/disconnect', methods=['POST'])
@login_required
def disconnect_client():
    data = request.json or {}
    assigned_ip = data.get("assigned_ip", "").strip()
    if not assigned_ip:
        return jsonify({"error": "IP-адрес не указан"}), 400

    success = manager.disconnect_peer(assigned_ip)
    log_action(session.get("username"), "CLIENT_DISCONNECT", f"Принудительный сброс туннеля {assigned_ip}")
    return jsonify({"success": success})

@app.route('/api/clients/<string:username>/credentials', methods=['GET'])
@login_required
def get_credentials(username):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT username, password, assigned_ip, description, device_type FROM clients WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Клиент не найден"}), 404

    psk = manager.get_ipsec_psk()
    pub_ip = config["public_ip"]

    return jsonify({
        "server": pub_ip,
        "psk": psk,
        "username": row["username"],
        "password": row["password"],
        "assigned_ip": row["assigned_ip"],
        "description": row["description"],
        "device_type": row["device_type"],
        "auth_type": "MS-CHAP v2",
        "synology_guide": {
            "step1": "Откройте Панель управления Synology DSM -> Сеть -> Сетевой интерфейс.",
            "step2": "Нажмите Создать -> Создать профиль VPN -> Выберите L2TP/IPSec.",
            "step3": f"Имя профиля: VPN-Zabbix | Адрес сервера: {pub_ip} | Имя пользователя: {row['username']} | Пароль: {row['password']} | Общий ключ: {psk}",
            "step4": "В Дополнительных параметрах убедитесь, что снята галочка «Использовать шлюз удаленной сети» (чтобы локальный интернет офиса не шел через VPN)."
        }
    })

# ==============================================================================
# API Routes: Server Config Editor & Service Control
# ==============================================================================
@app.route('/api/server/config', methods=['GET'])
@admin_required
def get_server_config():
    # If raw file requested
    key = request.args.get("key")
    if key:
        content = manager.read_config_file(key)
        return jsonify({"key": key, "content": content})

    # Structured config parameters matching OpenVPN Web
    try:
        cfg = {
            "server_host": config.get("server_host") or config.get("public_ip") or "",
            "ipsec_psk": manager.get_ipsec_psk(),
            "local_ip": config.get("local_ip", "10.18.12.1"),
            "ip_pool_start": config.get("ip_pool_start", "10.18.15.1"),
            "ip_pool_end": config.get("ip_pool_end", "10.18.15.254"),
            "netmask": config.get("netmask", "255.255.255.0"),
            "dns1": config.get("dns1", "10.18.8.90"),
            "dns2": config.get("dns2", "1.1.1.1"),
            "mtu": config.get("mtu", 1360),
            "mru": config.get("mru", 1360),
            "zabbix_subnet": config.get("zabbix_subnet", "10.18.8.0/22"),
            "tcp_mss": config.get("tcp_mss", 1300),
            "proxy_arp": config.get("proxy_arp", True)
        }
        return jsonify({"success": True, "config": cfg})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server/config', methods=['POST'])
@admin_required
def save_server_config():
    data = request.json or {}

    # If raw file mode
    if "key" in data and "content" in data:
        key = data["key"]
        content = data["content"]
        if not key or content is None:
            return jsonify({"error": "Параметры key и content обязательны"}), 400
        success, msg = manager.save_config_file(key, content)
        if success:
            log_action(session.get("username"), "CONFIG_UPDATE", f"Отредактирован файл {key}")
            return jsonify({"success": True, "message": msg})
        return jsonify({"error": msg}), 500

    # Structured parameters mode
    ok, err = manager.apply_structured_config(data, config, CONFIG_PATH)
    if not ok:
        return jsonify({"error": err or "Ошибка сохранения параметров"}), 400

    log_action(session.get("username"), "UPDATE_SERVER_CONFIG", "Обновлена конфигурация сервера L2TP/IPsec")
    return jsonify({"success": True, "message": "Конфигурация сервера успешно сохранена и службы обновлены"})

@app.route('/api/service/status', methods=['GET'])
@login_required
def service_status():
    status = manager.get_service_statuses()
    active_sessions = manager.get_active_sessions()
    return jsonify({
        "services": status,
        "active_tunnels_count": len(active_sessions),
        "node_name": config["node_name"],
        "public_ip": config["public_ip"]
    })

@app.route('/api/service/restart', methods=['POST'])
@admin_required
def restart_services():
    success, msg = manager.restart_services()
    log_action(session.get("username"), "SERVICE_RESTART", f"Перезапуск служб: {msg}")
    return jsonify({"success": success, "message": msg})

# ==============================================================================
# API Routes: Audit Logs & User RBAC (Copied exactly from openvpn-web)
# ==============================================================================
@app.route('/api/audit', methods=['GET'])
@login_required
def get_audit():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, action, details, ip_address, timestamp FROM audit_logs ORDER BY id DESC LIMIT 150")
    logs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({"logs": logs})

@app.route('/api/users', methods=['GET'])
@admin_required
def get_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role, display_name, auth_source, last_login, created_at FROM users ORDER BY id ASC")
    users = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({"users": users})

@app.route('/api/users/update-role/<int:user_id>', methods=['POST'])
@admin_required
def update_user_role(user_id):
    data = request.json or {}
    new_role = data.get("role", "operator").strip().lower()
    if new_role not in ("admin", "operator", "viewer"):
        return jsonify({"error": "Недопустимая роль"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    conn.commit()
    conn.close()

    log_action(session.get("username"), "USER_ROLE_UPDATE", f"Пользователю ID {user_id} назначена роль {new_role}")
    return jsonify({"success": True})

# ==============================================================================
# Main Entry Point
# ==============================================================================
if __name__ == '__main__':
    port = config.get("bind_port", 5000)
    host = config.get("bind_host", "0.0.0.0")
    print(f"Starting l2tp-web on {host}:{port} in {config['auth_mode'].upper()} mode...")
    app.run(host=host, port=port, debug=False)
