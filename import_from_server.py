#!/usr/bin/env python3
"""
CLI Utility to import existing L2TP/PPP clients into l2tp-web database.
Can import either from a local chap-secrets file or directly via SSH from the old server.
"""

import sys
import os
import argparse
import subprocess
import sqlite3
import re

def parse_chap_secrets_content(content):
    clients = []
    lines = content.splitlines()
    current_comment = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            # capture comment
            c = stripped.lstrip("#").strip()
            if not c.lower().startswith("secrets for") and not c.lower().startswith("client\tserver"):
                current_comment = c
            continue

        parts = stripped.split()
        if len(parts) >= 3:
            username = parts[0]
            # server usually L2TP or *
            secret = parts[2].strip("'").strip('"')
            assigned_ip = parts[3].strip() if len(parts) >= 4 else ""

            # Check if Synology
            lower_name = username.lower()
            is_synology = "synology" in lower_name or "nas" in lower_name or "bkp" in lower_name

            clients.append({
                "username": username,
                "password": secret,
                "assigned_ip": assigned_ip,
                "description": current_comment or "Импортировано с сервера",
                "device_type": "synology" if is_synology else "generic"
            })
            current_comment = ""

    return clients

def import_into_db(clients, db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Ensure table exists
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

    imported = 0
    skipped = 0

    for c in clients:
        try:
            cursor.execute(
                "INSERT INTO clients (username, password, assigned_ip, description, device_type, enabled) VALUES (?, ?, ?, ?, ?, 1)",
                (c["username"], c["password"], c["assigned_ip"], c["description"], c["device_type"])
            )
            imported += 1
        except sqlite3.IntegrityError:
            skipped += 1

    conn.commit()
    conn.close()
    return imported, skipped

def main():
    parser = argparse.ArgumentParser(description="Import chap-secrets into l2tp-web database")
    parser.add_argument("--file", "-f", help="Path to local chap-secrets file")
    parser.add_argument("--ssh", "-s", help="SSH host string (e.g. a.ivanov@10.18.9.190)")
    parser.add_argument("--db", "-d", default="data/node.db", help="Path to node.db SQLite file (default: data/node.db)")

    args = parser.parse_args()

    content = ""
    if args.ssh:
        print(f"Connecting to {args.ssh} via SSH...")
        try:
            res = subprocess.check_output(["ssh", args.ssh, "sudo cat /etc/ppp/chap-secrets"], text=True)
            content = res
        except Exception as e:
            print(f"SSH Error: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.file:
        if not os.path.exists(args.file):
            print(f"File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        with open(args.file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    else:
        print("Please specify either --file or --ssh", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    clients = parse_chap_secrets_content(content)
    print(f"Parsed {len(clients)} credentials from source.")

    imported, skipped = import_into_db(clients, args.db)
    print(f"Successfully imported: {imported} clients into {args.db}")
    if skipped:
        print(f"Skipped duplicates: {skipped}")

if __name__ == "__main__":
    main()
