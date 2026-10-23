#!/bin/bash
set -e

echo "=================================================="
echo " Starting L2TP/IPsec Web Management Hub Container "
echo "=================================================="

DATA_DIR="${DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR"
mkdir -p /etc/ppp /etc/xl2tpd /etc/ipsec.d /var/run/xl2tpd /run/pluto /var/run/pluto /var/lib/ipsec/nss /var/log /etc/supervisor/conf.d

# Ensure supervisor uses latest config
if [ -f /app/supervisord.conf ]; then
    cp /app/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
fi

# Clean up any leftover PID or control socket files
rm -f /run/pluto/pluto.pid /run/pluto/pluto.ctl /var/run/pluto/pluto.pid /var/run/pluto/pluto.ctl /var/run/xl2tpd/l2tp-control

# 1. Ensure /dev/ppp device exists for pppd
if [ ! -c /dev/ppp ]; then
    echo "Creating /dev/ppp character device..."
    mknod /dev/ppp c 108 0 || true
fi
chmod 666 /dev/ppp 2>/dev/null || true

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

# Sanitize existing ipsec.conf if corrupted or missing IKEv1 Libreswan settings
if [ -f "$DATA_DIR/ipsec.conf" ]; then
    nat_line=$(grep -n "conn L2TP-PSK-NAT" "$DATA_DIR/ipsec.conf" | head -n1 | cut -d: -f1 || echo 0)
    nonat_line=$(grep -n "conn L2TP-PSK-noNAT" "$DATA_DIR/ipsec.conf" | head -n1 | cut -d: -f1 || echo 0)
    if [ "$nat_line" -gt 0 ] && [ "$nonat_line" -gt 0 ] && [ "$nat_line" -lt "$nonat_line" ]; then
        echo "Fixing inverted conn order in $DATA_DIR/ipsec.conf..."
        cp "$DATA_DIR/ipsec.conf" "$DATA_DIR/ipsec.conf.bak"
        cp /app/default_configs/ipsec.conf "$DATA_DIR/ipsec.conf"
    fi
    if ! grep -q "ikev1-policy" "$DATA_DIR/ipsec.conf"; then
        echo "Adding ikev1-policy=accept to $DATA_DIR/ipsec.conf..."
        sed -i '/config setup/a \    ikev1-policy=accept' "$DATA_DIR/ipsec.conf"
    fi
    if ! grep -q "ikev2=" "$DATA_DIR/ipsec.conf"; then
        echo "Adding ikev2=no to $DATA_DIR/ipsec.conf..."
        sed -i '/conn L2TP-PSK-noNAT/a \    ikev2=no' "$DATA_DIR/ipsec.conf"
    fi
    if grep -q "left=%any" "$DATA_DIR/ipsec.conf"; then
        echo "Updating left=%any to left=%defaultroute in $DATA_DIR/ipsec.conf..."
        sed -i 's/left=%any/left=%defaultroute/g' "$DATA_DIR/ipsec.conf"
    fi
    if grep -q "leftnexthop=%defaultroute" "$DATA_DIR/ipsec.conf"; then
        echo "Removing obsolete leftnexthop/rightnexthop from $DATA_DIR/ipsec.conf..."
        sed -i '/leftnexthop=%defaultroute/d' "$DATA_DIR/ipsec.conf"
        sed -i '/rightnexthop=%defaultroute/d' "$DATA_DIR/ipsec.conf"
    fi
fi

# Link configuration files to system paths
ln -sf "$DATA_DIR/ipsec.conf" /etc/ipsec.conf
ln -sf "$DATA_DIR/l2tp.secrets" /etc/ipsec.d/l2tp.secrets
ln -sf "$DATA_DIR/xl2tpd.conf" /etc/xl2tpd/xl2tpd.conf
ln -sf "$DATA_DIR/options.xl2tpd" /etc/ppp/options.xl2tpd

# Ensure chap-secrets exists in DATA_DIR and is symlinked to /etc/ppp/chap-secrets
touch "$DATA_DIR/chap-secrets"
ln -sf "$DATA_DIR/chap-secrets" /etc/ppp/chap-secrets
chmod 600 "$DATA_DIR/chap-secrets" 2>/dev/null || true
chmod 600 /etc/ppp/chap-secrets 2>/dev/null || true

# Make helper scripts executable
chmod +x /app/entrypoint.sh /app/start_pluto.sh 2>/dev/null || true

# Ensure ipsec.secrets includes /etc/ipsec.d/*.secrets
if [ ! -f /etc/ipsec.secrets ]; then
    echo "include /etc/ipsec.d/*.secrets" > /etc/ipsec.secrets
fi
chmod 600 /etc/ipsec.secrets 2>/dev/null || true
chmod 600 /etc/ipsec.d/*.secrets 2>/dev/null || true
chmod 600 "$DATA_DIR/l2tp.secrets" 2>/dev/null || true

# 4. Initialize Libreswan NSS security database
mkdir -p /var/lib/ipsec/nss /etc/ipsec.d
if [ ! -f /var/lib/ipsec/nss/cert9.db ]; then
    echo "Initializing Libreswan NSS database..."
    ipsec initnss --nssdir /var/lib/ipsec/nss 2>/dev/null || ipsec initnss 2>/dev/null || true
fi
chmod 700 /var/lib/ipsec/nss 2>/dev/null || true
find /var/lib/ipsec/nss -type f -exec chmod 600 {} + 2>/dev/null || true

echo "Checking NSS and Libreswan config syntax..."
ipsec --checknss 2>/dev/null || true
ipsec addconn --config "$DATA_DIR/ipsec.conf" --checkconfig 2>/dev/null || true

# 5. Apply Firewall & NAT rules
echo "Applying iptables rules..."
iptables -t filter -C FORWARD -i ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss 1300 2>/dev/null || \
iptables -t filter -A FORWARD -i ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss 1300 || true

iptables -t filter -C FORWARD -o ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss 1300 2>/dev/null || \
iptables -t filter -A FORWARD -o ppp+ -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1301:65535 -j TCPMSS --set-mss 1300 || true

iptables -t nat -C POSTROUTING -s 10.18.8.0/22 -d 10.18.12.0/22 -j MASQUERADE 2>/dev/null || \
iptables -t nat -A POSTROUTING -s 10.18.8.0/22 -d 10.18.12.0/22 -j MASQUERADE || true

# 6. Initialize database and sync chap-secrets from SQLite
python3 -c "
try:
    import app
    app.init_db()
    conn = app.get_db()
    c = conn.cursor()
    c.execute('SELECT username, password, assigned_ip, description, enabled FROM clients')
    clients = [dict(r) for r in c.fetchall()]
    conn.close()
    app.manager.sync_chap_secrets(clients)
    print(f'[init] Synced {len(clients)} clients from DB to chap-secrets')
except Exception as e:
    print(f'[init] DB / chap-secrets sync error: {e}')
" || true

echo "Starting Supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
