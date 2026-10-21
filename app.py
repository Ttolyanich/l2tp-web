import os
import sys
import json
import sqlite3
import time
import secrets
import string
import requests
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_file
from dotenv import load_dotenv

from l2tp_manager import L2TPManager

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "l2tp-web-super-secret-key-change-in-production")

# Global Configuration
config = {
    "node_name": os.getenv("NODE_NAME", "L2TP-Gateway-1"),
    "auth_mode": os.getenv("AUTH_MODE", "standalone").lower().strip(), # standalone | central | ad
    "central_auth_url": os.getenv("CENTRAL_AUTH_URL", "http://127.0.0.1:5001"),
    "node_api_token": os.getenv("NODE_API_TOKEN", "default-token"),
    "verify_ssl": os.getenv("VERIFY_SSL", "false").lower() in ("true", "1", "yes"),
    "standalone_admin_user": os.getenv("STANDALONE_ADMIN_USER", "admin"),
    "standalone_admin_password": os.getenv("STANDALONE_ADMIN_PASSWORD", "admin123"),
    "admin_users": [u.strip().lower() for u in os.getenv("ADMIN_USERS", "admin").split(",") if u.strip()],
    "admin_group": os.getenv("ADMIN_GROUP", "VPN_Admins"),
    "public_ip": os.getenv("PUBLIC_IP", ""),
    "ip_pool_start": os.getenv("IP_POOL_START", "10.18.15.1"),
    "ip_pool_end": os.getenv("IP_POOL_END", "10.18.15.254"),
    "local_tunnel_ip": os.getenv("LOCAL_TUNNEL_IP", "10.18.12.1"),
    "database_path": os.getenv("DATABASE_PATH", "/app/data/node.db"),
    # Direct AD/LDAP settings
    "ldap_server": os.getenv("LDAP_SERVER", ""),
    "ldap_port": int(os.getenv("LDAP_PORT", "389")),
    "ldap_use_ssl": os.getenv("LDAP_USE_SSL", "false").lower() in ("true", "1", "yes"),
    "ldap_base_dn": os.getenv("LDAP_BASE_DN", ""),
    "ldap_bind_user": os.getenv("LDAP_BIND_USER", ""),
    "ldap_bind_password": os.getenv("LDAP_BIND_PASSWORD", ""),
    "ldap_user_filter": os.getenv("LDAP_USER_FILTER", "(&(objectClass=user)(sAMAccountName={username}))"),
}

# Auto-detect public IP if not set
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

    # Users table (Web Admin RBAC)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            display_name TEXT,
            auth_source TEXT NOT NULL DEFAULT 'standalone',
            last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
# Helper Functions: RBAC, Audit, Rate Limiting
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
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO rate_limits (rate_key, attempt_time) VALUES (?, ?)", (ip, time.time()))
    conn.commit()
    conn.close()

def sync_user(username, central_role="operator", display_name=None, auth_source="standalone"):
    clean_user = username.strip()
    short_login = clean_user.split('@')[0].split('\\')[-1].strip().lower()
    is_explicit_admin = (clean_user.lower() in config["admin_users"] or short_login in config["admin_users"])

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, role FROM users WHERE LOWER(username) = ? OR LOWER(username) = ?", (clean_user.lower(), short_login))
    row = cursor.fetchone()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    disp_name = display_name or clean_user

    if not row:
        initial_role = "admin" if (is_explicit_admin or central_role == "admin") else central_role
        cursor.execute(
            "INSERT INTO users (username, role, display_name, auth_source, last_login) VALUES (?, ?, ?, ?, ?)",
            (clean_user, initial_role, disp_name, auth_source, now_str)
        )
        conn.commit()
        conn.close()
        return initial_role
    else:
        user_id, current_role = row[0], row[1]
        effective_role = "admin" if is_explicit_admin else current_role
        cursor.execute(
            "UPDATE users SET role = ?, display_name = ?, last_login = ? WHERE id = ?",
            (effective_role, disp_name, now_str, user_id)
        )
        conn.commit()
        conn.close()
        return effective_role

