"""Local, single-user spending review dashboard."""
import csv
import hmac
import io
import secrets
import sqlite3
from pathlib import Path
from flask import Flask, jsonify, render_template, request, session, send_file
from analysis import CATEGORIES, analyse, parse_csv, safe_csv_cell

ROOT = Path(__file__).resolve().parent

def create_app(data_dir=None):
    app = Flask(__name__)
    folder = Path(data_dir) if data_dir else ROOT / 'instance'
    folder.mkdir(parents=True, exist_ok=True)
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=2*1024*1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
    db_path = folder / 'spending.sqlite'
    def connect():
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        return db
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, date TEXT NOT NULL, merchant TEXT NOT NULL, cents INTEGER NOT NULL, category TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0)')
    @app.before_request
    def protect():
        if request.method == 'POST':
            token = request.headers.get('X-CSRF-Token', '')
            if not token or not hmac.compare_digest(token, session.get('csrf', '')):
                return jsonify(error='Refresh the page and try again.'), 403
    @app.after_request
    def headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response
    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error='File is too large. Maximum upload is 2 MB.'), 413
    def all_rows():
        with connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM transactions ORDER BY date DESC, id DESC')]
    @app.get('/')
    def index():
        session.setdefault('csrf', secrets.token_hex(32))
        return render_template('index.html', token=session['csrf'], categories=CATEGORIES)
    @app.get('/api/transactions')
    def transactions():
        rows, recurring = analyse(all_rows())
        return jsonify(rows=rows, recurring=recurring)
    def replace(raw):
        rows = parse_csv(raw)  # Validate everything before replacing previous data.
        with connect() as db:
            db.execute('DELETE FROM transactions')
            db.executemany('INSERT INTO transactions(date,merchant,cents,category) VALUES(:date,:merchant,:cents,:category)', rows)
        return jsonify(count=len(rows))
    @app.post('/api/import')
    def upload():
        item = request.files.get('file')
        if not item or not item.filename.lower().endswith('.csv'):
            return jsonify(error='Choose a CSV file.'), 400
        if request.form.get('replace') != 'yes':
            return jsonify(error='Confirm replacement of the current data first.'), 400
        try:
            return replace(item.read())
        except ValueError as error:
            return jsonify(error=str(error)), 400
    @app.post('/api/demo')
    def demo():
        return replace((ROOT / 'sample-data' / 'demo.csv').read_bytes())
    @app.post('/api/transactions/<int:row_id>')
    def edit(row_id):
        data = request.get_json(silent=True) or {}
        if data.get('category') not in CATEGORIES or type(data.get('reviewed')) is not bool:
            return jsonify(error='Choose a valid category and review status.'), 400
        with connect() as db:
            cursor = db.execute('UPDATE transactions SET category=?, reviewed=? WHERE id=?', (data['category'], int(data['reviewed']), row_id))
        if cursor.rowcount == 0:
            return jsonify(error='Transaction no longer exists.'), 404
        return jsonify(ok=True)
    @app.get('/sample.csv')
    def sample():
        return send_file(ROOT / 'sample-data' / 'demo.csv', as_attachment=True, download_name='sample-spending.csv')
    @app.get('/report.csv')
    def export():
        rows, _ = analyse(all_rows())
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['date', 'merchant', 'amount_gbp', 'category', 'possible_duplicate', 'reviewed'])
        for row in rows:
            writer.writerow([row['date'], safe_csv_cell(row['merchant']), f"{row['cents']/100:.2f}", row['category'], 'yes' if row['duplicate'] else 'no', 'yes' if row['reviewed'] else 'no'])
        return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name='spending-review.csv')
    return app

if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=5050, debug=False)
