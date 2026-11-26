"""Pembaca text layer PDF yang dijalankan sebagai PROSES TERPISAH.

Dipanggil oleh documents.pdf_text() dengan ``python -I pdf_extract.py <file> <maks_halaman>
<maks_karakter> <maks_memori>``. Script ini sengaja berdiri sendiri (hanya stdlib dan
pypdfium2) agar bisa dijalankan dalam mode isolasi ``-I``. PDFium adalah kode native:
menjalankannya di proses terpisah berarti PDF berbahaya tidak bisa menjatuhkan server,
dan PDF yang macet bisa dihentikan dengan timeout. Hasil dikirim sebagai JSON di stdout.
"""
import json
import sys


def reply(**payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    sys.stdout.flush()
    raise SystemExit(0)


def main() -> None:
    path, max_pages, max_chars, max_memory = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    try:  # batas memori di Linux; Windows tidak punya modul resource
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (max_memory, max_memory))
    except (ImportError, ValueError, OSError):
        pass
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    try:
        document = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        reply(error="encrypted" if "password" in str(exc).lower() else "invalid")
    try:
        if pdfium_c.FPDF_GetSecurityHandlerRevision(document.raw) != -1:
            reply(error="encrypted")
        count = len(document)
        if count > max_pages:
            reply(error="pages", pages=count)
        pages, total = [], 0
        for index in range(count):
            page = document[index]
            textpage = page.get_textpage()
            text = textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
            textpage.close()
            page.close()
            total += len(text)
            if total > max_chars:
                reply(error="chars")
            pages.append(text)
        reply(pages=pages)
    finally:
        document.close()


if __name__ == "__main__":
    main()
