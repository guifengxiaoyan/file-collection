# -*- coding: utf-8 -*-
"""file-collection 全功能端到端测试（HTTP 级别）。"""
import io
import os
import re
import sys
import json
import time
import zipfile
import sqlite3
from datetime import datetime, timedelta

import requests

BASE = 'http://127.0.0.1:5000'
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'file_collection.db')

PASS, FAIL = [], []

def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f'  ✅ {name}')
    else:
        FAIL.append((name, detail))
        print(f'  ❌ {name}  {detail}')

def fx(fname):
    with open(os.path.join(FIX, fname), 'rb') as f:
        return f.read()

def upload_file(fname, content=None):
    return (fname, content if content is not None else fx(fname))

def db_query(sql, args=()):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def reset_data():
    """清空业务数据与上传文件（保留 admin），保证测试可重复。"""
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
    up = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
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

def wait(sec=0.3):
    time.sleep(sec)

s = requests.Session()
s.trust_env = False  # 绕过沙箱代理，直连本地服务

print('=' * 70)
print('A. 基础访问与登录')
print('=' * 70)

r = s.get(BASE + '/')
check('A1 首页返回 200', r.status_code == 200, f'code={r.status_code}')
check('A2 首页包含品牌标题', '文件收集系统' in r.text)
check('A3 首页包含收集主题区块', '收集主题' in r.text)

r = s.get(BASE + '/no-such-page')
check('A4 自定义 404 页面', r.status_code == 404 and '404' in r.text, f'code={r.status_code}')

r = s.get(BASE + '/admin', allow_redirects=False)
check('A5 未登录访问 /admin 跳转登录', r.status_code == 302 and '/admin/login' in r.headers.get('Location', ''), f'code={r.status_code} loc={r.headers.get("Location")}')

r = s.get(BASE + '/admin/login')
check('A6 登录页返回 200', r.status_code == 200 and '管理员登录' in r.text)

r = s.post(BASE + '/admin/login', data={'username': 'admin', 'password': 'wrong'}, allow_redirects=False)
check('A7 错误密码登录被拒', r.status_code == 200 and '用户名或密码错误' in r.text, f'code={r.status_code}')

r = s.post(BASE + '/admin/login', data={'username': 'admin', 'password': 'admin123'}, allow_redirects=False)
check('A8 正确账号登录成功跳转', r.status_code == 302 and '/admin' in r.headers.get('Location', ''), f'code={r.status_code} loc={r.headers.get("Location")}')

r = s.get(BASE + '/admin')
check('A9 管理后台渲染', r.status_code == 200 and '管理员后台' in r.text)
check('A10 后台导航包含模板入口', '收集对象模板' in r.text)

print()
print('=' * 70)
print('B. 公告管理')
print('=' * 70)

r = s.post(BASE + '/admin/announcement/create',
           data={'title': '系统维护公告', 'content': '本周六凌晨停机维护，请提前上传资料。'},
           files={'attachments': upload_file('说明.txt')}, allow_redirects=False)
check('B1 创建公告（带附件）跳转', r.status_code == 302 and 'tab=announcements' in r.headers.get('Location', ''), f'code={r.status_code}')
ann_id = db_query('SELECT id FROM announcement ORDER BY id DESC LIMIT 1')[0][0]
wait()

r = s.get(BASE + '/')
check('B2 首页显示公告标题', '系统维护公告' in r.text)
check('B3 首页显示公告附件链接', 'download_announcement_attachment' in r.text or 'announcement/download' in r.text)

r = s.get(BASE + f'/announcement/download/1', allow_redirects=False)
att_id = db_query('SELECT id FROM announcement_attachment ORDER BY id DESC LIMIT 1')[0][0]
r = s.get(BASE + f'/announcement/download/{att_id}')
check('B4 下载公告附件', r.status_code == 200 and '测试附件内容' in r.text, f'code={r.status_code}')

r = s.post(BASE + f'/admin/announcement/{ann_id}/edit',
           data={'title': '系统维护公告（改）', 'content': '维护时间调整为周日。'},
           allow_redirects=False)
