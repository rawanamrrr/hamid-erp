"""Which address other devices on the shop's WiFi should use to reach this machine.

The app is a POS server as well as a POS: waiters' phones and tablets open it over the
local network. Working out the URL to type into them is the first thing anyone has to do
after installing, and until now the only way to find it was to open a command prompt and
read `ipconfig` — which is not something a cafe owner is going to do. So the app tells
them.
"""
import os
import socket


def _port():
    return os.environ.get('POS_PORT', '8085')


def primary_lan_ip():
    """The IPv4 address other machines on the same network can actually reach.

    Found by opening a UDP socket toward a public address and reading back which local
    interface the OS chose. No packet is ever sent (UDP connect only sets the peer) and no
    internet connection is required — it is purely a way to ask the routing table "if I
    were to send something out, which of my addresses would I send it from". That matters
    on a till with several adapters (WiFi, ethernet, a VPN, Hyper-V), where simply taking
    the first address the machine reports can hand back one nothing else can reach.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        sock.connect(('8.8.8.8', 80))
        return sock.getsockname()[0]
    except OSError:
        return ''
    finally:
        sock.close()


def all_lan_ips():
    """Every IPv4 address this machine answers on, primary one first.

    A till connected by both WiFi and a cable has two, and only one of them is on the same
    network as the waiter's phone — so listing the alternatives is more useful than
    guessing wrong and leaving them stuck.
    """
    primary = primary_lan_ip()
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            # 127.x is this machine only. 169.254.x is what Windows assigns to an adapter
            # that failed to get a DHCP lease — a till typically has several (Bluetooth, a
            # disconnected ethernet port, virtual adapters) and none of them can be
            # reached from a phone, so offering them as alternatives sends the user to
            # try addresses that cannot possibly work.
            if address.startswith(('127.', '169.254.')) or address in found:
                continue
            found.append(address)
    except OSError:
        pass

    if primary and primary not in found:
        found.insert(0, primary)
    elif primary:
        found.remove(primary)
        found.insert(0, primary)
    return found


def network_access(request=None):
    """Context for the settings page: the URL to type, and the alternatives."""
    port = _port()
    addresses = all_lan_ips()
    return {
        'lan_ip': addresses[0] if addresses else '',
        'lan_url': 'http://%s:%s' % (addresses[0], port) if addresses else '',
        'lan_alternatives': [
            {'ip': ip, 'url': 'http://%s:%s' % (ip, port)} for ip in addresses[1:]
        ],
        'lan_port': port,
    }
