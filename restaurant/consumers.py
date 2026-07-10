import json

from channels.generic.websocket import AsyncJsonWebsocketConsumer


def branch_group(prefix, branch_id):
    return f"{prefix}_branch_{branch_id}"


class _BranchGroupConsumer(AsyncJsonWebsocketConsumer):
    """Base: joins a per-branch group named '<group_prefix>_branch_<id>'.

    Clients only ever receive events for their own branch — a busy table map or a
    KDS ticket from another outlet never crosses over.
    """
    group_prefix = 'base'

    async def connect(self):
        branch_id = self.scope['url_route']['kwargs']['branch_id']
        self.group_name = branch_group(self.group_prefix, branch_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def broadcast_event(self, event):
        """Handler for group_send({'type': 'broadcast_event', 'payload': {...}})."""
        await self.send_json(event['payload'])


class KitchenConsumer(_BranchGroupConsumer):
    group_prefix = 'kds'


class WaiterConsumer(_BranchGroupConsumer):
    group_prefix = 'waiter'


class DeliveryConsumer(_BranchGroupConsumer):
    group_prefix = 'delivery'


class CashierConsumer(_BranchGroupConsumer):
    group_prefix = 'cashier'


def push_event(prefix, branch_id, payload):
    """Fire-and-forget broadcast to a branch's group from sync code (views).

    Usage: push_event('kds', branch_id, {'event': 'new_item', 'order_id': 5, ...})
    Safe no-op if no channel layer is configured (e.g. during management commands).
    """
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        branch_group(prefix, branch_id),
        {'type': 'broadcast_event', 'payload': payload},
    )
