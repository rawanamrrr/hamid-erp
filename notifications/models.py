from django.db import models
from django.contrib.auth.models import User

class Notification(models.Model):
    recipient = models.ForeignKey(User, related_name='notifications', on_delete=models.CASCADE, verbose_name="المستلم")
    title = models.CharField(max_length=255, verbose_name="العنوان")
    message = models.TextField(verbose_name="الرسالة")
    link = models.CharField(max_length=255, blank=True, null=True, verbose_name="رابط")
    is_read = models.BooleanField(default=False, verbose_name="تمت القراءة")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="وقت الإشعار")
    created_by = models.ForeignKey(User, related_name='created_notifications', on_delete=models.SET_NULL, null=True, verbose_name="المرسل")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "إشعار"
        verbose_name_plural = "الإشعارات"

    def __str__(self):
        return f"{self.title} - {self.recipient.username}"