"""Prototipe internal: CSV ke JSON, bukan antrean persisten/layanan publik."""
import csv
import io
import json
from pathlib import Path
import zipfile
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table

FORMATS = {'csv': ['json'], 'json': ['csv'], 'docx': ['txt'], 'txt': ['docx']}
DOWNLOAD_HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}

def attachment(data, target, media_type):
    return Response(data, media_type=media_type, headers={**DOWNLOAD_HEADERS,
        'Content-Disposition': f'attachment; filename="hasil.{target}"'})
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

app = FastAPI(title='KonversiKu • Prototipe UI')
MAX_BYTES = 2 * 1024 * 1024

@app.get('/api/formats')
def formats():
    return FORMATS

@app.get('/')
def home():
    return FileResponse(Path(__file__).with_name('index.html'))

@app.post('/api/convert')
async def convert(request: Request, target: str = 'json'):
    source = request.query_params.get('source', 'csv').lower()
    if target not in FORMATS.get(source, []):
        raise HTTPException(400, 'Pasangan format tidak didukung.')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, 'Batas prototipe 2 MB per file.')
    if not raw:
        raise HTTPException(400, 'File kosong. Pilih file berisi data.')
    if source == 'docx':
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 20 * 1024 * 1024:
                    raise ValueError('Isi DOCX terlalu besar (maksimal 20 MB setelah dibuka).')
            document = Document(io.BytesIO(raw))
            lines = []
            for block in document.iter_inner_content():
                if isinstance(block, Paragraph):
                    lines.append(block.text)
                elif isinstance(block, Table):
                    lines.extend('\t'.join(cell.text for cell in row.cells) for row in block.rows)
            text = '\n'.join(lines)
            if not text.strip():
                raise ValueError('DOCX tidak berisi teks yang bisa dibaca.')
        except Exception as exc:
            raise HTTPException(400, 'DOCX tidak valid, terlalu besar, atau tidak berisi teks. Gunakan .docx (bukan .doc).') from exc
        return attachment(text, 'txt', 'text/plain')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise HTTPException(400, 'Simpan file teks sebagai UTF-8, lalu coba lagi.')
    if source == 'txt':
        try:
            document = Document()
            for line in text.split('\n'):
                document.add_paragraph(line.rstrip('\r'))
            output = io.BytesIO()
            document.save(output)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, 'TXT berisi karakter kontrol yang tidak didukung DOCX.') from exc
        return attachment(output.getvalue(), 'docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    if source == 'json':
        try:
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError('Kunci JSON tidak boleh duplikat.')
                    result[key] = value
                return result
            def invalid_constant(value):
                raise ValueError('Angka JSON harus terbatas.')
            records = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
            if not isinstance(records, list) or not records or len(records) > 10000:
                raise ValueError('JSON harus berupa array 1–10.000 objek datar.')
            if not isinstance(records[0], dict) or not records[0]:
                raise ValueError('Setiap baris JSON harus berupa objek dengan kunci.')
            header = list(records[0])
            for record in records:
                if not isinstance(record, dict) or set(record) != set(header):
                    raise ValueError('Semua objek JSON harus memiliki kunci yang sama.')
                if any(isinstance(value, (dict, list)) for value in record.values()):
                    raise ValueError('JSON bersarang tidak didukung; gunakan nilai skalar.')
            output = io.StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=header)
            writer.writeheader()
            writer.writerows(records)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc))
        return Response(output.getvalue(), media_type='text/csv', headers={
            'Content-Disposition': 'attachment; filename="hasil.csv"',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        })
    try:
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        header = next(reader, None)
        if not header or any(not name.strip() for name in header) or len(set(header)) != len(header):
            raise ValueError('CSV wajib memiliki header unik dan tidak kosong.')
        records = []
        for line, row in enumerate(reader, 2):
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(f'Jumlah kolom tidak sesuai pada record {line}.')
            if len(records) >= 10000:
                raise ValueError('Batas prototipe 10.000 baris data.')
            records.append(dict(zip(header, row)))
        if not records:
            raise ValueError('CSV belum berisi baris data.')
    except (csv.Error, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return Response(json.dumps(records, ensure_ascii=False, indent=2), media_type='application/json', headers={
        'Content-Disposition': 'attachment; filename="hasil.json"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    })
