from django.db import models

class Camera(models.Model):
    name = models.CharField(max_length=100, help_text="Example: Living Room Camera")
    
    # Connection Details
    ip_address = models.GenericIPAddressField(protocol='IPv4', help_text="Example: 192.168.1.200")
    port = models.CharField(max_length=10, default='554', help_text="Default RTSP port is 554")
    username = models.CharField(max_length=100, help_text="Camera Username")
    password = models.CharField(max_length=100, help_text="Camera Password")
    
    # Stream Paths (flexible for different camera brands)
    stream_path_hd = models.CharField(
        max_length=100, 
        default='/stream1', 
        help_text="Path for High Quality (e.g., /stream1 or /main)"
    )
    stream_path_sd = models.CharField(
        max_length=100, 
        default='/stream2', 
        help_text="Path for Low Quality (e.g., /stream2 or /sub)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.ip_address})"