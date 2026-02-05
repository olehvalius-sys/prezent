import os
import datetime
import logging
from typing import Optional
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import qrcode
from PIL import Image
from io import BytesIO

# Cloudinary
import cloudinary
import cloudinary.uploader

# Константи
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png'}

# Зовнішня адреса для QR-кодів
BASE_URL = os.getenv("BASE_URL", "https://prezent-zfsw.onrender.com")

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change_me_to_something_very_secure')

# Database config
DATABASE_URL = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///shields.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['ADMIN_PASSWORD'] = os.getenv('ADMIN_PASSWORD', 'admin')

# Cloudinary конфігурація
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET'),
    secure=True
)

# Реєстрація кастомного фільтра Jinja
@app.template_filter('format_money')
def format_money(value: float) -> str:
    try:
        return f"{value:,.2f}".replace(',', ' ').replace('.', ',')
    except (ValueError, TypeError):
        return "0,00"

db = SQLAlchemy(app)
migrate = Migrate(app, db)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Shield(db.Model):
    __tablename__ = 'shields'
    id = db.Column(db.Integer, primary_key=True)
    street = db.Column(db.String(100), nullable=False)
    client = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    paid = db.Column(db.Boolean, default=False)
    date_created = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
    paid_date = db.Column(db.DateTime, nullable=True, index=True)
    photo_path = db.Column(db.String(500), nullable=True)  # URL з Cloudinary

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

        photo_url = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and allowed_file(file.filename):
                try:
                    upload_result = cloudinary.uploader.upload(
                        file,
                        folder="tarcze",
                        resource_type="image",
                        allowed_formats=["jpg", "jpeg", "png"]
                    )
                    photo_url = upload_result.get('secure_url')
                    logger.info(f"Завантажено в Cloudinary: {photo_url}")
                except Exception as e:
                    logger.error(f"Помилка Cloudinary: {str(e)}")
                    flash(f'Помилка завантаження фото: {str(e)}', 'danger')
                    return redirect(url_for('admin'))
            elif file.filename:
                flash('Dozwolone тільки JPG/PNG.', 'danger')
                return redirect(url_for('admin'))

        new_shield = Shield(
            street=street,
            client=client,
            amount=amount,
            photo_path=photo_url
        )
        db.session.add(new_shield)
        db.session.commit()

        # QR-код
        qr_url = f"{BASE_URL}/public/{new_shield.id}"
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        qr_filename = f"shield_{new_shield.id}.png"
        qr_path = os.path.join('static/qrcodes', qr_filename)
        os.makedirs('static/qrcodes', exist_ok=True)
        img.save(qr_path)

        flash('Tarcza dodana pomyślnie!', 'success')
        return redirect(url_for('admin'))

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
    qr_file = f"shield_{shield.id}.png"
    qr_path = os.path.join('static/qrcodes', qr_file)
    if os.path.exists(qr_path):
        os.remove(qr_path)
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
    return send_from_directory('static/qrcodes', f"shield_{shield_id}.png", as_attachment=True)

@app.route('/static/qrcodes/<filename>')
def serve_qr(filename: str):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return send_from_directory('static/qrcodes', filename)

if __name__ == '__main__':
    os.makedirs('static/qrcodes', exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))