"""Maps AttendanceDevice.adapter_type (a plain string) → adapter class.

Adding a new device brand/protocol = write the adapter class, add ONE line here. No
other file in the system (models, sync engine, views, financial/payroll logic) needs to
change — this registry is the single point of coupling between "a device type exists"
and "here is the code that talks to it".
"""
from .base import AttendanceDeviceAdapter
from .csv_import import CsvImportAdapter
from .zkteco_tcp import ZKTecoTcpAdapter

ADAPTER_REGISTRY: dict[str, type[AttendanceDeviceAdapter]] = {
    'csv_import': CsvImportAdapter,
    'zkteco_tcp': ZKTecoTcpAdapter,
    # 'hikvision_isapi': HikvisionAdapter,  # example future entry — HTTP/ISAPI adapter
}


def get_adapter_class(adapter_type: str) -> type[AttendanceDeviceAdapter]:
    try:
        return ADAPTER_REGISTRY[adapter_type]
    except KeyError:
        raise ValueError(
            f"لا يوجد محول (adapter) مسجّل للنوع '{adapter_type}'. "
            f"الأنواع المتاحة: {', '.join(ADAPTER_REGISTRY) or '(لا يوجد)'}")


def adapter_choices() -> list[tuple[str, str]]:
    """(value, label) pairs for the device-form's protocol dropdown."""
    labels = {
        'csv_import': 'استيراد ملف CSV/Excel',
        'zkteco_tcp': 'ZKTeco / أجهزة متوافقة (شبكة، منفذ 4370)',
    }
    return [(key, labels.get(key, key)) for key in ADAPTER_REGISTRY]