def generate_secure_password(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()_+-="
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Требуется авторизация"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Требуется авторизация"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "Доступ запрещен. Требуются права Администратора."}), 403
        return f(*args, **kwargs)
    return decorated_function

# ==============================================================================
# Active Directory / LDAP Authentication Helper
# ==============================================================================
def authenticate_ad(username, password):
    """Authenticates against Active Directory / LDAP directly using ldap3."""
    if not config["ldap_server"] or not config["ldap_base_dn"]:
        return False, "LDAP server not configured", "operator", username

    try:
        from ldap3 import Server, Connection, ALL, NTLM, SUBTREE
        server = Server(
            config["ldap_server"],
            port=config["ldap_port"],
            use_ssl=config["ldap_use_ssl"],
            get_info=ALL
        )
        
        # User login format (UserPrincipalName or sAMAccountName)
        login_user = username
        if "@" not in username and "\\" not in username and config.get("ldap_domain"):
            login_user = f"{username}@{config['ldap_domain']}"

        conn = Connection(server, user=login_user, password=password, auto_bind=True)
        if conn.bound:
            # Query user attributes and groups
            user_filter = config["ldap_user_filter"].format(username=username.split('@')[0].split('\\')[-1])
            conn.search(config["ldap_base_dn"], user_filter, SUBTREE, attributes=['displayName', 'memberOf', 'mail'])
            
            role = "operator"
            disp_name = username
            if conn.entries:
                entry = conn.entries[0]
                disp_name = str(entry.displayName) if hasattr(entry, 'displayName') and entry.displayName else username
                # Check admin group membership
                if hasattr(entry, 'memberOf') and entry.memberOf:
                    admin_grp = config["admin_group"].lower()
                    for grp in entry.memberOf:
                        if admin_grp in str(grp).lower():
                            role = "admin"
                            break

            conn.unbind()
            return True, "Success", role, disp_name
        else:
            return False, "Invalid credentials", "operator", username
    except Exception as e:
        return False, f"LDAP error: {e}", "operator", username