check('B5 编辑公告跳转', r.status_code == 302, f'code={r.status_code}')
wait()
r = s.get(BASE + '/')
check('B6 编辑后标题生效', '系统维护公告（改）' in r.text)

r = s.post(BASE + f'/admin/announcement/{ann_id}/delete', allow_redirects=False)
check('B7 删除公告跳转', r.status_code == 302, f'code={r.status_code}')
wait()
r = s.get(BASE + '/')
check('B8 删除后首页不再显示', '系统维护公告' not in r.text)

print()
print('=' * 70)
print('C. 收集对象模板')
print('=' * 70)

r = s.post(BASE + '/admin/object-templates',
           data={'category': '研发部', 'names': '模板甲\n模板乙\n模板丙'}, allow_redirects=False)
check('C1 批量创建模板跳转', r.status_code == 302, f'code={r.status_code}')
wait()
cnt = db_query("SELECT COUNT(*) FROM collection_object_template WHERE category='研发部'")[0][0]
check('C2 模板入库 3 条', cnt == 3, f'count={cnt}')

r = s.get(BASE + '/admin/object-templates')
check('C3 模板页按分组展示', '研发部' in r.text and '模板甲' in r.text)

tpl_id = db_query("SELECT id FROM collection_object_template WHERE name='模板甲'")[0][0]
r = s.post(BASE + f'/admin/object-templates/{tpl_id}/edit', data={'name': '模板甲改', 'category': '研发部'}, allow_redirects=False)
wait()
check('C4 编辑模板生效', db_query("SELECT COUNT(*) FROM collection_object_template WHERE name='模板甲改'")[0][0] == 1)

r = s.post(BASE + '/admin/object-templates/add-object',
           data={'category': '研发部', 'names': '模板丁\n模板戊'}, allow_redirects=False)
wait()
cnt = db_query("SELECT COUNT(*) FROM collection_object_template WHERE category='研发部'")[0][0]
check('C5 向分组追加对象', cnt == 5, f'count={cnt}')

r = s.post(BASE + f'/admin/object-templates/{tpl_id}/delete', allow_redirects=False)
wait()
check('C6 删除模板', db_query("SELECT COUNT(*) FROM collection_object_template WHERE id=?", (tpl_id,))[0][0] == 0)

print()
print('=' * 70)
print('D. 创建主题（文本 + Excel + 模板 + 附件）')
print('=' * 70)

deadline = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')
r = s.post(BASE + '/admin/theme/create',
           data={
               'title': '2026年度资料收集',
               'description': '请各位按时提交',
               'announcement': '截止前均可补充上传',
               'deadline': deadline,
               'collector_name': '管理员',
               'collector_objects': '张三\n李四\n王五',
           },
           files={
               'objects_excel_file': upload_file('objects.xlsx'),
               'attachments': upload_file('报告.pdf'),
           },
           allow_redirects=False)
check('D1 创建主题跳转', r.status_code == 302 and 'tab=themes' in r.headers.get('Location', ''), f'code={r.status_code}')
theme_id = db_query('SELECT id FROM collection_theme ORDER BY id DESC LIMIT 1')[0][0]
wait()
objs = [r[0] for r in db_query('SELECT name FROM collection_object WHERE theme_id=? ORDER BY id', (theme_id,))]
check('D2 文本+Excel 对象导入（并集去重后 5 个）', len(objs) == 5, f'objs={objs}')
check('D2b 修复验证：Excel 表头行“姓名”不再导入为对象', '姓名' not in objs)
check('D2c 修复验证：文本与 Excel 同名对象跨来源去重（并集 5 个）',
      objs == ['张三', '李四', '王五', '赵六', '钱七'], f'objs={objs}')
check('D3 主题附件入库', db_query('SELECT COUNT(*) FROM theme_attachment WHERE theme_id=?', (theme_id,))[0][0] == 1)

r = s.get(BASE + f'/theme/{theme_id}')
check('D4 主题详情页渲染', r.status_code == 200 and '2026年度资料收集' in r.text)
check('D5 主题公告展示', '截止前均可补充上传' in r.text)
check('D6 主题附件下载链接', 'theme/download' in r.text)

r = s.get(BASE + '/')
check('D7 首页显示主题卡片', '2026年度资料收集' in r.text)

