import os
import datetime
import logging
from typing import Optional
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
import qrcode
from PIL import Image
from io import BytesIO

# Константи
UPLOAD_FOLDER_PHOTOS = 'static/photos'
UPLOAD_FOLDER_QRCODES = 'static/qrcodes'
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png'}
MAX_FILE_SIZE = 16 * 1024 * 1024  # 16 MB

# Зовнішня адреса твого додатку на Render (можна змінити)
BASE_URL = "https://prezent-zfsw.onrender.com"

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change_me_to_something_very_secure')

# Підключення до бази даних
# На Render обов’язково вказати змінну середовища SQLALCHEMY_DATABASE_URI
# Якщо змінної немає — fallback на локальну sqlite (тільки для розробки)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'SQLALCHEMY_DATABASE_URI',
    'sqlite:///shields.db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER_PHOTOS'] = UPLOAD_FOLDER_PHOTOS
app.config['UPLOAD_FOLDER_QRCODES'] = UPLOAD_FOLDER_QRCODES
app.config['ALLOWED_EXTENSIONS'] = ALLOWED_EXTENSIONS
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE
app.config['ADMIN_PASSWORD'] = os.getenv('ADMIN_PASSWORD', 'admin')  # Зміни!

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@app.template_filter('format_money')
def format_money(value: float) -> str:
    try:
        return f"{value:,.2f}".replace(',', ' ').replace('.', ',')
    except (ValueError, TypeError):
        return "0,00"

os.makedirs(UPLOAD_FOLDER_PHOTOS, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_QRCODES, exist_ok=True)

class Shield(db.Model):
    __tablename__ = 'shields'
    id = db.Column(db.Integer, primary_key=True)
    street = db.Column(db.String(100), nullable=False)
    client = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    paid = db.Column(db.Boolean, default=False)
    date_created = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
    paid_date = db.Column(db.DateTime, nullable=True, index=True)
    photo_path = db.Column(db.String(200), nullable=True)

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def delete_file(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
        logger.info(f"Plik usunięty: {path}")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == app.config['ADMIN_PASSWORD']:
            session['logged_in'] = True
            flash('Pomyślne logowanie!', 'success')
            return redirect(url_for('admin'))
        flash('Nieprawidłowe hasło!', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Zostałeś wylogowany.', 'info')
    return redirect(url_for('login'))

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        street = request.form.get('street', '').strip()
        client = request.form.get('client', '').strip()
        amount_str = request.form.get('amount', '')

        if not street or not client or not amount_str:
            flash('Wszystkie pola są wymagane!', 'danger')
            return redirect(url_for('admin'))

        try:
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash('Kwota musi być liczbą dodatnią!', 'danger')
            return redirect(url_for('admin'))

        photo_filename = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                photo_filename = secure_filename(f"shield_{timestamp}.{ext}")
                file.save(os.path.join(UPLOAD_FOLDER_PHOTOS, photo_filename))
            elif file.filename:
                flash('Dozwolone tylko JPG/PNG.', 'danger')
                return redirect(url_for('admin'))

        new_shield = Shield(street=street, client=client, amount=amount, photo_path=photo_filename)
        db.session.add(new_shield)
        db.session.commit()

        # Генерація QR з правильним зовнішнім посиланням
        qr_url = f"{BASE_URL}/public/{new_shield.id}"

        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        qr_filename = f"shield_{new_shield.id}.png"
        qr_path = os.path.join(UPLOAD_FOLDER_QRCODES, qr_filename)
        img.save(qr_path)

        flash('Tarcza dodana pomyślnie!', 'success')
        return redirect(url_for('admin'))

    # Пагінація та сортування
    page = request.args.get('page', 1, type=int)
    per_page = 20
    sort_by = request.args.get('sort', 'date_created')
    sort_dir = request.args.get('dir', 'desc')

    query = Shield.query
    if sort_by == 'amount':
        query = query.order_by(Shield.amount.desc() if sort_dir == 'desc' else Shield.amount.asc())
    elif sort_by == 'paid':
        query = query.order_by(Shield.paid.desc() if sort_dir == 'desc' else Shield.paid.asc())
    elif sort_by == 'paid_date':
        query = query.order_by(Shield.paid_date.desc() if sort_dir == 'desc' else Shield.paid_date.asc())
    else:
        query = query.order_by(Shield.date_created.desc() if sort_dir == 'desc' else Shield.date_created.asc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template('admin.html', shields=pagination.items, pagination=pagination,
                           sort_by=sort_by, sort_dir=sort_dir)

@app.route('/toggle_paid/<int:shield_id>')
def toggle_paid(shield_id: int):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    shield = Shield.query.get_or_404(shield_id)
    shield.paid = not shield.paid
    shield.paid_date = datetime.datetime.utcnow() if shield.paid else None
    db.session.commit()
    flash(f"Status zmieniony na {'opłacony' if shield.paid else 'nieopłacony'}!", 'success')
    return redirect(url_for('admin'))

@app.route('/delete_shield/<int:shield_id>', methods=['POST'])
def delete_shield(shield_id: int):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    shield = Shield.query.get_or_404(shield_id)
    if shield.photo_path:
        delete_file(os.path.join(UPLOAD_FOLDER_PHOTOS, shield.photo_path))
    delete_file(os.path.join(UPLOAD_FOLDER_QRCODES, f"shield_{shield.id}.png"))
    db.session.delete(shield)
    db.session.commit()
    flash('Tarcza usunięta!', 'success')
    return redirect(url_for('admin'))

@app.route('/public/<int:shield_id>')
def public(shield_id: int):
    shield = Shield.query.get_or_404(shield_id)
    return render_template('public.html', shield=shield)

@app.route('/download_qr/<int:shield_id>')
def download_qr(shield_id: int):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return send_from_directory(UPLOAD_FOLDER_QRCODES, f"shield_{shield_id}.png", as_attachment=True)

@app.route('/static/photos/<filename>')
def serve_photo(filename: str):
    return send_from_directory(UPLOAD_FOLDER_PHOTOS, filename)

@app.route('/static/qrcodes/<filename>')
def serve_qr(filename: str):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return send_from_directory(UPLOAD_FOLDER_QRCODES, filename)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)