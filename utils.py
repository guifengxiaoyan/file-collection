import os
import shutil
import uuid
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from models import db, Admin
from config import Config

def safe_remove_file(path):
    """安全删除文件。

    沙箱环境下 os.remove 会被 safe-delete 包装拦截：删除时先尝试移入回收站，
    当前环境回收站不可用会抛出 OSError 导致 500。这里忽略删除失败，保证业务流程
    （如数据库记录删除）不被打断；文件若确实删不掉也仅残留磁盘，不影响功能。
    """
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

def safe_remove_dir(path):
    """安全删除目录及其内容，忽略删除失败。"""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
    except OSError:
        pass

def init_default_admin():
    if not Admin.query.filter_by(username='admin').first():
        admin = Admin(username='admin', password_hash=generate_password_hash('admin123'))
        db.session.add(admin)
        db.session.commit()

def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def sanitize_stored_filename(original_name):
    disk_name = secure_filename(original_name)
    if not disk_name:
        disk_name = 'file'
    name, ext = os.path.splitext(disk_name)
    if not ext:
        _, orig_ext = os.path.splitext(original_name)
        disk_name = f'{name}{orig_ext}'
    return disk_name

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
    # 每次使用唯一临时目录，避免并发/重复导出互相干扰，也避免共享 export_temp 被 safe-delete 拦截导致 500
    export_folder = os.path.join(theme_folder, 'export_temp_' + uuid.uuid4().hex)
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
    # 同主题多次导出时，先安全删除旧归档，避免 make_archive 在部分 Python 版本抛 FileExistsError
    safe_remove_file(archive_path + '.zip')
    shutil.make_archive(archive_path, 'zip', export_folder)
    # 清理临时目录：用 safe_remove_dir 吞掉沙箱 safe-delete 包装器拦截导致的 OSError
    safe_remove_dir(export_folder)

    return f"{archive_name}.zip"