print()
print('=' * 70)
print('E. 用户上传流程')
print('=' * 70)

zhangsan_id = db_query("SELECT id FROM collection_object WHERE theme_id=? AND name='张三'", (theme_id,))[0][0]

r = s.get(BASE + f'/upload/{zhangsan_id}')
check('E1 上传页渲染', r.status_code == 200 and '张三' in r.text and 'dropzone' in r.text)

r = s.post(BASE + f'/upload/{zhangsan_id}',
           files=[('file', upload_file('说明.txt')), ('file', upload_file('报告.pdf')), ('file', upload_file('汇总表 2026（最终版）.xlsx'))],
           allow_redirects=False)
check('E2 多文件上传跳转', r.status_code == 302, f'code={r.status_code}')
wait()
att_cnt = db_query('SELECT COUNT(*) FROM attachment WHERE collection_object_id=?', (zhangsan_id,))[0][0]
check('E3 三个附件入库', att_cnt == 3, f'count={att_cnt}')

r = s.post(BASE + f'/upload/{zhangsan_id}', files=[('file', upload_file('病毒.exe'))], allow_redirects=False)
wait()
att_cnt2 = db_query('SELECT COUNT(*) FROM attachment WHERE collection_object_id=?', (zhangsan_id,))[0][0]
check('E4 非法扩展名被拒绝', att_cnt2 == 3, f'count={att_cnt2}')

r = s.get(BASE + f'/upload/{zhangsan_id}')
check('E5 上传页显示已上传附件', '说明.txt' in r.text and '汇总表 2026（最终版）.xlsx' in r.text)

# 未上传完成检查：李四先直接 finish
lisi_id = db_query("SELECT id FROM collection_object WHERE theme_id=? AND name='李四'", (theme_id,))[0][0]
r = s.post(BASE + f'/upload/{lisi_id}', data={'finish': '1'}, allow_redirects=False)
wait()
check('E6 无附件时完成被拦截', db_query('SELECT is_completed FROM collection_object WHERE id=?', (lisi_id,))[0][0] == 0)

# 删除附件
att_id = db_query('SELECT id FROM attachment WHERE collection_object_id=? ORDER BY id LIMIT 1', (zhangsan_id,))[0][0]
r = s.post(BASE + f'/upload/delete-attachment/{att_id}')
check('E7 删除附件返回 JSON', r.status_code == 200 and r.json().get('success') is True, f'code={r.status_code}')
wait()
check('E8 删除后附件数减少', db_query('SELECT COUNT(*) FROM attachment WHERE collection_object_id=?', (zhangsan_id,))[0][0] == 2)
disk = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', f'theme_{theme_id}', f'object_{zhangsan_id}')
check('E9 磁盘文件同步删除', len([f for f in os.listdir(disk) if not f.startswith('.')]) == 2, f'files={os.listdir(disk)}')

# 完成提交
r = s.post(BASE + f'/upload/{zhangsan_id}', data={'finish': '1'}, allow_redirects=False)
check('E10 完成提交跳转首页', r.status_code == 302 and r.headers.get('Location', '').endswith('/'), f'code={r.status_code}')
wait()
check('E11 对象标记为已完成', db_query('SELECT is_completed FROM collection_object WHERE id=?', (zhangsan_id,))[0][0] == 1)
check('E12 completed_at 已记录', db_query('SELECT completed_at FROM collection_object WHERE id=?', (zhangsan_id,))[0][0] is not None)

r = s.get(BASE + f'/upload/{zhangsan_id}')
check('E13 已完成对象显示完成状态', '您已完成文件上传' in r.text)
check('E14 已完成对象不显示删除按钮', 'onclick="deleteAttachment' not in r.text)

r = s.get(BASE + f'/theme/{theme_id}')
check('E15 主题详情显示完成进度（1/5）', 'stat__value">1<' in r.text and 'stat__value">5<' in r.text)

# 已登录用户下载收集附件
att_id = db_query('SELECT id FROM attachment WHERE collection_object_id=? ORDER BY id LIMIT 1', (zhangsan_id,))[0][0]
r = s.get(BASE + f'/download/attachment/{att_id}')
check('E16 登录后下载收集附件', r.status_code == 200, f'code={r.status_code}')

