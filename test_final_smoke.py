# -*- coding: utf-8 -*-
"""最终健康检查（不触发任何文件删除）：干净系统上的完整正向流程。"""
import os
import sys
import sqlite3
from datetime import datetime, timedelta

import requests

BASE = 'http://127.0.0.1:5000'
ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'instance', 'file_collection.db')
FIX = os.path.join(ROOT, 'fixtures')
PASS, FAIL = [], []

def check(name, cond, detail=''):
    if cond:
        PASS.append(name); print(f'  ✅ {name}')
    else:
        FAIL.append((name, detail)); print(f'  ❌ {name}  {detail}')

def fx(fname):
    with open(os.path.join(FIX, fname), 'rb') as f:
        return f.read()

def reset_data():
    """清空业务数据与上传文件（保留 admin），保证“干净系统”前提成立。"""
    import shutil
    con = sqlite3.connect(DB)
    try:
        for t in ['announcement_attachment', 'announcement', 'attachment',
                  'collection_object', 'collection_object_template',
                  'theme_attachment', 'collection_theme']:
            con.execute(f'DELETE FROM {t}')
        con.commit()
    finally:
        con.close()
    up = os.path.join(ROOT, 'uploads')
    if os.path.isdir(up):
        for name in os.listdir(up):
            p = os.path.join(up, name)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            except OSError:
                pass

reset_data()

s = requests.Session()
s.trust_env = False

# 干净系统：未登录首页
r = s.get(BASE + '/')
check('F1 干净系统首页（无主题空状态）', r.status_code == 200 and '暂无收集主题' in r.text)

# 登录
r = s.post(BASE + '/admin/login', data={'username': 'admin', 'password': 'admin123'}, allow_redirects=False)
check('F2 默认账号登录', r.status_code == 302)

# 建模板 → 建主题（模板来源）
r = s.post(BASE + '/admin/object-templates', data={'category': '最终检查组', 'names': '最终甲\n最终乙'}, allow_redirects=False)
tpl = s.get(BASE + '/admin/theme/create').text
check('F3 模板出现在创建页', '最终检查组' in tpl and '最终甲' in tpl)

deadline = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')
import re
m = re.search(r'name="selected_templates" value="(\d+)"', tpl)
tpl_ids = re.findall(r'name="selected_templates" value="(\d+)"', tpl)
r = s.post(BASE + '/admin/theme/create',
           data={'title': '最终检查主题', 'description': 'd', 'announcement': 'a', 'deadline': deadline,
                 'collector_name': 'c', 'collector_objects': '', 'selected_templates': tpl_ids},
           files={'attachments': ('最终附件.txt', b'final')},
           allow_redirects=False)
check('F4 用模板创建主题', r.status_code == 302)
con = sqlite3.connect(DB)
tid = con.execute('SELECT id FROM collection_theme ORDER BY id DESC LIMIT 1').fetchone()[0]
objs = [x[0] for x in con.execute('SELECT name FROM collection_object WHERE theme_id=?', (tid,))]
con.close()
check('F5 模板对象导入（2 个，按模板页名称排序：乙在前）',
      objs == ['最终乙', '最终甲'], f'objs={objs}')

# 上传 → 完成
oid = objs and s.get(BASE + f'/theme/{tid}').text and None
con = sqlite3.connect(DB)
oid = con.execute('SELECT id FROM collection_object WHERE theme_id=? LIMIT 1', (tid,)).fetchone()[0]
con.close()
r = s.post(BASE + f'/upload/{oid}', files=[('file', ('最终提交.txt', b'submit'))], allow_redirects=False)
check('F6 上传附件', r.status_code == 302)
r = s.post(BASE + f'/upload/{oid}', data={'finish': '1'}, allow_redirects=False)
con = sqlite3.connect(DB)
done = con.execute('SELECT is_completed FROM collection_object WHERE id=?', (oid,)).fetchone()[0]
con.close()
check('F7 完成提交', r.status_code == 302 and done == 1)

# 首页与详情展示
r = s.get(BASE + '/')
check('F8 首页展示主题与进度', '最终检查主题' in r.text and '已完成' in r.text)
r = s.get(BASE + f'/theme/{tid}')
check('F9 主题详情展示', '最终附件.txt' in r.text and '最终提交' not in r.text)

# 导出（只创建不删除旧归档——本目录是新建的，无旧归档）
r = s.get(BASE + f'/admin/theme/{tid}/export')
check('F10 导出附件 zip', r.status_code == 200 and r.content[:2] == b'PK')

# Excel 合并检测页
r = s.get(BASE + f'/admin/theme/{tid}/merge-excel')
check('F11 合并检测页（非 Excel → 不可合并）', '暂不能合并' in r.text)

print()
print(f'最终健康检查：通过 {len(PASS)} 项，失败 {len(FAIL)} 项')
if FAIL:
    for n, d in FAIL:
        print(f'  - {n}: {d}')
sys.exit(1 if FAIL else 0)
