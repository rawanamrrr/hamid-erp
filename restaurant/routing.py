from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/kds/(?P<branch_id>\d+)/$', consumers.KitchenConsumer.as_asgi()),
    re_path(r'^ws/waiter/(?P<branch_id>\d+)/$', consumers.WaiterConsumer.as_asgi()),
    re_path(r'^ws/delivery/(?P<branch_id>\d+)/$', consumers.DeliveryConsumer.as_asgi()),
    re_path(r'^ws/cashier/(?P<branch_id>\d+)/$', consumers.CashierConsumer.as_asgi()),
]