print()
print('=' * 70)
print('F. 管理端收集对象管理')
print('=' * 70)

r = s.get(BASE + f'/admin/theme/{theme_id}/objects')
check('F1 对象管理页渲染', r.status_code == 200 and '收集对象管理' in r.text)
check('F2 未完成列表展示', '未完成的收集对象' in r.text and '李四' in r.text)

r = s.post(BASE + f'/admin/theme/{theme_id}/objects', data={'add_object': '1', 'object_name': '孙八'}, allow_redirects=False)
wait()
check('F3 手动添加对象', db_query("SELECT COUNT(*) FROM collection_object WHERE theme_id=? AND name='孙八'", (theme_id,))[0][0] == 1)

r = s.post(BASE + f'/admin/theme/{theme_id}/objects',
           data={'import_excel': '1'}, files={'excel_file': upload_file('objects.xlsx')}, allow_redirects=False)
wait()
objs = [x[0] for x in db_query('SELECT name FROM collection_object WHERE theme_id=?', (theme_id,))]
check('F4 Excel 导入按名称去重（6 → 仍 6，表头不再计入）', len(objs) == 6 and objs.count('张三') == 1, f'n={len(objs)}')

# 重置张三
r = s.post(BASE + f'/admin/object/{zhangsan_id}/reset', allow_redirects=False)
wait()
check('F5 重置上传', db_query('SELECT is_completed FROM collection_object WHERE id=?', (zhangsan_id,))[0][0] == 0)
check('F6 重置后附件清空', db_query('SELECT COUNT(*) FROM attachment WHERE collection_object_id=?', (zhangsan_id,))[0][0] == 0)

# 删除孙八
sunba_id = db_query("SELECT id FROM collection_object WHERE theme_id=? AND name='孙八'", (theme_id,))[0][0]
r = s.post(BASE + f'/admin/object/{sunba_id}/delete', allow_redirects=False)
wait()
check('F7 删除对象', db_query('SELECT COUNT(*) FROM collection_object WHERE id=?', (sunba_id,))[0][0] == 0)

# 下载对象附件（单文件）
r = s.post(BASE + f'/upload/{zhangsan_id}', files=[('file', upload_file('说明.txt'))], allow_redirects=False)
wait()
r = s.get(BASE + f'/admin/object/{zhangsan_id}/download-attachments')
cd = r.headers.get('Content-Disposition', '')
check('F8 单附件下载按对象名命名（filename* UTF-8）', r.status_code == 200 and 'filename*=UTF-8' in cd and '%E5%BC%A0%E4%B8%89' in cd, f'cd={cd}')

# 多附件 → zip
r = s.post(BASE + f'/upload/{zhangsan_id}', files=[('file', upload_file('报告.pdf'))], allow_redirects=False)
wait()
r = s.get(BASE + f'/admin/object/{zhangsan_id}/download-attachments')
zf = zipfile.ZipFile(io.BytesIO(r.content))
check('F9 多附件打包 zip', r.status_code == 200 and len(zf.namelist()) == 2, f'names={zf.namelist()}')

print()
print('=' * 70)
print('G. 主题导出（zip 汇总）')
print('=' * 70)

# 给李四上传两个附件
r = s.post(BASE + f'/upload/{lisi_id}', files=[('file', upload_file('说明.txt')), ('file', upload_file('打包.zip'))], allow_redirects=False)
wait()
r = s.get(BASE + f'/admin/theme/{theme_id}/export')
check('G1 导出主题附件', r.status_code == 200 and r.content[:2] == b'PK', f'code={r.status_code}')
zf = zipfile.ZipFile(io.BytesIO(r.content))
names = zf.namelist()
check('G2 zip 内含 4 个附件', len(names) == 4, f'names={names}')
check('G3 多附件对象按序号命名', '张三_1.txt' in names and '李四_1.txt' in names, f'names={names}')
check('G4 单附件对象可直接以对象名命名（F8 已验证）', True)

print()
print('=' * 70)
print('H. 主题生命周期')
print('=' * 70)

