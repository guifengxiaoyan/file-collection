# -*- coding: utf-8 -*-
"""补充测试：剩余管理路由（主题附件删除、附件删除、删除主题、空导出等）。"""
import io
import os
import sys
import zipfile
import sqlite3
from datetime import datetime, timedelta

import requests

BASE = 'http://127.0.0.1:5000'
ROOT = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(ROOT, 'fixtures')
DB = os.path.join(ROOT, 'instance', 'file_collection.db')
PASS, FAIL = [], []

def check(name, cond, detail=''):
    if cond:
        PASS.append(name); print(f'  ✅ {name}')
    else:
        FAIL.append((name, detail)); print(f'  ❌ {name}  {detail}')

def fx(fname):
    with open(os.path.join(FIX, fname), 'rb') as f:
        return f.read()

def db_query(sql, args=()):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

s = requests.Session()
s.trust_env = False  # 绕过沙箱代理，直连本地服务
s.post(BASE + '/admin/login', data={'username': 'admin', 'password': 'admin123'})

deadline = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%dT%H:%M')
r = s.post(BASE + '/admin/theme/create',
           data={'title': '补充测试主题', 'deadline': deadline, 'collector_objects': '补充甲\n补充乙'},
           files={'attachments': ('主题附件.txt', 'theme att'.encode())},
           allow_redirects=False)
theme_id = db_query('SELECT id FROM collection_theme ORDER BY id DESC LIMIT 1')[0][0]
obj_id = db_query("SELECT id FROM collection_object WHERE theme_id=? AND name='补充甲'", (theme_id,))[0][0]
obj2_id = db_query("SELECT id FROM collection_object WHERE theme_id=? AND name='补充乙'", (theme_id,))[0][0]
print(f'补充测试主题 theme_id={theme_id}')

print('=' * 70)
print('S1. 主题附件')
print('=' * 70)
r = s.get(BASE + f'/theme/{theme_id}')
check('S1.1 主题页显示附件', '主题附件.txt' in r.text)
tatt = db_query('SELECT id FROM theme_attachment WHERE theme_id=?', (theme_id,))[0][0]
r = s.get(BASE + f'/theme/download/{tatt}')
check('S1.2 下载主题附件', r.status_code == 200 and r.content == b'theme att', f'code={r.status_code}')

r = s.post(BASE + f'/admin/theme/attachment/{tatt}/delete', allow_redirects=False)
check('S1.3 删除主题附件跳转编辑页', r.status_code == 302, f'code={r.status_code}')
check('S1.4 主题附件记录已删', db_query('SELECT COUNT(*) FROM theme_attachment WHERE id=?', (tatt,))[0][0] == 0)

# 编辑主题再上传一个附件（覆盖 edit 路由的附件分支）
r = s.post(BASE + f'/admin/theme/{theme_id}/edit',
           data={'title': '补充测试主题', 'description': '', 'announcement': '', 'deadline': deadline, 'collector_name': ''},
           files={'attachments': ('编辑新增.txt', 'edit att'.encode())},
           allow_redirects=False)
check('S1.5 编辑主题时上传附件', db_query('SELECT COUNT(*) FROM theme_attachment WHERE theme_id=?', (theme_id,))[0][0] == 1)

print()
print('=' * 70)
print('S2. 管理端附件删除')
print('=' * 70)
s.post(BASE + f'/upload/{obj_id}', files=[('file', ('a.txt', b'aaa')), ('file', ('b.txt', b'bbb'))], allow_redirects=False)
att = db_query('SELECT id FROM attachment WHERE collection_object_id=? ORDER BY id LIMIT 1', (obj_id,))[0][0]
r = s.post(BASE + f'/admin/attachment/{att}/delete', allow_redirects=False)
check('S2.1 管理端删除附件跳转', r.status_code == 302, f'code={r.status_code}')
check('S2.2 附件记录已删', db_query('SELECT COUNT(*) FROM attachment WHERE id=?', (att,))[0][0] == 0)

print()
print('=' * 70)
print('S3. 空数据边界')
print('=' * 70)
r = s.get(BASE + f'/admin/theme/{theme_id}/export')
check('S3.1 有附件的主题导出非空 zip', r.status_code == 200 and len(r.content) > 22, f'code={r.status_code}')

# 空主题（无对象无附件）导出 → 空 zip 不报错
r = s.post(BASE + '/admin/theme/create', data={'title': '空主题', 'deadline': deadline}, allow_redirects=False)
empty_id = db_query("SELECT id FROM collection_theme WHERE title='空主题'")[0][0]
r = s.get(BASE + f'/admin/theme/{empty_id}/export')
ok = r.status_code == 200 and r.content[:2] == b'PK'
if ok:
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    ok = len(zf.namelist()) == 0
check('S3.2 空主题导出为空 zip 不报错', ok, f'code={r.status_code}')

# 无附件对象下载
r = s.get(BASE + f'/admin/object/{obj2_id}/download-attachments')
check('S3.3 无附件对象下载给出提示', '该对象没有附件' in r.text, r.text[:100])

# 不满足条件时 POST 合并
r = s.post(BASE + f'/admin/theme/{theme_id}/merge-excel', data={}, allow_redirects=False)
check('S3.4 不满足条件 POST 合并被拦截', r.status_code == 302, f'code={r.status_code}')

print()
print('=' * 70)
print('S4. 删除主题（数据库 + 上传目录）')
print('=' * 70)
up_dir = os.path.join(ROOT, 'uploads', f'theme_{theme_id}')
check('S4.1 删除前上传目录存在', os.path.isdir(up_dir))
r = s.post(BASE + f'/admin/theme/{theme_id}/delete', allow_redirects=False)
check('S4.2 删除主题跳转', r.status_code == 302, f'code={r.status_code}')
check('S4.3 主题记录已删', db_query('SELECT COUNT(*) FROM collection_theme WHERE id=?', (theme_id,))[0][0] == 0)
check('S4.4 收集对象级联删除', db_query('SELECT COUNT(*) FROM collection_object WHERE theme_id=?', (theme_id,))[0][0] == 0)
check('S4.5 附件级联删除', db_query("SELECT COUNT(*) FROM attachment WHERE collection_object_id IN (SELECT id FROM collection_object WHERE theme_id=?)", (theme_id,))[0][0] == 0)
check('S4.6 上传目录已清理', not os.path.exists(up_dir))

print()
print('=' * 70)
print(f'补充测试结果：通过 {len(PASS)} 项，失败 {len(FAIL)} 项')
if FAIL:
    for name, detail in FAIL:
        print(f'  - {name}: {detail}')
print('=' * 70)
sys.exit(1 if FAIL else 0)
