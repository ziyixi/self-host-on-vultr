# v2ray quic
sysctl -w net.core.rmem_max=2500000
# bbr
echo net.core.default_qdisc=fq >> /etc/sysctl.conf
echo net.ipv4.tcp_congestion_control=bbr >> /etc/sysctl.conf
sysctl -p
# check
sysctl net.ipv4.tcp_available_congestion_control
lsmod | grep bbr