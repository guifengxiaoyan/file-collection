# 内网文件收集系统 - Docker 部署指南

## 快速启动

```bash
# 构建并启动容器
docker-compose up -d

# 查看日志
docker-compose logs -f
```

## 访问系统

启动后访问 http://localhost:5000

默认管理员账号：`admin` / `admin123`

## 数据持久化

- 上传文件：`./uploads` 目录
- 数据库：`./data` 目录

## 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| SECRET_KEY | Flask 密钥 | dev-secret-key-change-in-production |
| FLASK_ENV | 运行模式 | production |

### 修改 SECRET_KEY

1. 编辑 `docker-compose.yml`
2. 修改 `SECRET_KEY` 的值
3. 重启容器：`docker-compose restart`

## 常用命令

```bash
# 停止服务
docker-compose down

# 重新构建（如有代码更新）
docker-compose up -d --build

# 进入容器
docker exec -it file-collection bash
```

## 内网服务器部署

将整个项目目录复制到内网服务器后执行：

```bash
docker-compose up -d
```
