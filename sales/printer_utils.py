"""Printer discovery for the settings screens.

Only enumerates printers — the actual printing lives in restaurant/direct_print.py.
Windows-only by nature (win32print); every function degrades to a harmless empty
result elsewhere so the settings page still renders on a non-Windows machine.
"""


def get_available_printers():
    """(value, label) pairs of printers installed on the machine running the SERVER.

    Note this is the server's printer list, not the browser's — direct printing happens
    server-side, so a printer must be installed on the cashier PC (locally attached or
    added as a shared/network printer) to appear here.
    """
    try:
        import win32print
    except ImportError:
        return [('', 'الطباعة المباشرة غير متاحة على هذا النظام')]

    try:
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = [p[2] for p in win32print.EnumPrinters(flags)]
    except Exception:
        return [('', 'تعذّر قراءة قائمة الطابعات')]

    if not names:
        return [('', 'لا توجد طابعات مثبّتة')]
    return [('', '— بدون —')] + [(n, n) for n in names]


def print_html_to_backend(html_content, printer_name, base_url=None):
    """Legacy stub kept so sales/views.py's print_receipt_backend import doesn't break.

    HTML->printer rendering was never implemented here; the customer invoice is printed
    by the browser (templates/sales/invoice.html), and the kitchen ticket is printed
    directly as a rasterised image by restaurant/direct_print.py.
    """
    return False, "الطباعة المباشرة للفواتير غير مفعّلة — تتم الطباعة من المتصفح."
