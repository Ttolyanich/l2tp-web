import os
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime
import ipaddress

class L2TPManager:
    def __init__(self, data_dir="/app/data", ppp_dir="/etc/ppp", xl2tpd_dir="/etc/xl2tpd", ipsec_dir="/etc"):
        self.data_dir = data_dir
        self.ppp_dir = ppp_dir
        self.xl2tpd_dir = xl2tpd_dir
        self.ipsec_dir = ipsec_dir
        
        self.chap_secrets_path = os.getenv("CHAP_SECRETS_PATH", os.path.join(self.ppp_dir, "chap-secrets"))
        self.ipsec_conf_path = os.getenv("IPSEC_CONF_PATH", os.path.join(self.ipsec_dir, "ipsec.conf"))
        self.ipsec_secrets_path = os.getenv("IPSEC_SECRETS_PATH", os.path.join(self.ipsec_dir, "ipsec.secrets"))
        self.l2tp_secrets_path = os.getenv("L2TP_SECRETS_PATH", os.path.join(self.ipsec_dir, "ipsec.d", "l2tp.secrets"))
        self.xl2tpd_conf_path = os.getenv("XL2TPD_CONF_PATH", os.path.join(self.xl2tpd_dir, "xl2tpd.conf"))
        self.options_xl2tpd_path = os.getenv("OPTIONS_XL2TPD_PATH", os.path.join(self.ppp_dir, "options.xl2tpd"))
        self.iptables_rules_path = os.getenv("IPTABLES_RULES_PATH", os.path.join(self.data_dir, "iptables_rules"))

    def get_service_statuses(self):
        """Check status of Pluto (Libreswan/IPsec), xl2tpd, and Web API."""
        status = {
            "ipsec": {"running": False, "pid": None, "name": "Libreswan IPsec (Pluto)", "error": None},
            "xl2tpd": {"running": False, "pid": None, "name": "Layer 2 Tunnelling Protocol (xl2tpd)", "error": None},
            "iptables": {"configured": False, "name": "NAT / MSS Clamping Firewall"}
        }

        # Check Pluto (Libreswan)
        try:
            out = subprocess.check_output(["pgrep", "-f", "pluto"], text=True).strip()
            pids = out.split()
            if pids:
                status["ipsec"]["running"] = True
                status["ipsec"]["pid"] = int(pids[0])
        except Exception:
            # Fallback check socket or /run/pluto/pluto.ctl
            if os.path.exists("/run/pluto/pluto.ctl") or os.path.exists("/var/run/pluto/pluto.ctl"):
                status["ipsec"]["running"] = True

        if not status["ipsec"]["running"]:
            error_msg = ""
            for log_file in ["/var/log/pluto.log", "/var/log/pluto_err.log"]:
                if os.path.exists(log_file):
                    try:
                        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                            lines = [l.strip() for l in f.readlines() if l.strip()]
                            err_lines = [l for l in lines[-20:] if any(k in l.lower() for k in ["fatal", "error", "abort", "failed", "cannot", "address already in use", "syntax error"])]
                            if err_lines:
                                error_msg = err_lines[-1]
                            elif lines:
                                error_msg = lines[-1]
                    except Exception:
                        pass
                if error_msg:
                    break

            if not error_msg:
                try:
                    res = subprocess.run(["ipsec", "addconn", "--config", self.ipsec_conf_path, "--checkconfig"], capture_output=True, text=True, timeout=5)
                    if res.returncode != 0:
                        error_msg = res.stderr.strip() or res.stdout.strip()
                except Exception:
                    pass

            status["ipsec"]["error"] = error_msg or "Процесс Pluto остановлен (exit status 1)"

        # Check xl2tpd
        try:
            out = subprocess.check_output(["pgrep", "-f", "xl2tpd"], text=True).strip()
            pids = out.split()
            if pids:
                status["xl2tpd"]["running"] = True
                status["xl2tpd"]["pid"] = int(pids[0])
        except Exception:
            if os.path.exists("/var/run/xl2tpd/l2tp-control"):
                status["xl2tpd"]["running"] = True

        if not status["xl2tpd"]["running"]:
            error_msg = ""
            for log_file in ["/var/log/xl2tpd_err.log", "/var/log/xl2tpd.log"]:
                if os.path.exists(log_file):
                    try:
                        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                            lines = [l.strip() for l in f.readlines() if l.strip()]
                            if lines:
                                error_msg = lines[-1]
                    except Exception:
                        pass
                if error_msg:
                    break
            status["xl2tpd"]["error"] = error_msg or "xl2tpd остановлен"

        # Check iptables forward/mss rule presence
        try:
            ipt = subprocess.check_output(["iptables", "-t", "nat", "-L", "-n"], text=True, stderr=subprocess.DEVNULL)
            if "MASQUERADE" in ipt:
                status["iptables"]["configured"] = True
        except Exception:
            pass

        return status

    def get_active_sessions(self):
        """
        Parses active ppp* network interfaces, finds peer IP addresses,
        and returns a dict: {peer_ip: {ifname, rx_bytes, tx_bytes, ip}}
        """
        sessions = {}
        # 1. Parse ip -o -4 addr show
        try:
            out = subprocess.check_output(["ip", "-o", "-4", "addr", "show"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if "ppp" in line:
                    parts = line.split()
                    ifname = parts[1]
                    # Format: 43528: ppp19 inet 10.18.12.1 peer 10.18.15.22/32 scope global ppp19
                    peer_ip = None
                    if "peer" in parts:
                        idx = parts.index("peer")
                        if idx + 1 < len(parts):
                            peer_ip = parts[idx + 1].split("/")[0]
                    
                    if peer_ip:
                        rx, tx = self._get_interface_stats(ifname)
                        sessions[peer_ip] = {
                            "interface": ifname,
                            "peer_ip": peer_ip,
                            "rx_bytes": rx,
                            "tx_bytes": tx,
                            "connected": True
                        }
        except Exception:
            pass

        # 2. Correlate with IPsec SAs from libreswan if available
        try:
            ts = subprocess.check_output(["ipsec", "whack", "--trafficstatus"], text=True, stderr=subprocess.DEVNULL)
            # Format: 006 #158491: "L2TP-PSK-NAT"[68247] 85.193.99.84, type=ESP, add_time=..., inBytes=..., outBytes=...
            for line in ts.splitlines():
                m_ip = re.search(r'\]\s+([0-9\.]+),', line)
                m_in = re.search(r'inBytes=(\d+)', line)
                m_out = re.search(r'outBytes=(\d+)', line)
                if m_ip:
                    public_ip = m_ip.group(1)
                    # We can store active public IPs as context
        except Exception:
            pass

        return sessions

    def _get_interface_stats(self, ifname):
        """Read RX and TX bytes for an interface."""
        rx_path = f"/sys/class/net/{ifname}/statistics/rx_bytes"
        tx_path = f"/sys/class/net/{ifname}/statistics/tx_bytes"
        try:
            if os.path.exists(rx_path) and os.path.exists(tx_path):
                with open(rx_path, "r") as f:
                    rx = int(f.read().strip())
                with open(tx_path, "r") as f:
                    tx = int(f.read().strip())
                return rx, tx
        except Exception:
            pass
        return 0, 0

    def sync_chap_secrets(self, clients):
        """
        Writes the chap-secrets file from a list of client dicts:
        [{username, password, assigned_ip, enabled, description}]
        Sanitizes all inputs to prevent line injection or quoting errors.
        """
        lines = [
            "# Secrets for authentication using CHAP",
            "# Auto-generated by l2tp-web",
            "# client\tserver\tsecret\t\tIP addresses\n"
        ]

        for c in clients:
            if not c.get("enabled", True):
                continue
            raw_user = str(c.get("username", "")).strip()
            raw_secret = str(c.get("password", "")).strip()
            raw_ip = str(c.get("assigned_ip", "")).strip() or "*"
            raw_desc = str(c.get("description", "")).strip()

            # Sanitize username: remove any whitespace, tabs, newlines, quotes
            user = re.sub(r'[\s\'"]', '', raw_user)
            if not user or not raw_secret:
                continue

            # Sanitize password: escape backslashes and double quotes, strip newlines
            clean_secret = raw_secret.replace("\r", "").replace("\n", "").replace("\\", "\\\\").replace('"', '\\"')

            # Sanitize IP: ensure no newlines/whitespace
            clean_ip = re.sub(r'\s+', '', raw_ip)

            # Sanitize description: single line only
            if raw_desc:
                clean_desc = raw_desc.replace("\r", "").replace("\n", " ").strip()
                lines.append(f"# {clean_desc}")

            # Use '*' as server so pppd matches regardless of 'l2tpd' or 'L2TP' naming
            lines.append(f'{user}\t*\t"{clean_secret}"\t{clean_ip}')

        content = "\n".join(lines) + "\n"

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.chap_secrets_path), exist_ok=True)

        # Write temporary and atomic replace
        tmp_path = self.chap_secrets_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self.chap_secrets_path)
        return True

    def disconnect_peer(self, peer_ip):
        """Disconnects an active PPP session by peer IP."""
        active = self.get_active_sessions()
        if peer_ip in active:
            ifname = active[peer_ip]["interface"]
            try:
                # Find pid of pppd managing this exact interface using regex word boundaries
                out = subprocess.check_output(["pgrep", "-f", f"pppd.*\\b{ifname}\\b"], text=True).strip()
                for pid in out.split():
                    subprocess.run(["kill", "-HUP", pid], check=False)
                return True
            except Exception:
                try:
                    subprocess.run(["ip", "link", "set", ifname, "down"], check=False)
                    return True
                except Exception:
                    pass
        return False

    def get_next_available_ip(self, allocated_ips, pool_start="10.18.15.1", pool_end="10.18.15.254"):
        """Calculates the lowest unallocated IP in the defined pool."""
        try:
            start_int = int(ipaddress.IPv4Address(pool_start))
            end_int = int(ipaddress.IPv4Address(pool_end))
            allocated_set = {str(ip).strip() for ip in allocated_ips if ip}

            for ip_int in range(start_int, end_int + 1):
                cand = str(ipaddress.IPv4Address(ip_int))
                if cand not in allocated_set:
                    return cand
        except Exception:
            pass
        return ""

    def read_config_file(self, config_key):
        """Reads a server configuration file."""
        key_map = {
            "ipsec.conf": self.ipsec_conf_path,
            "l2tp.secrets": self.l2tp_secrets_path,
            "xl2tpd.conf": self.xl2tpd_conf_path,
            "options.xl2tpd": self.options_xl2tpd_path,
            "iptables_rules": self.iptables_rules_path
        }
        path = key_map.get(config_key)
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            return f"# Error reading file: {e}"

    def save_config_file(self, config_key, content):
        """Saves a server configuration file and creates a backup."""
        key_map = {
            "ipsec.conf": self.ipsec_conf_path,
            "l2tp.secrets": self.l2tp_secrets_path,
            "xl2tpd.conf": self.xl2tpd_conf_path,
            "options.xl2tpd": self.options_xl2tpd_path,
            "iptables_rules": self.iptables_rules_path
        }
        path = key_map.get(config_key)
        if not path:
            return False, "Unknown configuration file key"

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Create backup if original exists
            if os.path.exists(path):
                bak_path = path + f".bak.{int(time.time())}"
                shutil.copy2(path, bak_path)

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True, "Config successfully updated"
        except Exception as e:
            return False, str(e)

    def get_ipsec_psk(self):
        """Extracts the current IPsec Pre-Shared Key (PSK)."""
        paths_to_check = [
            self.l2tp_secrets_path,
            os.path.join(self.data_dir, "l2tp.secrets"),
            os.path.join(self.ipsec_dir, "ipsec.d", "l2tp.secrets")
        ]
        for p in paths_to_check:
            if p and os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if "PSK" in line:
                                parts = line.split("PSK", 1)
                                if len(parts) > 1:
                                    psk = parts[1].strip().strip('"').strip("'")
                                    if psk:
                                        return psk
                except Exception:
                    pass
        return os.getenv("L2TP_IPSEC_PSK", "L2tP(L2tB)Gbylf2019!")

    def apply_structured_config(self, new_cfg, app_config, config_path):
        """
        Validates, saves config parameters into app_config and config.json,
        and regenerates underlying Libreswan, xl2tpd, options.xl2tpd, and iptables configs.
        Prevents shell injection by strictly validating all IP parameters.
        """
        import json

        server_host = str(new_cfg.get("server_host", "")).strip()
        ipsec_psk = str(new_cfg.get("ipsec_psk", "")).strip()
        local_ip_raw = str(new_cfg.get("local_ip", "10.18.12.1")).strip()
        ip_pool_start_raw = str(new_cfg.get("ip_pool_start", "10.18.15.1")).strip()
        ip_pool_end_raw = str(new_cfg.get("ip_pool_end", "10.18.15.254")).strip()
        netmask_raw = str(new_cfg.get("netmask", "255.255.255.0")).strip()
        dns1_raw = str(new_cfg.get("dns1", "10.18.8.90")).strip()
        dns2_raw = str(new_cfg.get("dns2", "1.1.1.1")).strip()
        zabbix_subnet_raw = str(new_cfg.get("zabbix_subnet", "10.18.8.0/22")).strip()

        if not ipsec_psk:
            return False, "Общий ключ IPsec PSK не может быть пустым"

        # Strict IP validation to eliminate any command injection risk
        try:
            local_ip = str(ipaddress.IPv4Address(local_ip_raw))
            ip_pool_start = str(ipaddress.IPv4Address(ip_pool_start_raw))
            ip_pool_end = str(ipaddress.IPv4Address(ip_pool_end_raw))
            netmask = str(ipaddress.IPv4Address(netmask_raw))
            zabbix_subnet = str(ipaddress.IPv4Network(zabbix_subnet_raw, strict=False))
        except Exception as e:
            return False, f"Ошибка валидации IP/подсети: {e}"

        dns1 = ""
        if dns1_raw:
            try:
                dns1 = str(ipaddress.IPv4Address(dns1_raw))
            except Exception:
                return False, f"Некорректный IP адрес DNS 1: {dns1_raw}"

        dns2 = ""
        if dns2_raw:
            try:
                dns2 = str(ipaddress.IPv4Address(dns2_raw))
            except Exception:
                return False, f"Некорректный IP адрес DNS 2: {dns2_raw}"

        try:
            mtu = int(new_cfg.get("mtu", 1360))
            mtu = max(576, min(1500, mtu))
        except (ValueError, TypeError):
            mtu = 1360

        try:
            mru = int(new_cfg.get("mru", 1360))
            mru = max(576, min(1500, mru))
        except (ValueError, TypeError):
            mru = 1360

        try:
            tcp_mss = int(new_cfg.get("tcp_mss", 1300))
            tcp_mss = max(500, min(1460, tcp_mss))
        except (ValueError, TypeError):
            tcp_mss = 1300

        proxy_arp = bool(new_cfg.get("proxy_arp", True))

        # Update in-memory app_config
        app_config["server_host"] = server_host
        if server_host:
            app_config["public_ip"] = server_host
        app_config["ipsec_psk"] = ipsec_psk
        app_config["local_ip"] = local_ip
        app_config["ip_pool_start"] = ip_pool_start
        app_config["ip_pool_end"] = ip_pool_end
        app_config["netmask"] = netmask
        app_config["dns1"] = dns1
        app_config["dns2"] = dns2
        app_config["mtu"] = mtu
        app_config["mru"] = mru
        app_config["zabbix_subnet"] = zabbix_subnet
        app_config["tcp_mss"] = tcp_mss
        app_config["proxy_arp"] = proxy_arp

        # Persist to config.json
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(app_config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Warning: Could not save config.json: {e}")

        # 1. Update l2tp.secrets
        psk_content = f'%any %any: PSK "{ipsec_psk}"\n'
        self.save_config_file("l2tp.secrets", psk_content)

        # 2. Update xl2tpd.conf (CRITICAL: assign ip = no allows pppd to assign static IPs from chap-secrets)
        xl2tpd_content = (
            "[global]\n"
            "port = 1701\n"
            "access control = no\n\n"
            "[lns default]\n"
            f"ip range = {ip_pool_start}-{ip_pool_end}\n"
            "assign ip = no\n"
            f"local ip = {local_ip}\n"
            "refuse chap = yes\n"
            "refuse pap = yes\n"
            "require authentication = yes\n"
            "name = l2tpd\n"
            "pppoptfile = /etc/ppp/options.xl2tpd\n"
            "length bit = yes\n"
        )
        self.save_config_file("xl2tpd.conf", xl2tpd_content)

        # 3. Update options.xl2tpd (CRITICAL: keep LCP echo to clean dead peer sessions)
        proxyarp_line = "proxyarp\n" if proxy_arp else ""
        dns1_line = f"ms-dns {dns1}\n" if dns1 else ""
        dns2_line = f"ms-dns {dns2}\n" if dns2 else ""
        options_content = (
            "ipcp-accept-local\n"
            "ipcp-accept-remote\n"
            f"{dns1_line}"
            f"{dns2_line}"
            "noccp\n"
            "auth\n"
            "crtscts\n"
            "idle 1800\n"
            f"mtu {mtu}\n"
            f"mru {mru}\n"
            "nodefaultroute\n"
            "debug\n"
            "lcp-echo-interval 5\n"
            "lcp-echo-failure 10\n"
            "lcp-echo-adaptive\n"
            f"{proxyarp_line}"
            "connect-delay 5000\n"
            "refuse-pap\n"
            "refuse-chap\n"
            "refuse-mschap\n"
            "require-mschap-v2\n"
            "name l2tpd\n"
        )
        self.save_config_file("options.xl2tpd", options_content)

        # 4. Update iptables rules script (MASQUERADE Zabbix polling traffic into 10.18.12.0/22 VPN subnet)
        # 10.18.12.0/22 covers 10.18.12.X, 10.18.13.X, 10.18.14.X, 10.18.15.X
        tunnel_net = "10.18.12.0/22"
        iptables_content = (
            "#!/bin/bash\n"
            "echo 1 > /proc/sys/net/ipv4/ip_forward\n"
            f"iptables -t nat -C POSTROUTING -s {zabbix_subnet} -d {tunnel_net} -j MASQUERADE 2>/dev/null || "
            f"iptables -t nat -A POSTROUTING -s {zabbix_subnet} -d {tunnel_net} -j MASQUERADE\n"
            f"iptables -t filter -C FORWARD -i ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss {tcp_mss} 2>/dev/null || "
            f"iptables -t filter -A FORWARD -i ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss {tcp_mss}\n"
            f"iptables -t filter -C FORWARD -o ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss {tcp_mss} 2>/dev/null || "
            f"iptables -t filter -A FORWARD -o ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss {tcp_mss}\n"
        )
        self.save_config_file("iptables_rules", iptables_content)
        try:
            if os.path.exists(self.iptables_rules_path):
                subprocess.run(["bash", self.iptables_rules_path], check=False)
        except Exception:
            pass

        # 5. Restart services
        self.restart_services()

        return True, None

    def restart_services(self):
        """Triggers service restart for Libreswan and xl2tpd."""
        results = []
        # Try supervisorctl if running inside docker container
        try:
            res = subprocess.run(["supervisorctl", "restart", "pluto", "xl2tpd"], capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return True, "Службы IPsec и xl2tpd успешно перезапущены"
            results.append(f"supervisor: {res.stderr or res.stdout}")
        except Exception as e:
            results.append(f"supervisor error: {e}")

        # Try systemctl fallback if running on host
        try:
            subprocess.run(["systemctl", "restart", "ipsec", "xl2tpd"], check=False)
            return True, "Services restarted via systemctl"
        except Exception as e:
            results.append(f"systemctl error: {e}")

        return False, "; ".join(results)