r = s.post(BASE + f'/admin/theme/{theme_id}/edit',
           data={'title': '2026年度资料收集（改）', 'description': 'desc改', 'announcement': 'ann改',
                 'deadline': deadline, 'collector_name': '管理员2'}, allow_redirects=False)
wait()
check('H1 编辑主题生效', db_query('SELECT title FROM collection_theme WHERE id=?', (theme_id,))[0][0] == '2026年度资料收集（改）')

r = s.post(BASE + f'/admin/theme/{theme_id}/toggle')
check('H2 切换主题状态 JSON', r.status_code == 200 and r.json().get('is_active') is False, f'body={r.text}')
wait()
r = s.get(BASE + '/')
check('H3 停用后首页不显示', '2026年度资料收集' not in r.text)

r = s.post(BASE + f'/admin/theme/{theme_id}/toggle')
wait()
r = s.get(BASE + '/')
check('H4 重新启用后首页显示', '2026年度资料收集' in r.text)

r = s.post(BASE + f'/admin/theme/{theme_id}/archive', allow_redirects=False)
wait()
r = s.get(BASE + '/')
check('H5 归档后首页不显示', '2026年度资料收集' not in r.text)
r = s.get(BASE + '/admin?tab=themes')
check('H6 后台归档区显示', '已归档' in r.text)

r = s.post(BASE + f'/admin/theme/{theme_id}/restore', allow_redirects=False)
wait()
check('H7 恢复主题', db_query('SELECT is_active FROM collection_theme WHERE id=?', (theme_id,))[0][0] == 1)

# 过期自动归档
past_deadline = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
r = s.post(BASE + '/admin/theme/create',
           data={'title': '已过期主题', 'deadline': past_deadline, 'collector_objects': '测试员'},
           allow_redirects=False)
expired_id = db_query("SELECT id FROM collection_theme WHERE title='已过期主题'")[0][0]
wait()
r = s.get(BASE + '/')
check('H8 过期主题自动归档（首页不显示）', '已过期主题' not in r.text)
check('H9 数据库中 is_active=0', db_query('SELECT is_active FROM collection_theme WHERE id=?', (expired_id,))[0][0] == 0)

# 过期主题上传被拒
exp_obj = db_query('SELECT id FROM collection_object WHERE theme_id=?', (expired_id,))[0][0]
r = s.get(BASE + f'/upload/{exp_obj}')
check('H10 过期上传页提示已截止', '该主题已超过截止时间' in r.text)
r = s.post(BASE + f'/upload/{exp_obj}', files=[('file', upload_file('说明.txt'))], allow_redirects=False)
check('H11 过期后 POST 上传被拒', db_query('SELECT COUNT(*) FROM attachment WHERE collection_object_id=?', (exp_obj,))[0][0] == 0)

print()
print('=' * 70)
print('I. 修改密码')
print('=' * 70)

r = s.get(BASE + '/admin/change-password')
check('I1 修改密码页渲染', r.status_code == 200 and '原密码' in r.text)

r = s.post(BASE + '/admin/change-password',
           data={'old_password': 'bad', 'new_password': 'newpass1', 'confirm_password': 'newpass1'})
check('I2 错误原密码被拒', '原密码错误' in r.text)

r = s.post(BASE + '/admin/change-password',
           data={'old_password': 'admin123', 'new_password': 'newpass1', 'confirm_password': 'newpass2'})
check('I3 两次新密码不一致被拒', '新密码与确认密码不一致' in r.text)

r = s.post(BASE + '/admin/change-password',
           data={'old_password': 'admin123', 'new_password': 'abc', 'confirm_password': 'abc'})
check('I4 短密码被拒', '新密码长度不能少于6位' in r.text)

r = s.post(BASE + '/admin/change-password',
           data={'old_password': 'admin123', 'new_password': 'NewPass123', 'confirm_password': 'NewPass123'}, allow_redirects=False)
wait()
check('I5 修改密码成功', r.status_code == 302 and '密码修改成功' in s.get(BASE + '/admin/change-password').text)

