# This file has been stripped of functionality by user request.
# It provides empty stub functions to prevent import errors in views.py.

def get_available_printers():
    """
    Stub function. Returns a default message indicating printing is disabled.
    """
    return [('', 'Printing Disabled')]

def print_html_to_backend(html_content, printer_name, base_url=None):
    """
    Stub function. Always returns False as printing is disabled.
    """
    return False, "Printing functionality has been removed from this system."