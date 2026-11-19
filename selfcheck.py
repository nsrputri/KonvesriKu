"""Jalankan: python selfcheck.py (setelah memasang requirements)."""
import asyncio
import csv
import io
import json

from fastapi import HTTPException
from starlette.requests import Request
from docx import Document

import app


async def convert(source, target, data):
    async def receive():
        return {'type': 'http.request', 'body': data, 'more_body': False}
    request = Request({'type': 'http', 'query_string': f'source={source}'.encode()}, receive)
    return await app.convert(request, target)


async def check():
    assert app.formats() == {'csv': ['json'], 'json': ['csv'], 'docx': ['txt'], 'txt': ['docx']}
    response = await convert('csv', 'json', 'id,nama\n001,Putri\n'.encode())
    records = json.loads(response.body)
    assert records == [{'id': '001', 'nama': 'Putri'}]
    response = await convert('json', 'csv', json.dumps(records).encode())
    assert list(csv.DictReader(io.StringIO(response.body.decode()))) == records
    text = 'Halo Putri\nKonversi teks Unicode: café'
    response = await convert('txt', 'docx', text.encode())
    assert [p.text for p in Document(io.BytesIO(response.body)).paragraphs] == text.split('\n')
    response = await convert('docx', 'txt', response.body)
    assert response.body.decode() == text
    invalid = [
        ('txt', 'json', b'test', 400),
        ('csv', 'json', b'', 400),
        ('json', 'csv', b'[{"a":{"b":1}}]', 400),
        ('json', 'csv', b'[{"a":1,"a":2}]', 400),
        ('docx', 'txt', b'not a docx', 400),
        ('txt', 'docx', b'x' * (app.MAX_BYTES + 1), 413),
    ]
    for source, target, data, expected in invalid:
        try:
            await convert(source, target, data)
        except HTTPException as exc:
            assert exc.status_code == expected, (source, target, exc.status_code)
        else:
            raise AssertionError(f'Input invalid diterima: {source} -> {target}')
    print('PASS: matriks format, 4 pasangan konversi, 6 validasi error.')


if __name__ == '__main__':
    asyncio.run(check())
