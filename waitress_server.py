import os
import sys
import socket
from waitress import serve
from django.core.wsgi import get_wsgi_application

# Add the project directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Point to the NEW production settings file
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'textile_pos.production_settings')

def get_local_ip():
    """Detects the computer's actual LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # We don't actually connect, just try to reach a public IP
        # to see which local interface the OS would use.
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

if __name__ == '__main__':
    # Detect the IP
    lan_ip = get_local_ip()
    port = 8085

    print("----------------------------------------------------------------")
    print(" Textile POS System - Production Server")
    print("----------------------------------------------------------------")
    print(" > status: Loading application...")
    
    try:
        application = get_wsgi_application()
        print(" > status: Application loaded successfully.")
        print(f" > serving: http://localhost:{port}")
        print(f" > serving: http://{lan_ip}:{port}  <-- Use this on other devices")
        print("----------------------------------------------------------------")
        print(" Keep this window open to keep the system running.")
        print("----------------------------------------------------------------")
        
        # Threads=6 is a good balance for a local windows server
        serve(application, host='0.0.0.0', port=port, threads=6)
        
    except Exception as e:
        print(f"\n ERROR: Could not start server.\n {e}")
        input("Press Enter to exit...")