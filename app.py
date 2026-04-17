import os
from flask import Flask
from config import Config
from models import db, login_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def create_app():
    app = Flask(__name__, 
                template_folder=os.path.join(BASE_DIR, 'app', 'templates'),
                static_folder=os.path.join(BASE_DIR, 'static'))
    app.config.from_object(Config)
    
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'admin_login'
    
    from routes import register_routes
    register_routes(app)
    
    with app.app_context():
        db.create_all()
        from utils import init_default_admin
        init_default_admin()
    
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=False)
