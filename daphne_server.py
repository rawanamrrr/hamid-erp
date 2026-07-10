import os
import socket
import sys

# Add the project directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Point to the production settings file (same one waitress_server.py uses)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'textile_pos.production_settings')


def get_local_ip():
    """Detects the computer's actual LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


if __name__ == '__main__':
    lan_ip = get_local_ip()
    port = int(os.environ.get('PORT', '8085'))

    print("----------------------------------------------------------------")
    print(" Cafe ERP - ASGI Server (HTTP + Live KDS/Waiter/Delivery)")
    print("----------------------------------------------------------------")
    print(" > status: Loading application...")

    try:
        import django
        django.setup()
        print(" > status: Application loaded successfully.")
        print(f" > serving: http://localhost:{port}")
        print(f" > serving: http://{lan_ip}:{port}  <-- Use this on other devices")
        if not os.environ.get('DJANGO_REDIS_URL'):
            print(" > note: live updates use the in-memory channel layer (single-process).")
            print("        set DJANGO_REDIS_URL to scale across multiple workers.")
        print("----------------------------------------------------------------")
        print(" Keep this window open to keep the system running.")
        print("----------------------------------------------------------------")

        from daphne.cli import CommandLineInterface
        CommandLineInterface().run([
            '-b', '0.0.0.0', '-p', str(port), 'textile_pos.asgi:application',
        ])
    except Exception as e:
        print(f"\n ERROR: Could not start server.\n {e}")
        input("Press Enter to exit...")
