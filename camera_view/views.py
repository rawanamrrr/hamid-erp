import cv2
import threading
import os
import time
import urllib.parse
from django.shortcuts import render, get_object_or_404
from django.http import StreamingHttpResponse
from django.contrib.auth.decorators import login_required
from .models import Camera

# Helper to construct the RTSP URL from the database object
def get_rtsp_url(camera, stream_id):
    # URL Encode credentials to handle special chars like '$', '@', ':'
    safe_username = urllib.parse.quote(camera.username)
    safe_password = urllib.parse.quote(camera.password)
    
    # Select path based on stream ID (1=HD, 2=SD)
    stream_suffix = camera.stream_path_hd if stream_id == 1 else camera.stream_path_sd
    
    # Construct URL: rtsp://user:pass@ip:port/stream_path
    url = f"rtsp://{safe_username}:{safe_password}@{camera.ip_address}:{camera.port}{stream_suffix}"
    return url

class VideoCamera(object):
    def __init__(self, camera_id, stream_id=2):
        # Fetch camera from DB
        try:
            self.camera_obj = Camera.objects.get(pk=camera_id)
        except Camera.DoesNotExist:
            print(f"❌ Error: Camera ID {camera_id} not found.")
            self.video = None
            return

        self.url = get_rtsp_url(self.camera_obj, stream_id)
        
        print(f"Attempting connection to {self.camera_obj.name} (IP: {self.camera_obj.ip_address}) Stream: {stream_id}")

        # FORCE TCP: Crucial for stable RTSP over WiFi/LAN
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        
        # Open Camera
        self.video = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        
        if not self.video.isOpened():
            print(f"❌ Error: Failed to open stream for {self.camera_obj.name}. Retrying UDP...")
            # Fallback: Try UDP
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp"
            self.video = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            
            if not self.video.isOpened():
                 print("❌ Error: UDP also failed. Check network/firewall.")
        else:
            print(f"✅ {self.camera_obj.name} Connected Successfully.")

        # Set buffer to 1 to reduce lag
        if self.video:
            self.video.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def __del__(self):
        if hasattr(self, 'video') and self.video:
            self.video.release()

    def get_frame(self):
        if not self.video or not self.video.isOpened():
            return None

        # Grab frame
        self.video.grab()
        success, image = self.video.retrieve()
        
        if success:
            # Encode to JPEG
            ret, jpeg = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            return jpeg.tobytes()
            
        return None

def gen(camera_object):
    # Retry mechanism inside the generator
    while True:
        frame = camera_object.get_frame()
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n\r\n')
        else:
            # If connection lost or starting up, wait slightly
            time.sleep(0.5) 
            continue

@login_required
def live_feed(request, camera_id, stream_id=2):
    try:
        camera_stream = VideoCamera(camera_id, int(stream_id))
        return StreamingHttpResponse(gen(camera_stream),
                                     content_type='multipart/x-mixed-replace; boundary=frame')
    except Exception as e:
        print(f"Camera Exception: {e}")
        return None

@login_required
def camera_dashboard(request):
    # Fetch all cameras from the database
    cameras = Camera.objects.all()
    return render(request, 'camera_view/live_stream.html', {
        'cameras': cameras,
    })