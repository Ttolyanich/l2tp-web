#!/bin/bash
set -e

echo "=================================================="
echo " Starting L2TP/IPsec Web Management Hub Container "
echo "=================================================="

DATA_DIR="${DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR"
mkdir -p /etc/ppp /etc/xl2tpd /etc/ipsec.d /var/run/xl2tpd /run/pluto /var/run/pluto /var/lib/ipsec/nss /var/log

# Clean up any leftover PID or control socket files
rm -f /run/pluto/pluto.pid /run/pluto/pluto.ctl /var/run/pluto/pluto.pid /var/run/pluto/pluto.ctl /var/run/xl2tpd/l2tp-control

# 1. Ensure /dev/ppp device exists for pppd
if [ ! -c /dev/ppp ]; then
    echo "Creating /dev/ppp character device..."
    mknod /dev/ppp c 108 0 || true
    chmod 600 /dev/ppp || true
fi

# 2. Kernel networking & sysctl parameters
echo "Applying kernel sysctl parameters..."
sysctl -w net.ipv4.ip_forward=1 || true
for f in /proc/sys/net/ipv4/conf/*/accept_redirects; do
    [ -f "$f" ] && echo 0 > "$f" || true
done
for f in /proc/sys/net/ipv4/conf/*/send_redirects; do
    [ -f "$f" ] && echo 0 > "$f" || true
done

# 3. Populate default configs if not already in DATA_DIR
if [ ! -f "$DATA_DIR/ipsec.conf" ]; then
    echo "Initializing default ipsec.conf..."
    cp /app/default_configs/ipsec.conf "$DATA_DIR/ipsec.conf"
fi
if [ ! -f "$DATA_DIR/l2tp.secrets" ]; then
    echo "Initializing default l2tp.secrets..."
    cp /app/default_configs/l2tp.secrets "$DATA_DIR/l2tp.secrets"
fi
if [ ! -f "$DATA_DIR/xl2tpd.conf" ]; then
    echo "Initializing default xl2tpd.conf..."
    cp /app/default_configs/xl2tpd.conf "$DATA_DIR/xl2tpd.conf"
fi
if [ ! -f "$DATA_DIR/options.xl2tpd" ]; then
    echo "Initializing default options.xl2tpd..."
    cp /app/default_configs/options.xl2tpd "$DATA_DIR/options.xl2tpd"
fi
if [ ! -f "$DATA_DIR/iptables_rules" ]; then
    echo "Initializing default iptables_rules..."
    cp /app/default_configs/iptables_rules "$DATA_DIR/iptables_rules"
fi

# Link configuration files to system paths
ln -sf "$DATA_DIR/ipsec.conf" /etc/ipsec.conf
ln -sf "$DATA_DIR/l2tp.secrets" /etc/ipsec.d/l2tp.secrets
ln -sf "$DATA_DIR/xl2tpd.conf" /etc/xl2tpd/xl2tpd.conf
ln -sf "$DATA_DIR/options.xl2tpd" /etc/ppp/options.xl2tpd

# Ensure ipsec.secrets includes /etc/ipsec.d/*.secrets
if [ ! -f /etc/ipsec.secrets ]; then
    echo "include /etc/ipsec.d/*.secrets" > /etc/ipsec.secrets
    chmod 600 /etc/ipsec.secrets
fi

# 4. Initialize Libreswan NSS security database if missing
if [ ! -d /var/lib/ipsec/nss ] || [ ! -f /var/lib/ipsec/nss/cert9.db ]; then
    echo "Initializing Libreswan NSS database..."
    mkdir -p /var/lib/ipsec/nss
    ipsec initnss || true
fi

ipsec --checknss || true
ipsec --checknflog || true

# 5. Apply Firewall & NAT rules
echo "Applying iptables rules..."
iptables -t filter -C FORWARD -i ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss 1300 2>/dev/null || \
iptables -t filter -A FORWARD -i ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss 1300 || true

iptables -t filter -C FORWARD -o ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss 1300 2>/dev/null || \
iptables -t filter -A FORWARD -o ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss 1300 || true

iptables -t nat -C POSTROUTING -s 10.18.8.0/22 -d 10.18.12.0/22 -j MASQUERADE 2>/dev/null || \
iptables -t nat -A POSTROUTING -s 10.18.8.0/22 -d 10.18.12.0/22 -j MASQUERADE || true

# 6. Initialize database and ensure chap-secrets exists
python3 -c "import app; app.init_db()" || true

echo "Starting Supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
