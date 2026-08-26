"""Turn a rendered HTML page into a real PDF using the browser Windows already has.

Why not WeasyPrint (which this replaced): WeasyPrint is a thin Python layer over GTK/Pango,
a native text-shaping stack from the Linux world. On a Windows machine without GTK
installed it cannot even be imported —

    OSError: cannot load library 'libgobject-2.0-0'

— so every "download PDF" silently degraded to an HTML preview. Bundling it into the
PyInstaller build does not help either: WeasyPrint loads those libraries at runtime through
ctypes.util.find_library rather than a normal import, so the packaging step cannot see them
and they never get collected.

Microsoft Edge is present on every supported Windows install and prints HTML to PDF from
the command line. That means no extra dependency, nothing for the customer to install, and
— because it is the same engine that renders the page on screen — a PDF that matches the
on-screen invoice exactly, with Arabic shaped by Windows' own text stack.

Callers keep their existing HTML fallback: this returns None rather than raising when no
browser is available, so a PDF download degrades to a print preview instead of erroring.
"""
import logging
import os
import shutil
import subprocess
import tempfile
import uuid

logger = logging.getLogger(__name__)

# How long to let the browser work before giving up. A one-page invoice renders in well
# under a second; this is only here so a wedged process can never hang a web request.
RENDER_TIMEOUT_SECONDS = 60

_BROWSER_RELATIVE_PATHS = (
    r'Microsoft\Edge\Application\msedge.exe',
    r'Google\Chrome\Application\chrome.exe',
)


def find_browser():
    """Full path to a Chromium-based browser that can print to PDF, or None."""
    override = os.environ.get('POS_PDF_BROWSER')
    if override:
        return override if os.path.isfile(override) else None

    roots = [
        os.environ.get('ProgramFiles(x86)'),
        os.environ.get('ProgramW6432'),
        os.environ.get('ProgramFiles'),
        os.environ.get('LOCALAPPDATA'),
    ]
    for root in filter(None, dict.fromkeys(roots)):
        for rel in _BROWSER_RELATIVE_PATHS:
            candidate = os.path.join(root, rel)
            if os.path.isfile(candidate):
                return candidate

    for name in ('msedge', 'chrome', 'chromium', 'google-chrome'):
        found = shutil.which(name)
        if found:
            return found
    return None


def is_available():
    return find_browser() is not None


def _with_base_href(html, base_url):
    """Point relative URLs at the running server.

    The page is handed to the browser as a local file, so `/static/app.css` would otherwise
    resolve against the filesystem and the invoice would lose its stylesheet and logo. A
    <base> tag pins them back to the origin the request came from.
    """
    if not base_url:
        return html
    tag = f'<base href="{base_url}">'
    lowered = html.lower()
    head = lowered.find('<head')
    if head != -1:
        close = lowered.find('>', head)
        if close != -1:
            return html[:close + 1] + tag + html[close + 1:]
    return tag + html


def html_to_pdf(html, base_url=None, landscape=False):
    """Render `html` and return PDF bytes, or None if it could not be produced.

    `base_url` should be the origin the page was served from (e.g.
    request.build_absolute_uri('/')) so stylesheets, fonts and uploaded images resolve.
    Page size and margins come from the template's own @page CSS, which Chromium honours.
    """
    browser = find_browser()
    if not browser:
        logger.warning("No Edge/Chrome found for PDF rendering; caller should fall back to HTML.")
        return None

    work_dir = tempfile.mkdtemp(prefix='digiflow-pdf-')
    src_path = os.path.join(work_dir, f'{uuid.uuid4().hex}.html')
    pdf_path = os.path.join(work_dir, 'out.pdf')

    try:
        with open(src_path, 'w', encoding='utf-8') as fh:
            fh.write(_with_base_href(html, base_url))

        cmd = [
            browser,
            '--headless',
            '--disable-gpu',
            # A throwaway profile: without it the run can attach to (or be blocked by) the
            # signed-in Edge the cashier already has open, which is how "it works on my
            # machine but produces nothing on the till" happens.
            f'--user-data-dir={os.path.join(work_dir, "profile")}',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-extensions',
            # Chromium otherwise stamps the page URL and print date into every margin —
            # the exact junk that made browser-printed receipts look unprofessional.
            '--no-pdf-header-footer',
            f'--print-to-pdf={pdf_path}',
        ]
        if landscape:
            cmd.append('--landscape')
        cmd.append('file:///' + src_path.replace('\\', '/'))

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            # Windowed PyInstaller builds have no console; without this every invoice
            # download would flash a black command window in the cashier's face.
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

        if not os.path.isfile(pdf_path):
            logger.warning(
                "PDF render produced no file (exit %s): %s",
                result.returncode,
                (result.stderr or b'')[-400:],
            )
            return None

        with open(pdf_path, 'rb') as fh:
            data = fh.read()

        # Chromium can leave a truncated file behind if it dies mid-write; a caller that
        # streamed that to the browser would hand the customer a corrupt download.
        if not data.startswith(b'%PDF-'):
            logger.warning("PDF render produced a file that is not a PDF (%d bytes).", len(data))
            return None
        return data

    except subprocess.TimeoutExpired:
        logger.warning("PDF render timed out after %ss.", RENDER_TIMEOUT_SECONDS)
        return None
    except Exception:
        logger.exception("PDF render failed.")
        return None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
