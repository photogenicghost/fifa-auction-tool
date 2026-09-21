"""Isolated import and image-source regression checks; leaves live data untouched."""
import io
import os
import secrets
import tempfile
from pathlib import Path

from openpyxl import Workbook


with tempfile.TemporaryDirectory(prefix='auction-images-') as directory:
    os.environ.update(DATA_DIR=directory, ADMIN_PASSWORD=secrets.token_hex(16),
                      SESSION_SECRET=secrets.token_hex(32), COOKIE_SECURE='false')
    import main
    from fastapi.testclient import TestClient

    workbook = Workbook()
    people = workbook.active
    people.title = 'Total Points'
    people.append(['Total Points', 'NAME', 'Email'])
    people.append([100, 'Test Person', 'test@example.com'])
    prizes = workbook.create_sheet('Auction Prize List')
    prizes.append(['Product Description', 'SKU', 'Link to Image'])
    sources = [
        'https://example.com/image.webp?width=500&v=2',
        'https://example.com/image-without-extension',
        'data:image/webp;base64,UklGRg==',
        '//example.com/image.avif',
        '=HYPERLINK("https://example.com/image.png","View image")',
        'Click here',
        'javascript:alert(1)',
    ]
    for index, source in enumerate(sources):
        prizes.append([f'Prize {index}', None, source])
    prizes['C7'].hyperlink = 'https://example.com/linked.jpg'
    output = io.BytesIO()
    workbook.save(output)
    users, imported, warnings = main.parse_xlsx(output.getvalue())
    assert len(imported) == 7 and len(warnings) == 1
    assert imported[0][2] == sources[0]
    assert imported[1][2] == sources[1]
    assert imported[2][2] == sources[2]
    assert imported[3][2] == 'https://example.com/image.avif'
    assert imported[4][2] == 'https://example.com/image.png'
    assert imported[5][2] == 'https://example.com/linked.jpg'
    assert imported[6][2] == ''
    assert main.parse_xlsx(output.getvalue())[1] == imported
    for invalid in ('file:///C:/image.png', 'data:text/html;base64,WA==',
                    'data:image/webp;base64,invalid!', 'https://[bad'):
        assert main.normalize_image_url(invalid) == ''
    assert main.auction_payload({'image_url': sources[2], 'status': 'READY'})['image_url'] == sources[2]
    client = TestClient(main.app)
    connection = main.db()
    for name, sku, image, quantity in imported:
        connection.execute('INSERT INTO prizes(mode,name,sku,image_url,quantity) VALUES(?,?,?,?,?)',
                           (main.mode(), name, sku, image, quantity))
    connection.close()
    catalog = client.get('/catalog')
    assert catalog.status_code == 200
    assert 'data:image/webp;base64,UklGRg==' in catalog.text
    assert 'javascript:alert' not in catalog.text
    assert 'referrerpolicy="no-referrer"' in catalog.text
    source = Path('C:/Users/thstp/Downloads/FIFA Auction Tracker (2).xlsx')
    if source.exists():
        _, actual, warnings = main.parse_xlsx(source.read_bytes())
        assert len(actual) == 19 and sum(p[3] for p in actual) == 21
        assert all(p[2] for p in actual) and not warnings
        assert next(p for p in actual if 'Oura' in p[0])[2].startswith('data:image/webp;base64,')
        print('Attached workbook: 19 unique prizes, 21 units, all 19 image sources imported.')
    print('Image regression checks passed; live database unchanged.')
