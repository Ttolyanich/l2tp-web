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
            "ipsec": {"running": False, "pid": None, "name": "Libreswan IPsec (Pluto)"},
            "xl2tpd": {"running": False, "pid": None, "name": "Layer 2 Tunnelling Protocol (xl2tpd)"},
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
        [{username, password, assigned_ip, enabled}]
        """
        lines = [
            "# Secrets for authentication using CHAP",
            "# Auto-generated by l2tp-web",
            "# client\tserver\tsecret\t\tIP addresses\n"
        ]

        for c in clients:
            if not c.get("enabled", True):
                continue
            user = c.get("username", "").strip()
            secret = c.get("password", "").strip()
            ip = c.get("assigned_ip", "").strip() or "*"
            desc = c.get("description", "").strip()
            
            if not user or not secret:
                continue

            if desc:
                lines.append(f"# {desc}")
            lines.append(f"{user}\tL2TP\t'{secret}'\t{ip}")

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
                # Find pid of pppd managing this interface
                out = subprocess.check_output(["pgrep", "-f", f"pppd.*{ifname}"], text=True).strip()
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
        if os.path.exists(self.l2tp_secrets_path):
            try:
                with open(self.l2tp_secrets_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if "PSK" in line:
                            parts = line.split("PSK", 1)
                            if len(parts) > 1:
                                psk = parts[1].strip().strip('"').strip("'")
                                return psk
            except Exception:
                pass
        return os.getenv("L2TP_IPSEC_PSK", "L2tP(L2tB)Gbylf2019!")

    def restart_services(self):
        """Triggers service restart for Libreswan and xl2tpd."""
        results = []
        # Try supervisorctl if running inside docker container
        try:
            res = subprocess.run(["supervisorctl", "restart", "all"], capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return True, "Services successfully restarted via supervisor"
            results.append(f"supervisor: {res.stderr}")
        except Exception as e:
            results.append(f"supervisor error: {e}")

        # Try systemctl fallback if running on host
        try:
            subprocess.run(["systemctl", "restart", "ipsec", "xl2tpd"], check=False)
            return True, "Services restarted via systemctl"
        except Exception as e:
            results.append(f"systemctl error: {e}")

        return False, "; ".join(results)
