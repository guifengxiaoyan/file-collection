import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(BASE_DIR, "instance", "file_collection.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'zip', 'rar', '7z', 'png', 'jpg', 'jpeg', 'gif'}

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)
