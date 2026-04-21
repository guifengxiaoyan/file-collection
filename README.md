# 文件收集系统

一个基于 Flask 的文件收集系统，支持多主题管理、文件上传收集、公告栏等功能。

## 功能特性

- **首页公告栏** - 管理员可发布公告并上传附件
- **收集主题管理** - 创建、编辑、归档/恢复主题
- **收集对象管理** - 支持 Excel 批量导入收集对象
- **文件上传** - 多附件上传，自动以收集对象名称命名
- **进度追踪** - 实时显示已完成/未完成状态
- **附件导出** - 一键导出所有附件
- **科技感 UI** - 深色主题，现代化界面设计

## 技术栈

- **后端**: Python 3.11 + Flask
- **数据库**: SQLite
- **前端**: HTML5 + CSS3 + Font Awesome
- **部署**: Docker

## 快速开始

### Docker 部署（推荐）

```bash
# 1. 下载部署包
git clone https://github.com/guifengxiaoyan/file-collection.git

# 2. 进入项目目录
cd file-collection

# 3. 启动服务
docker-compose up -d

# 4. 访问 http://服务器IP:5000

# 更新代码并重建容器
cd file-collection
git pull
docker-compose up -d --build --force-recreate

### 本地开发

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动服务
python app.py

# 3. 访问 http://localhost:5000
```

## 默认账号

- **用户名**: admin
- **密码**: admin123

## 目录结构

```
file-collection/
├── app.py                 # 应用入口
├── config.py              # 配置文件
├── models.py              # 数据模型
├── routes.py              # 路由和业务逻辑
├── utils.py               # 工具函数
├── requirements.txt       # Python 依赖
├── Dockerfile             # Docker 镜像构建
├── docker-compose.yml     # Docker Compose 配置
├── entrypoint.sh          # 容器启动脚本
├── DEPLOY.md              # 部署说明
├── app/
│   └── templates/         # HTML 模板
└── static/
    └── css/              # 样式文件
```

## 数据存储

- **上传文件**: `uploads/` 目录
- **数据库**: `instance/file_collection.db`

## API 端点

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 首页 |
| `/admin` | GET | 管理员后台 |
| `/admin/login` | POST | 管理员登录 |
| `/theme/<id>` | GET | 主题详情 |
| `/upload/<object_id>` | GET/POST | 文件上传 |
| `/admin/theme/create` | POST | 创建主题 |
| `/admin/theme/<id>/export` | GET | 导出附件 |

## 许可证

MIT License
