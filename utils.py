import os
import shutil
from werkzeug.security import generate_password_hash
from models import db, Admin
from config import Config

def init_default_admin():
    if not Admin.query.filter_by(username='admin').first():
        admin = Admin(username='admin', password_hash=generate_password_hash('admin123'))
        db.session.add(admin)
        db.session.commit()

def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def get_theme_folder(theme_id):
    folder = os.path.join(Config.UPLOAD_FOLDER, f'theme_{theme_id}')
    if not os.path.exists(folder):
        os.makedirs(folder)
    return folder

def get_object_folder(theme_id, object_id):
    folder = os.path.join(get_theme_folder(theme_id), f'object_{object_id}')
    if not os.path.exists(folder):
        os.makedirs(folder)
    return folder

def get_announcement_folder():
    folder = os.path.join(Config.UPLOAD_FOLDER, 'announcements')
    if not os.path.exists(folder):
        os.makedirs(folder)
    return folder

def rename_uploaded_file(theme_id, object_id, filename, original_name):
    object_folder = get_object_folder(theme_id, object_id)
    name, ext = os.path.splitext(original_name)
    safe_name = f"{name}{ext}"
    counter = 1
    while os.path.exists(os.path.join(object_folder, safe_name)):
        safe_name = f"{name}_{counter}{ext}"
        counter += 1
    return safe_name

def create_export_archive(theme_id, theme_title):
    theme_folder = get_theme_folder(theme_id)
    export_folder = os.path.join(theme_folder, 'export_temp')
    if os.path.exists(export_folder):
        shutil.rmtree(export_folder)
    os.makedirs(export_folder)
    
    from models import CollectionObject, Attachment
    objects = CollectionObject.query.filter_by(theme_id=theme_id).all()
    
    for obj in objects:
        attachments = Attachment.query.filter_by(collection_object_id=obj.id).all()
        if len(attachments) == 1:
            att = attachments[0]
            src = os.path.join(theme_folder, f'object_{obj.id}', att.filename)
            _, ext = os.path.splitext(att.original_name)
            dst = os.path.join(export_folder, f"{obj.name}{ext}")
            if os.path.exists(src):
                shutil.copy2(src, dst)
        else:
            for idx, att in enumerate(attachments, 1):
                src = os.path.join(theme_folder, f'object_{obj.id}', att.filename)
                _, ext = os.path.splitext(att.original_name)
                dst = os.path.join(export_folder, f"{obj.name}_{idx}{ext}")
                if os.path.exists(src):
                    shutil.copy2(src, dst)
    
    archive_name = f"{theme_title}_附件汇总"
    archive_path = os.path.join(theme_folder, archive_name)
    shutil.make_archive(archive_path, 'zip', export_folder)
    shutil.rmtree(export_folder)
    
    return f"{archive_name}.zip"
