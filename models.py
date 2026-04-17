from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin
from datetime import datetime

db = SQLAlchemy()
login_manager = LoginManager()

class Admin(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)

    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)

@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))

class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    attachments = db.relationship('AnnouncementAttachment', backref='announcement', lazy=True, cascade='all, delete-orphan')

class AnnouncementAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(500), nullable=False)
    original_name = db.Column(db.String(200), nullable=False)
    announcement_id = db.Column(db.Integer, db.ForeignKey('announcement.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

class CollectionTheme(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    announcement = db.Column(db.Text)
    deadline = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    collector_name = db.Column(db.String(100))
    collection_objects = db.relationship('CollectionObject', backref='theme', lazy=True, cascade='all, delete-orphan')
    attachments = db.relationship('ThemeAttachment', backref='theme', lazy=True, cascade='all, delete-orphan')

class ThemeAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(500), nullable=False)
    original_name = db.Column(db.String(200), nullable=False)
    theme_id = db.Column(db.Integer, db.ForeignKey('collection_theme.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

class CollectionObject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    theme_id = db.Column(db.Integer, db.ForeignKey('collection_theme.id'), nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    attachments = db.relationship('Attachment', backref='collection_object', lazy=True, cascade='all, delete-orphan')

class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(500), nullable=False)
    original_name = db.Column(db.String(200), nullable=False)
    collection_object_id = db.Column(db.Integer, db.ForeignKey('collection_object.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
