import io
import tempfile
import unittest
from app import create_app
from analysis import analyse, parse_csv

class SpendingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name)
        self.client = self.app.test_client()
        self.client.get('/')
        with self.client.session_transaction() as s:
            self.headers = {'X-CSRF-Token': s['csrf']}
    def tearDown(self):
        self.temp.cleanup()
    def upload(self, data):
        return self.client.post('/api/import',headers=self.headers,data={'replace':'yes','file':(io.BytesIO(data),'data.csv')})
    def test_demo_and_patterns(self):
        self.client.post('/api/demo', headers=self.headers)
        data=self.client.get('/api/transactions').json
        self.assertEqual(len(data['rows']),24)
        self.assertEqual(sum(r['duplicate'] for r in data['rows']),2)
        self.assertEqual({p['merchant'] for p in data['recurring']},{'Streambox','Bright Mobile','North Energy','City Rail'})
    def test_invalid_import_preserves_data(self):
        self.client.post('/api/demo',headers=self.headers)
        response=self.upload(b'date,merchant,amount\n2026-09-01,Shop,2.00\n2026-02-30,Shop,NaN\n')
        self.assertEqual(response.status_code,400)
        self.assertEqual(len(self.client.get('/api/transactions').json['rows']),24)
    def test_validation(self):
        for amount in ['NaN','Infinity','1.234','1000001','text']:
            with self.subTest(amount=amount),self.assertRaises(ValueError):
                parse_csv(f'date,merchant,amount\n2026-09-01,Shop,{amount}\n'.encode())
        rows=parse_csv(b'date,merchant,amount\n2026-09-01,Shop,-12.00\n')
        self.assertEqual(rows[0]['cents'],-1200)
    def test_csrf(self):
        self.assertEqual(self.client.post('/api/demo').status_code,403)
    def test_review_and_export(self):
        self.upload(b'date,merchant,amount\n2026-09-01,=HYPERLINK("bad"),2.00\n')
        row=self.client.get('/api/transactions').json['rows'][0]
        response=self.client.post(f"/api/transactions/{row['id']}",headers=self.headers,json={'category':'Bills','reviewed':True})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/transactions').json['rows'][0]['reviewed'],1)
        report=self.client.get('/report.csv').data.decode('utf-8-sig')
        self.assertIn("'=HYPERLINK",report)
        self.assertIn('Bills,no,yes',report)
    def test_same_day_copies_not_recurring(self):
        rows=[dict(id=i,date='2026-09-01',merchant='Test',cents=100) for i in range(3)]
        rows,patterns=analyse(rows)
        self.assertEqual(patterns,[])
        self.assertTrue(all(r['duplicate'] for r in rows))
    def test_bad_category_and_missing_row(self):
        self.assertEqual(self.client.post('/api/transactions/999',headers=self.headers,json={'category':'Bad','reviewed':True}).status_code,400)
        self.assertEqual(self.client.post('/api/transactions/999',headers=self.headers,json={'category':'Food','reviewed':True}).status_code,404)
    def test_confirmation_required(self):
        response=self.client.post('/api/import',headers=self.headers,data={'file':(io.BytesIO(b'date,merchant,amount\n2026-09-01,Shop,2\n'),'data.csv')})
        self.assertEqual(response.status_code,400)

if __name__ == '__main__':
    unittest.main()