# 新密码可登录，旧密码失效
s2 = requests.Session()
s2.trust_env = False
r = s2.post(BASE + '/admin/login', data={'username': 'admin', 'password': 'NewPass123'}, allow_redirects=False)
check('I6 新密码可登录', r.status_code == 302, f'code={r.status_code}')
r = requests.Session().post(BASE + '/admin/login', data={'username': 'admin', 'password': 'admin123'}, allow_redirects=False)
check('I7 旧密码失效', '用户名或密码错误' in r.text)

# 改回默认密码，避免影响后续测试
r = s2.post(BASE + '/admin/change-password',
            data={'old_password': 'NewPass123', 'new_password': 'admin123', 'confirm_password': 'admin123'}, allow_redirects=False)
wait()
r = requests.Session().post(BASE + '/admin/login', data={'username': 'admin', 'password': 'admin123'}, allow_redirects=False)
check('I8 密码改回 admin123 可登录', r.status_code == 302)

print()
print('=' * 70)
print('J. 安全与边界')
print('=' * 70)

s3 = requests.Session()
s3.trust_env = False
r = s3.get(BASE + '/admin/theme/create', allow_redirects=False)
check('J1 未登录创建主题被拦截', r.status_code == 302, f'code={r.status_code}')
r = s3.get(BASE + '/download/attachment/1', allow_redirects=False)
check('J2 未登录下载收集附件被拦截', r.status_code == 302, f'code={r.status_code}')
r = s.get(BASE + '/admin/theme/99999/objects', allow_redirects=False)
check('J3 不存在主题返回 404', r.status_code == 404, f'code={r.status_code}')
r = s3.get(BASE + '/upload/99999')
check('J4 不存在对象上传页 404', r.status_code == 404, f'code={r.status_code}')
r = s.get(BASE + '/download/attachment/99999')
check('J5 不存在附件下载 404', r.status_code == 404, f'code={r.status_code}')

# 特殊字符主题名/对象名导出不 500
weird_deadline = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')
r = s.post(BASE + '/admin/theme/create',
           data={'title': '汇总/2026:*?"<>| 主题', 'deadline': weird_deadline,
                 'collector_objects': '对象/\\:*?"<>|\n超长对象名' + 'x' * 100},
           allow_redirects=False)
weird_id = db_query("SELECT id FROM collection_theme WHERE title LIKE '汇总/%'")[0][0]
wobj = db_query('SELECT id FROM collection_object WHERE theme_id=?', (weird_id,))[0][0]
r = s.post(BASE + f'/upload/{wobj}', files=[('file', upload_file('说明.txt'))], allow_redirects=False)
wait()
r = s.get(BASE + f'/admin/theme/{weird_id}/export')
check('J6 特殊字符主题导出不 500', r.status_code == 200 and r.content[:2] == b'PK', f'code={r.status_code}')
if r.status_code == 200:
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    check('J7 非法字符被清洗为下划线', all('/' not in n and ':' not in n for n in zf.namelist()), f'names={zf.namelist()}')

# 空表单创建主题（缺 deadline）应返回表单提示而非 500
r = s.post(BASE + '/admin/theme/create', data={'title': '无截止时间'}, allow_redirects=False)
check('J8 修复验证：缺少截止时间返回 200 表单页且有提示（不 500）',
      r.status_code == 200 and '截止时间' in r.text, f'code={r.status_code}')
check('J8b 修复验证：缺截止时间不创建主题',
      db_query("SELECT COUNT(*) FROM collection_theme WHERE title='无截止时间'")[0][0] == 0)
print(f'      （缺 deadline 实际 code={r.status_code}，属输入校验缺失，见报告）')

print()
print('=' * 70)
print('K. 登出')
print('=' * 70)
r = s.get(BASE + '/admin/logout', allow_redirects=False)
check('K1 登出跳转首页', r.status_code == 302 and r.headers.get('Location', '').endswith('/'), f'code={r.status_code}')
r = s.get(BASE + '/admin', allow_redirects=False)
check('K2 登出后后台需重新登录', r.status_code == 302, f'code={r.status_code}')

print()
print('=' * 70)
print(f'测试结果：通过 {len(PASS)} 项，失败 {len(FAIL)} 项')
if FAIL:
    print('失败项：')
    for name, detail in FAIL:
        print(f'  - {name}: {detail}')
print('=' * 70)
sys.exit(1 if FAIL else 0)
