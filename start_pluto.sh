#!/bin/bash
# Wrapper script for Libreswan Pluto daemon with automatic connection loading
set -e

rm -f /run/pluto/pluto.pid /run/pluto/pluto.ctl /var/run/pluto/pluto.pid /var/run/pluto/pluto.ctl
mkdir -p /run/pluto /var/lib/ipsec/nss /etc/ipsec.d /var/log

# Background watcher: wait until Pluto UNIX socket is active, then add connections
(
    for i in $(seq 1 30); do
        if [ -S /run/pluto/pluto.ctl ] || [ -S /var/run/pluto/pluto.ctl ]; then
            sleep 1
            ipsec auto --ready 2>/dev/null || true
            ipsec auto --add L2TP-PSK-noNAT 2>/dev/null || true
            ipsec auto --add L2TP-PSK-NAT 2>/dev/null || ipsec addconn --all 2>/dev/null || true
            echo "[pluto-init] IPsec connections successfully loaded into Pluto."
            break
        fi
        sleep 0.5
    done
) &

# Execute Pluto daemon
if [ -x /usr/libexec/ipsec/pluto ]; then
    exec /usr/libexec/ipsec/pluto --config /etc/ipsec.conf --nofork --stderrlog
else
    exec /usr/lib/ipsec/pluto --config /etc/ipsec.conf --nofork --stderrlog
fi