# ==============================================================================
# Web Routes: Views
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
        auth_mode=config["auth_mode"]
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if session.get("logged_in"):
            return redirect(url_for('index'))
        return render_template('login.html', node_name=config["node_name"], auth_mode=config["auth_mode"])

    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({"error": "Заполните все поля"}), 400

    if login_rate_limited(request.remote_addr):
        return jsonify({"error": "Слишком много неудачных попыток входа. Попробуйте через 5 минут."}), 429

    auth_mode = config["auth_mode"]

    # 1. Standalone Authentication
    if auth_mode == "standalone":
        if username == config["standalone_admin_user"] and password == config["standalone_admin_password"]:
            role = sync_user(username, "admin", "Локальный Администратор", "standalone")
            session["logged_in"] = True
            session["username"] = username
            session["role"] = role
            session["display_name"] = "Локальный Администратор"
            session["auth_mode"] = "standalone"
            log_action(username, "LOGIN", f"Успешный автономный вход ({role})")
            return jsonify({"success": True})
        else:
            register_failed_login(request.remote_addr)
            log_action(username, "LOGIN_FAILED", "Неверный логин или пароль (Standalone)")
            return jsonify({"error": "Неверный логин или пароль"}), 401

    # 2. Direct Active Directory / LDAP Authentication
    elif auth_mode == "ad":
        success, msg, ad_role, disp_name = authenticate_ad(username, password)
        if success:
            role = sync_user(username, ad_role, disp_name, "ad")
            session["logged_in"] = True
            session["username"] = username
            session["role"] = role
            session["display_name"] = disp_name
            session["auth_mode"] = "ad"
            log_action(username, "LOGIN", f"Успешный вход через Active Directory ({role})")
            return jsonify({"success": True})
        else:
            register_failed_login(request.remote_addr)
            log_action(username, "LOGIN_FAILED", f"Ошибка входа AD: {msg}")
            return jsonify({"error": "Неверный логин или пароль Active Directory"}), 401

    # 3. Centralized Auth (SSO with centralized-auth / CA)
    else:
        central_url = f"{config['central_auth_url'].rstrip('/')}/api/auth/verify"
        try:
            resp = requests.post(
                central_url,
                json={"username": username, "password": password},
                headers={"X-Node-Token": config["node_api_token"]},
                timeout=5,
                verify=config["verify_ssl"]
            )
            if resp.status_code == 200:
                res_data = resp.json()
                if res_data.get("success"):
                    c_role = res_data.get("role", "operator")
                    disp_name = res_data.get("display_name") or res_data.get("full_name") or username
                    role = sync_user(res_data.get("username", username), c_role, disp_name, "central")
                    session["logged_in"] = True
                    session["username"] = res_data.get("username", username)
                    session["role"] = role
                    session["display_name"] = disp_name
                    session["auth_mode"] = "central"
                    log_action(username, "LOGIN", f"Успешный вход через Central Auth ({role})")
                    return jsonify({"success": True})
            
            error_msg = "Неверный логин или пароль"
            if resp.status_code != 200:
                try:
                    error_msg = resp.json().get("error", error_msg)
                except Exception:
                    pass
            register_failed_login(request.remote_addr)
            log_action(username or "unknown", "LOGIN_FAILED", f"Неудачная попытка входа Central: {error_msg}")
            return jsonify({"error": error_msg}), resp.status_code or 401
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Центральный сервер авторизации недоступен: {e}"}), 503

@app.route('/logout', methods=['POST'])
def logout():
    user = session.get("username")
    log_action(user, "LOGOUT", "Пользователь вышел из системы")
    session.clear()
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

    # Get real-time active PPP sessions
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

    # Auto-generate password if empty
    if not password:
        password = generate_secure_password()

    # Auto-assign next free IP if not specified
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

        # Fetch all clients to sync chap-secrets
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

        # If IP changed, disconnect previous tunnel
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
        # Client disabled: disconnect immediately
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

    # Disconnect active tunnel if alive
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
            "step3": f"Имя профиля: VPN-Zabbix | Адрес сервера: {pub_ip} | Имя пользователя: {row['username']} | Пароль: {row['password']} | Общий ключ (Pre-shared key): {psk}",
            "step4": "В Дополнительных параметрах убедитесь, что снята галочка «Использовать шлюз удаленной сети» (чтобы локальный интернет офиса не шел через VPN)."
        }
    })

# ==============================================================================
# API Routes: Server Config Editor & Service Control
# ==============================================================================
@app.route('/api/server/config', methods=['GET'])
@admin_required
def get_server_config():
    key = request.args.get("key", "ipsec.conf")
    content = manager.read_config_file(key)
    return jsonify({"key": key, "content": content})

@app.route('/api/server/config', methods=['POST'])
@admin_required
def save_server_config():
    data = request.json or {}
    key = data.get("key")
    content = data.get("content")

    if not key or content is None:
        return jsonify({"error": "Параметры key и content обязательны"}), 400

    success, msg = manager.save_config_file(key, content)
    if success:
        log_action(session.get("username"), "CONFIG_UPDATE", f"Отредактирован конфигурационный файл {key}")
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": msg}), 500

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
# API Routes: Audit Logs & User RBAC
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
    port = int(os.getenv("BIND_PORT", os.getenv("PORT", 5000)))
    host = os.getenv("BIND_HOST", "0.0.0.0")
    print(f"Starting l2tp-web on {host}:{port} in {config['auth_mode'].upper()} mode...")
    app.run(host=host, port=port, debug=False)
