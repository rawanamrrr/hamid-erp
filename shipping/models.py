from django.db import models
from django.contrib.auth.models import User
from sales.models import Order

class ShippingCompany(models.Model):
    name = models.CharField(max_length=100, verbose_name="اسم شركة الشحن")
    phone = models.CharField(max_length=20, blank=True, verbose_name="رقم الهاتف")
    contact_person = models.CharField(max_length=100, blank=True, verbose_name="اسم المندوب/المسؤول")
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "شركة شحن"
        verbose_name_plural = "شركات الشحن"

class Shipment(models.Model):
    STATUS_CHOICES = [
        ('new', 'طلب جديد (تحت التجهيز)'),
        ('packaged', 'تم التغليف'),
        ('shipped', 'تم الشحن'),
        ('arrived', 'تم التوصيل'),
        ('returned', 'مرتجع'),
    ]

    DELIVERY_TYPE_CHOICES = [
        ('shipping', 'شحن لعنوان العميل'),
        ('pickup', 'استلام من الفرع'),
    ]

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='shipment', verbose_name="رقم الفاتورة")
    shipping_company = models.ForeignKey(ShippingCompany, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="شركة الشحن")
    
    delivery_type = models.CharField(max_length=20, choices=DELIVERY_TYPE_CHOICES, default='shipping', verbose_name="طريقة التسليم")
    shipping_address = models.TextField(verbose_name="عنوان الشحن")
    tracking_number = models.CharField(max_length=100, blank=True, verbose_name="رقم البوليصة / التتبع")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new', verbose_name="حالة الشحن")
    
    # Return Info
    return_reason = models.TextField(blank=True, verbose_name="سبب الارتجاع")
    # Whether the customer's balance was credited when this was marked مرتجع (staff choice —
    # not every return means money goes back, e.g. the customer never actually paid yet).
    # Guards against double-crediting if the status flips back and forth.
    balance_refunded = models.BooleanField(default=False, verbose_name="تم رد المبلغ لرصيد العميل")

    # Latest note from "أضف ملاحظة" — the full history of every note ever added lives in
    # ShipmentLog.comment, but that's never shown anywhere; this is the current one, surfaced
    # on the dashboard and printed on the invoice.
    comment = models.TextField(blank=True, verbose_name="ملاحظات")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ إنشاء الشحنة")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")

    def __str__(self):
        return f"شحنة لفاتورة #{self.order.id}"

class ShipmentLog(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='logs')
    status = models.CharField(max_length=20, verbose_name="الحالة")
    comment = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.status} - {self.created_at}"