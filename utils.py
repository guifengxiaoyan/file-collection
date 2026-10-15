import os
import io
import re
import shutil
import uuid
import zipfile
from copy import copy
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from models import db, Admin
from config import Config
import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import (
    OneCellAnchor, TwoCellAnchor, AnchorMarker, XDRPositiveSize2D,
)

EMU_PER_PX = 9525  # 1 像素 = 9525 EMU（96 DPI）

# WPS「单元格内嵌图片」(以 DISPIMG 公式锚定在单元格中的截图) 相关声明。
# openpyxl 无法识别这类图片（ws._images 为空），保存时会把整张图片存储丢弃，
# 因此合并后必须手动把图片字节与声明重新注入结果文件，否则截图会丢失。
WPS_CELLIMAGE_NS = 'http://www.wps.cn/officeDocument/2017/etCustomData'
CELLIMAGE_REL_TYPE = 'http://www.wps.cn/officeDocument/2020/cellImage'
CELLIMAGE_CONTENT_TYPE = 'application/vnd.wps-officedocument.cellimage+xml'

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

EXCEL_EXTS = ('.xls', '.xlsx')

def _copy_cell_style(src, dst):
    """把源单元格的样式（字体/填充/边框/对齐/数字格式/保护）复制到目标单元格。

    不同工作簿之间不能共享样式索引，因此逐项拷贝属性。
    """
    dst.font = copy(src.font)
    dst.fill = copy(src.fill)
    dst.border = copy(src.border)
    dst.alignment = copy(src.alignment)
    dst.number_format = src.number_format
    dst.protection = copy(src.protection)


def _image_ext(src_anchor, w_px, h_px):
    """构造 OneCellAnchor 的 ext（图片显示尺寸，EMU）。

    优先沿用源锚点已记录的 ext（即原文件里的显示尺寸）；
    若源锚点没有有效 ext（或为零），则按图片真实像素尺寸换算
    （1px = 9525 EMU）。返回 XDRPositiveSize2D。
    """
    src_ext = getattr(src_anchor, 'ext', None)
    if src_ext is not None:
        cx = getattr(src_ext, 'cx', 0) or 0
        cy = getattr(src_ext, 'cy', 0) or 0
        if cx > 0 and cy > 0:
            return XDRPositiveSize2D(cx, cy)
    if w_px and h_px and w_px > 0 and h_px > 0:
        return XDRPositiveSize2D(int(round(w_px * EMU_PER_PX)),
                                 int(round(h_px * EMU_PER_PX)))
    return XDRPositiveSize2D(0, 0)


def _add_shifted_image(out_ws, img, out_row, out_col, occupied, delta):
    """把一张图片「保持原有锚定方式」地平移到输出表 (out_row, out_col)。

    关键：整体平移原锚点的行号（delta），并保留锚定类型（随单元格移动 / 随单元格
    移动并调整大小）、`to` 标记（图片右下角）以及 colOff/rowOff 偏移量。这样
    无论原图是「未锁定」(浮动) 还是「嵌入/锚定到单元格」，合并后都不会跳到单元格
    左上角、不会变形、不会因堆叠而重叠/丢失。

    注：这里只是「平移+避让」；合并末尾的 _fit_images_to_cells 会把所有图片
    统一改写为覆盖整个单元格的 TwoCellAnchor（嵌入单元格、随单元格缩放）。

    - 源图片若锚定在 (fr.row, fr.col)，平移后锚定行为 (fr.row+delta, out_col-1)；
      delta = out_row - r（1-based 行相减），保证图片跟随其数据行下沉。
    - 同一单元格出现多张图片（极端情况）时，优先在本行右侧找空位避让
      （图片仍锚在原来的数据行上），本行右侧也满才逐行下移，绝不丢弃。
    返回 True/False。
    """
    try:
        data = img._data()
    except Exception:
        return False
    if not data:
        return False
    try:
        new_img = XLImage(io.BytesIO(data))
    except Exception:
        return False
    if getattr(img, 'width', None):
        new_img.width = img.width
    if getattr(img, 'height', None):
        new_img.height = img.height

    a = img.anchor
    fr = getattr(a, '_from', None)
    if fr is None:
        return False

    # 图片实际像素尺寸（openpyxl 加载时按图片真实尺寸设置）
    w_px = getattr(img, 'width', None)
    h_px = getattr(img, 'height', None)

    new_from = AnchorMarker(
        col=out_col - 1,
        row=fr.row + delta,
        colOff=getattr(fr, 'colOff', 0),
        rowOff=getattr(fr, 'rowOff', 0),
    )
    to = getattr(a, 'to', None)
    if to is not None:
        # 保持原图列跨度一致（to.col - from.col），行同步平移 delta
        col_span = to.col - fr.col
        row_span = to.row - fr.row
        if col_span == 0 and row_span == 0:
            # 退化锚点（from==to，零跨度）：尺寸为 0 会导致图片不可见，
            # 改用 OneCellAnchor + 显式 ext，保证合并后仍然看得见
            new_img.anchor = OneCellAnchor(
                _from=new_from, ext=_image_ext(a, w_px, h_px))
        else:
            new_to = AnchorMarker(
                col=new_from.col + col_span,
                row=to.row + delta,
                colOff=getattr(to, 'colOff', 0),
                rowOff=getattr(to, 'rowOff', 0),
            )
            new_img.anchor = TwoCellAnchor(
                _from=new_from, to=new_to, editAs=getattr(a, 'editAs', None),
            )
    else:
        # OneCellAnchor：必须显式设置 ext（图片显示尺寸），
        # 否则默认 (0,0) 会让图片以零尺寸写入、在 Excel/WPS 中完全不可见
        new_img.anchor = OneCellAnchor(
            _from=new_from, ext=_image_ext(a, w_px, h_px))

    # 冲突避让：同格多图时，优先在本行右侧找空位——图片仍锚在原来的数据行上
    # （不会错到相邻记录行，也不会被后续行的图片连锁挤到数据表之外），
    # 本行右侧也满（最多右移 3 列）才逐行下移，绝不覆盖、绝不丢弃
    key = (new_from.row, new_from.col)
    if key in occupied:
        span_c = None
        if isinstance(new_img.anchor, TwoCellAnchor):
            span_c = new_to.col - new_from.col
        shifted = False
        for dc in range(1, 4):
            if (new_from.row, new_from.col + dc) not in occupied:
                new_from.col += dc
                if span_c is not None:
                    new_to.col = new_from.col + span_c
                shifted = True
                break
        if not shifted:
            while key in occupied:
                new_from.row += 1
                if isinstance(new_img.anchor, TwoCellAnchor):
                    new_to.row += 1
                key = (new_from.row, new_from.col)
    occupied.add((new_from.row, new_from.col))

    try:
        out_ws.add_image(new_img)
    except Exception:
        return False
    return True


def _collect_wps_cell_images(path):
    """读取 WPS「单元格内嵌图片」(xl/cellimages.xml + xl/media)。

    这类截图以 DISPIMG 公式锚定在单元格中（例如 =_xlfn.DISPIMG("ID_xxx",1)），
    openpyxl 无法识别（ws._images 为空），保存时会把整张图片存储丢弃，导致合并后
    截图丢失。这里从原始 zip 中解析出每张图片的字节与 pic 定义，供后续重新注入
    到合并结果中。

    返回 [{ 'name': <图片ID>, 'media': <bytes>, 'ext': '.png', 'pic': <etc:cellImage xml> }, ...]
    （按图片 id 去重）
    """
    try:
        z = zipfile.ZipFile(path)
    except Exception:
        return []
    names = set(z.namelist())
    if 'xl/cellimages.xml' not in names:
        return []

    try:
        cellimages = z.read('xl/cellimages.xml').decode('utf-8')
        rels = z.read('xl/_rels/cellimages.xml.rels').decode('utf-8')
    except Exception:
        return []

    # rId -> 相对 xl/ 的 media 路径
    rid_target = {}
    # \w+ 覆盖 rId1 以及 rIdImg1 这类带字母的 Id（容错）
    for m in re.finditer(r'Id="(rId\w+)"[^>]*?Target="([^"]+)"', rels):
        rid_target[m.group(1)] = m.group(2)

    out = []
    seen = set()
    # 注意 pic 块可能带 xmlns 等属性（<etc:cellImage xmlns:...>），故属性部分用 [^>]* 容忍
    for block in re.findall(r'<etc:cellImage[^>]*>.*?</etc:cellImage>', cellimages, re.S):
        nm = re.search(r'<xdr:cNvPr[^>]*?\bname="([^"]+)"', block)
        blip = re.search(r'<a:blip[^>]*?r:embed="([^"]+)"', block)
        if not nm or not blip:
            continue
        name = nm.group(1)
        if name in seen:
            continue
        target = rid_target.get(blip.group(1))
        if not target:
            continue
        media_path = target if target.startswith('xl/') else 'xl/' + target
        if media_path not in names:
            continue
        media = z.read(media_path)
        ext = os.path.splitext(target)[1].lower() or '.png'
        out.append({'name': name, 'media': media, 'ext': ext, 'pic': block})
        seen.add(name)
    return out


# WPS「单元格内嵌图片」的锚定公式，如 =_xlfn.DISPIMG("ID_1",1)
_DISPIMG_RE = re.compile(r'DISPIMG\(\s*"([^"]+)"')


def _collect_dispimg_cells(path):
    """扫描工作表中所有 DISPIMG 公式单元格，返回 [(row0, col0, image_name), ...]。

    WPS 的「单元格内嵌图片」以 =_xlfn.DISPIMG("ID",1) 公式锚定在单元格中，
    公式文本在 Excel / 网页预览等非 WPS 环境里无法解析（显示为公式或 #NAME?），
    且 openpyxl 保存时会丢失。合并时把这些截图转换为真实图片，需要先知道
    每张截图所在的单元格位置。
    """
    try:
        wb = openpyxl.load_workbook(path)
    except Exception:
        return []
    cells = []
    try:
        ws = wb.active
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith('='):
                    m = _DISPIMG_RE.search(c.value)
                    if m:
                        cells.append((c.row - 1, c.column - 1, m.group(1)))
    finally:
        wb.close()
    return cells


def _inject_wps_cell_images(buf, images):
    """把 WPS 单元格内嵌图片重新写入已保存的 xlsx 字节流。

    openpyxl 保存时会丢弃 xl/cellimages.xml 与 xl/media/，这里在保存后的 zip 中
    重新注入媒体文件、cellimages.xml 及其关系，并补全 [Content_Types].xml 与
    workbook.xml.rels 的声明，使 DISPIMG 公式能继续解析出截图。

    返回新的 BytesIO（若 images 为空则原样返回 buf）。
    """
    if not images:
        return buf

    zin = zipfile.ZipFile(buf)
    entries = {item.filename: zin.read(item.filename) for item in zin.infolist()}
    zin.close()

    # 1) 媒体文件（唯一文件名避免不同文件同名冲突）
    for i, img in enumerate(images, 1):
        entries[f'xl/media/merged_img_{i}{img["ext"]}'] = img['media']

    # 2) cellimages.xml：聚合所有 pic，重写 r:embed 指向新的 rId
    cellimage_parts = []
    rels_items = []
    for i, img in enumerate(images, 1):
        # 关系 Id 用纯数字后缀（rId1, rId2, ...），与 _collect_wps_cell_images
        # 中解析 rels 的 'Id="(rId\d+)"' 正则保持一致，避免 rIdImg1 这类带字母的
        # Id 无法被匹配导致截图收集为空。
        rid = f'rId{i}'
        # 仅替换第一个 r:embed（每个 pic 只有一个 blip）
        pic = re.sub(
            r'(<a:blip[^>]*?r:embed=")[^"]+(")',
            lambda m, r=rid: m.group(1) + r + m.group(2),
            img['pic'], count=1,
        )
        cellimage_parts.append(pic)
        rels_items.append(
            f'<Relationship Id="{rid}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/merged_img_{i}{img["ext"]}"/>'
        )

    cellimages_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<etc:cellImages '
        'xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        f'xmlns:etc="{WPS_CELLIMAGE_NS}">'
        + ''.join(cellimage_parts) +
        '</etc:cellImages>'
    )
    entries['xl/cellimages.xml'] = cellimages_xml.encode('utf-8')
    entries['xl/_rels/cellimages.xml.rels'] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + ''.join(rels_items) +
        '</Relationships>'
    ).encode('utf-8')

    # 3) [Content_Types].xml：确保图片扩展名默认类型 + cellimages override
    ct = entries['[Content_Types].xml'].decode('utf-8')
    exts_used = {img['ext'].lstrip('.') for img in images}
    for ext in exts_used:
        if f'Extension="{ext}"' not in ct:
            ct = ct.replace(
                '</Types>',
                f'<Default Extension="{ext}" ContentType="image/{ext}"/></Types>',
            )
    if '/xl/cellimages.xml' not in ct:
        ct = ct.replace(
            '</Types>',
            '<Override PartName="/xl/cellimages.xml" '
            f'ContentType="{CELLIMAGE_CONTENT_TYPE}"/></Types>',
        )
    entries['[Content_Types].xml'] = ct.encode('utf-8')

    # 4) workbook.xml.rels：增加 cellimages 关系（Id 不冲突）
    wrels_name = 'xl/_rels/workbook.xml.rels'
    wrels = entries[wrels_name].decode('utf-8')
    if 'cellimages.xml' not in wrels:
        ids = set(re.findall(r'Id="([^"]+)"', wrels))
        rid_cell = 'rIdCellImages'
        n = 1
        while rid_cell in ids:
            rid_cell = f'rIdCellImages{n}'
            n += 1
        wrels = wrels.replace(
            '</Relationships>',
            f'<Relationship Id="{rid_cell}" Type="{CELLIMAGE_REL_TYPE}" '
            'Target="cellimages.xml"/></Relationships>',
        )
        entries[wrels_name] = wrels.encode('utf-8')

    # 重新打包
    out = io.BytesIO()
    zout = zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED)
    for name, data in entries.items():
        zout.writestr(name, data)
    zout.close()
    out.seek(0)
    return out


def detect_excel_merge_ready(theme_id):
    """检测某个主题下的全部附件是否满足 Excel 合并条件。

    条件：
    1. 每个收集对象都至少上传了附件；
    2. 主题下所有附件都是 Excel 文件（.xls/.xlsx）；
    3. 所有 Excel 文件都能被读取且列数（格式）一致。

    返回 dict：{ ready, reason, files, columns }
      files 元素为 { object_name, original_name, path, ext, columns, has_images }
    """
    from models import CollectionObject, Attachment

    objects = CollectionObject.query.filter_by(theme_id=theme_id).order_by(CollectionObject.id).all()

    result = {'ready': False, 'reason': '', 'files': [], 'columns': None}

    excel_files = []
    non_excel = []
    for obj in objects:
        for att in obj.attachments:
            ext = os.path.splitext(att.original_name)[1].lower()
            path = os.path.join(get_object_folder(obj.theme_id, obj.id), att.filename)
            if ext not in EXCEL_EXTS:
                non_excel.append(att.original_name)
                continue
            excel_files.append({
                'object_name': obj.name,
                'original_name': att.original_name,
                'path': path,
                'ext': ext,
            })

    if not excel_files:
        result['reason'] = '该主题下还没有任何 Excel 附件，无法合并'
        return result

    if non_excel:
        shown = '、'.join(non_excel[:5])
        more = ' 等' if len(non_excel) > 5 else ''
        result['reason'] = f'存在非 Excel 附件（{shown}{more}），必须全部为 Excel 文件才能合并'
        return result

    # 检查每个 Excel 是否可读 + 列数是否一致（即“文件格式一样”）
    columns = None
    infos = []
    for f in excel_files:
        try:
            wb = openpyxl.load_workbook(f['path'], read_only=True, data_only=True)
            ws = wb.active
            ncols = ws.max_column
            nrows = ws.max_row
            wb.close()
        except Exception as e:
            result['reason'] = f'无法读取 Excel 文件「{f["original_name"]}」（可能是旧版 .xls 格式或文件损坏）：{e}'
            return result
        if ncols <= 0:
            result['reason'] = f'文件「{f["original_name"]}」没有可识别的列，无法合并'
            return result
        if columns is None:
            columns = ncols
        elif ncols != columns:
            result['reason'] = (f'各文件列数（格式）不一致：参考 {columns} 列，'
                                f'但「{f["original_name"]}」为 {ncols} 列，无法合并')
            return result
        infos.append({
            'object_name': f['object_name'],
            'original_name': f['original_name'],
            'path': f['path'],
            'ext': f['ext'],
            'columns': ncols,
            'rows': nrows,
        })

    result['ready'] = True
    result['files'] = infos
    result['columns'] = columns
    return result


def merge_excel_files(files, merge_identical=False, mark_source=False):
    """合并多个结构一致的 Excel 文件为单个工作簿（写入内存并返回 BytesIO）。

    files: list of dict { path, object_name, original_name }
      - 以第一个文件的表头为合并后表头；
      - 逐行（按行合并）把所有文件的数据行堆叠到一起；
      - merge_identical=True 时，跨所有文件删除“数据列完全相同”的重复行（去重合并）；
      - mark_source=True 时，在末尾新增“收集对象”列标注来源；
      - 原文件中的图片（浮动/未锁定、锚定到单元格、随单元格移动并调整大小，以及
        WPS 以 DISPIMG 公式锚定的“单元格内嵌截图”）在合并后会跟随各自的数据行整体
        下沉到正确位置：保留锚定类型/尺寸/偏移，跨列与表头图片不丢、同格多图不重叠。
      - WPS 的 DISPIMG 截图会转换为真实图片（DISPIMG 是 WPS 私有函数，公式文本在
        Excel / 网页预览中无法解析，只会显示 =DISPIMG(...)），转换后与其它图片一样
        嵌入单元格、随单元格缩放、不重叠，全环境可见。

    返回 io.BytesIO（xlsx 字节流），无数据则返回 None。
    """
    if not files:
        return None

    # 预收集所有 WPS 单元格内嵌截图（openpyxl 保存时会丢弃），并记录每个文件中
    # DISPIMG 公式所在的单元格——合并时把这些截图转换为真实图片（公式文本在
    # Excel / 网页预览中无法解析，只会显示 =DISPIMG(...)，用户看不到图）
    media_by_name = {}
    disp_cells_by_file = {}
    for fi, f in enumerate(files):
        for img in _collect_wps_cell_images(f['path']):
            if img['name'] not in media_by_name:
                media_by_name[img['name']] = img
        disp_cells_by_file[fi] = _collect_dispimg_cells(f['path'])

    # 以第一个文件为模板：表头、列数、工作表名
    first_wb = openpyxl.load_workbook(files[0]['path'])
    first_ws = first_wb.active
    src_ncols = first_ws.max_column
    header = [first_ws.cell(row=1, column=c).value for c in range(1, src_ncols + 1)]
    # 记录源表列宽与表头行高：浮动图/“随单元格移动并调整大小”图片的显示尺寸由
    # 行列网格决定。若合并后沿用默认行高列宽，图片会被压缩、缩放失真或漂移到
    # 错误位置（表现为“合并后图片混乱显示”），故必须沿用源表网格。
    col_widths = {}
    for letter, dim in first_ws.column_dimensions.items():
        if dim.width:
            col_widths[letter] = dim.width
    header_height = first_ws.row_dimensions[1].height
    first_wb.close()

    out_wb = openpyxl.Workbook()
    out_ws = out_wb.active
    out_ws.title = first_ws.title or '合并'

    occupied = set()           # 已放置图片的 (行, 列)，避免重合

    def _place_dispimg(out_row, col0, name):
        """把 WPS DISPIMG 截图转换为真实图片放到 (out_row, col0+1)，并清除原公式。

        转换后该截图与其它图片一视同仁：嵌入单元格、随单元格缩放、不重叠，
        且在 Excel / WPS / 网页预览中都能正常显示（公式文本则不能）。
        """
        media = media_by_name.get(name)
        if not media:
            return False
        img = XLImage(io.BytesIO(media['media']))
        img.anchor = OneCellAnchor(
            _from=AnchorMarker(col=col0, row=out_row - 1, colOff=0, rowOff=0))
        if _add_shifted_image(out_ws, img, out_row, col0 + 1, occupied, delta=0):
            # 图片已就位（避让后可能落到相邻单元格），原公式单元格清空
            out_ws.cell(row=out_row, column=col0 + 1).value = None
            return True
        return False

    # 可选的“来源”列
    if mark_source:
        header = header + ['收集对象']

    # 写入表头（含样式）
    for c, val in enumerate(header, 1):
        # 表头中的 DISPIMG 截图公式：清除公式，图片随后转换为真实图片
        if isinstance(val, str) and val.startswith('='):
            m = _DISPIMG_RE.search(val)
            if m and m.group(1) in media_by_name:
                val = None
        cell = out_ws.cell(row=1, column=c, value=val)
        if c <= src_ncols:
            _copy_cell_style(first_ws.cell(row=1, column=c), cell)

    # 表头行（仅首文件）的 DISPIMG 截图 -> 转换为真实图片固定到第 1 行
    for (r0, c0, name) in disp_cells_by_file.get(0, []):
        if r0 == 0:
            _place_dispimg(1, c0, name)

    # 沿用源表列宽与表头行高，保证图片按原网格定位/缩放
    for letter, w in col_widths.items():
        out_ws.column_dimensions[letter].width = w
    if header_height:
        out_ws.row_dimensions[1].height = header_height

    out_row = 2
    last_key = None            # 上一行写入的“数据列”内容，用于去重

    for fi, f in enumerate(files):
        wb = openpyxl.load_workbook(f['path'])
        ws = wb.active

        # 预收集本文件所有图片（按锚点 0-based 行/列），合并时整体平移到目标行
        file_imgs = []
        for img in ws._images:
            fr = getattr(getattr(img, 'anchor', None), '_from', None)
            if fr is not None:
                file_imgs.append((fr.row, fr.col, img))

        placed_ids = set()  # 已放置的图片 id，避免重复/兜底时二次放置

        # 首文件（fi==0）表头行（第 1 行）的图片单独固定到输出表第 1 行；
        # 其余文件的表头不并入合并表，其表头图片跳过，避免落到上一个文件的数据区造成重叠。
        if fi == 0:
            for (ir, ic, img) in file_imgs:
                if ir == 0:
                    if _add_shifted_image(out_ws, img, out_row=1, out_col=ic + 1,
                                          occupied=occupied, delta=0):
                        placed_ids.add(id(img))

        block_start = out_row  # 本文件首条数据在输出表的目标行（1-based）
        nrows = ws.max_row
        for r in range(2, nrows + 1):
            row_vals = [ws.cell(row=r, column=c).value for c in range(1, src_ncols + 1)]
            if all(v is None for v in row_vals):
                continue  # 跳过整行空白

            if mark_source:
                row_vals = row_vals + [f['object_name']]

            data_key = row_vals[:src_ncols]  # 用于比较“相同行”的键（不含来源列）

            if merge_identical and data_key == last_key:
                # 完全相同的行 -> 合并（去重）：不新增输出行，
                # 但把被丢弃行的图片“固定”到保留行对应单元格，避免丢失
                kept_row = out_row - 1
                for (ir, ic, img) in file_imgs:
                    if ir == r - 1 and id(img) not in placed_ids:
                        if _add_shifted_image(out_ws, img, out_row=kept_row,
                                              out_col=ic + 1, occupied=occupied,
                                              delta=kept_row - r):
                            placed_ids.add(id(img))
                # 被丢弃行的 WPS DISPIMG 截图同样固定到保留行
                for (ir, ic, name) in disp_cells_by_file.get(fi, []):
                    if ir == r - 1:
                        _place_dispimg(kept_row, ic, name)
                continue

            # 写入该行数据（含样式）
            for c, val in enumerate(row_vals, 1):
                # DISPIMG 截图公式：清除公式（截图随后转换为真实图片）
                if isinstance(val, str) and val.startswith('='):
                    m = _DISPIMG_RE.search(val)
                    if m and m.group(1) in media_by_name:
                        val = None
                cell = out_ws.cell(row=out_row, column=c, value=val)
                if c <= src_ncols:
                    _copy_cell_style(ws.cell(row=r, column=c), cell)

            # 沿用该行行高（各文件行高可能不同，按贡献行的来源复制），
            # 否则图片会因网格高度变化而被压缩/错位
            src_h = ws.row_dimensions[r].height
            if src_h:
                out_ws.row_dimensions[out_row].height = src_h

            # 把本行图片整体平移到输出表对应行：保留锚定类型/尺寸/偏移，
            # 不丢图、不重叠（delta=out_row-r 使图片严格跟随其数据行下沉）
            for (ir, ic, img) in file_imgs:
                if ir == r - 1 and id(img) not in placed_ids:
                    if _add_shifted_image(out_ws, img, out_row=out_row,
                                          out_col=ic + 1, occupied=occupied,
                                          delta=out_row - r):
                        placed_ids.add(id(img))

            # 本行的 WPS DISPIMG 截图 -> 转换为真实图片（公式已清除）
            for (ir, ic, name) in disp_cells_by_file.get(fi, []):
                if ir == r - 1:
                    _place_dispimg(out_row, ic, name)

            last_key = data_key
            out_row += 1

        # 兜底：锚定在「非数据行」（如空白行上的浮动图）的图片，按整块行偏移平移放入，
        # 否则这类图片因找不到对应数据行会被直接丢弃。
        for (ir, ic, img) in file_imgs:
            if id(img) in placed_ids:
                continue
            if fi > 0 and ir == 0:
                continue  # 非首文件表头图片跳过，避免落入上一文件数据区
            _add_shifted_image(out_ws, img, out_row=ir + block_start - 1,
                               out_col=ic + 1, occupied=occupied,
                               delta=block_start - 2)

        wb.close()

    if out_row == 2:
        return None  # 没有任何数据行

    # 若新增了来源列，给一个合适的列宽
    if mark_source:
        source_col = src_ncols + 1
        out_ws.column_dimensions[get_column_letter(source_col)].width = 18

    # 把每张图片「嵌入单元格」：浮动/未嵌入单元格的图片尺寸由自身决定，超出单元格
    # 就会压盖相邻单元格里的图片（合并后“图片重叠堆叠”）。这里优先扩展行列网格
    # 容纳图片（有上限），仍超限则等比缩小，最终统一改写为覆盖整个单元格的
    # TwoCellAnchor（editAs="twoCell"）——图片填满单元格且随行高列宽一起缩放。
    _fit_images_to_cells(out_ws)

    # 注：WPS DISPIMG 截图已在上面逐行转换为真实图片（嵌入单元格、随单元格缩放），
    # 无需再注入 cellimages.xml；_inject_wps_cell_images 仅用于构造测试用源文件。
    buf = io.BytesIO()
    out_wb.save(buf)
    buf.seek(0)
    return buf


def _fit_images_to_cells(out_ws, max_col_px=425, max_row_px=533):
    """把工作表中所有图片「嵌入单元格」，避免相互重叠且能随单元格缩放。

    背景：收集上来的 Excel 里，图片常常是「浮动/未嵌入单元格」的，其显示尺寸
    由图片自身决定，不受单元格约束。按行合并后，图片尺寸超出所在单元格时就会
    压盖到相邻单元格的图片上，表现为“合并后图片重叠堆叠”。

    做法（对每张图）：
      1. 取图片当前显示尺寸（oneCellAnchor 取 ext；twoCellAnchor 按网格跨度折算）；
      2. 宽度以 max_col_px 为上限、高度以 max_row_px 为上限，超限则等比缩小；
      3. 若行列网格容纳不下，优先扩展该列列宽/该行行高（取两者较大值）；
      4. 统一改写为「覆盖整个单元格」的 TwoCellAnchor（editAs="twoCell"），
         即把图片嵌入单元格：恰好填满所在单元格，调整行高/列宽时图片随之缩放。

    这样每张图都只占自己单元格的范围，任意两张图都不可能重叠；用户在
    Excel/WPS 里拖动行高列宽时，图片会跟着一起变大变小。
    """
    def col_w_px(cidx):
        w = out_ws.column_dimensions[get_column_letter(cidx + 1)].width
        return round((w or 8.43) * 7) + 5

    def row_h_px(ridx):
        h = out_ws.row_dimensions[ridx + 1].height
        return round((h or 15) * 96 / 72)

    # 1) 收集每张图的锚点与像素尺寸
    infos = []
    for img in out_ws._images:
        a = img.anchor
        fr = getattr(a, '_from', None)
        if fr is None:
            continue
        w = h = 0
        ext = getattr(a, 'ext', None)
        if ext is not None and (getattr(ext, 'cx', 0) or 0) > 0:
            w, h = ext.cx // EMU_PER_PX, ext.cy // EMU_PER_PX
        else:
            to = getattr(a, 'to', None)
            if to is not None:
                x1 = col_w_px(to.col) + (to.colOff or 0) // EMU_PER_PX
                y1 = row_h_px(to.row) + (to.rowOff or 0) // EMU_PER_PX
                x0 = col_w_px(fr.col) + (fr.colOff or 0) // EMU_PER_PX
                y0 = row_h_px(fr.row) + (fr.rowOff or 0) // EMU_PER_PX
                w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            w = getattr(img, 'width', None) or 120
            h = getattr(img, 'height', None) or 70
        infos.append((img, fr, w, h))

    if not infos:
        return

    # 2) 计算每张图的收敛后尺寸，以及需要扩展的列宽/行高
    need_col, need_row, fitted = {}, {}, []
    for img, fr, w, h in infos:
        disp_w = min(w, max_col_px)
        scale = disp_w / w if w else 1.0
        disp_h = h * scale
        if disp_h > max_row_px:
            scale = max_row_px / disp_h if disp_h else 1.0
            disp_w *= scale
            disp_h = max_row_px
        # 列宽不够则扩展（不超过上限）
        cw = col_w_px(fr.col)
        if disp_w > cw:
            need_col[fr.col] = max(need_col.get(fr.col, 0), disp_w)
        # 行高不够则扩展（不超过上限）
        rh = row_h_px(fr.row)
        if disp_h > rh:
            need_row[fr.row] = max(need_row.get(fr.row, 0), disp_h)
        fitted.append((img, fr, disp_w, disp_h))

    # 3) 应用扩展后的列宽/行高（与源表已复制的值取较大者）
    for cidx, px in need_col.items():
        letter = get_column_letter(cidx + 1)
        chars = (px - 5) / 7.0
        cur = out_ws.column_dimensions[letter].width
        if not cur or chars > cur:
            out_ws.column_dimensions[letter].width = round(chars, 2)
    for ridx, px in need_row.items():
        pts = px * 72.0 / 96.0
        cur = out_ws.row_dimensions[ridx + 1].height
        if not cur or pts > cur:
            out_ws.row_dimensions[ridx + 1].height = round(pts, 2)

    # 4) 统一改写为「覆盖整个单元格」的 TwoCellAnchor（editAs="twoCell"），
    #    等效于把图片嵌入单元格：图片恰好填满所在单元格，调整行高/列宽时
    #    随单元格一起缩放（“随单元格移动并调整大小”），且不可能溢出压盖
    #    其他单元格里的图片。
    for img, fr, dw, dh in fitted:
        img.anchor = TwoCellAnchor(
            _from=AnchorMarker(col=fr.col, colOff=0, row=fr.row, rowOff=0),
            to=AnchorMarker(col=fr.col + 1, colOff=0,
                            row=fr.row + 1, rowOff=0),
            editAs='twoCell',
        )


def _safe_fs_name(name, max_len=80):
    """把任意字符串变成安全的文件名片段：去除操作系统非法字符、压缩空白、限制长度。

    对象名/主题名由用户填写，可能含 \\ / : * ? " < > | 或过长路径，直接拼进路径会让
    shutil.copy2 / make_archive 抛 OSError 导致导出 500。这里统一清洗。
    """
    if not name:
        return '未命名'
    # 去掉 Windows/Unix 文件名非法字符
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', name)
    # 压缩连续空白并去掉首尾空白
    name = re.sub(r'\s+', '_', name.strip())
    # 去掉首尾的点/下划线（避免以 . 结尾或为空）
    name = name.strip('. ').strip('_')
    if not name:
        name = '未命名'
    # 限制长度，避免超长路径
    if len(name) > max_len:
        name = name[:max_len].rstrip('. ')
    return name or '未命名'


def create_export_archive(theme_id, theme_title):
    """把主题下所有对象的全部附件汇总打包成一个 zip 并返回文件名（位于主题目录内）。

    健壮性要点：
    - 每个附件的目标文件名全局唯一，绝不会因对象同名而互相覆盖 → 保证“全部”导出；
    - 主题名/对象名先过 _safe_fs_name，避免非法字符导致 500；
    - 归档名带 uuid，旧归档先清理，避免 make_archive 抛 FileExistsError；
    - 整段异常保护 + finally 必清临时目录，任何失败返回 None（由路由兜底提示）。
    """
    import glob
    from models import CollectionObject, Attachment
    theme_folder = get_theme_folder(theme_id)
    # 每次使用唯一临时目录，避免并发/重复导出互相干扰
    export_folder = os.path.join(theme_folder, 'export_temp_' + uuid.uuid4().hex)
    try:
        os.makedirs(export_folder)
    except OSError:
        return None

    try:
        objects = CollectionObject.query.filter_by(theme_id=theme_id).all()
        used_names = set()
        for obj in objects:
            safe_obj = _safe_fs_name(obj.name)
            attachments = Attachment.query.filter_by(collection_object_id=obj.id).all()
            for idx, att in enumerate(attachments, 1):
                src = os.path.join(theme_folder, f'object_{obj.id}', att.filename)
                if not os.path.exists(src):
                    continue
                _, ext = os.path.splitext(att.original_name)
                if not ext:
                    _, ext = os.path.splitext(att.filename)
                # 基础名：单附件直接对象名；多附件加序号便于阅读
                base = safe_obj if len(attachments) == 1 else f"{safe_obj}_{idx}"
                dst_name = base + ext
                # 全局去重：同名则追加计数，保证每个附件都有独立条目，互不覆盖
                n = 2
                while dst_name in used_names:
                    dst_name = f"{base}_{n}{ext}"
                    n += 1
                used_names.add(dst_name)
                shutil.copy2(src, os.path.join(export_folder, dst_name))

        # 归档名唯一化：主题名清洗 + 8 位 uuid，杜绝与旧归档冲突
        safe_title = _safe_fs_name(theme_title, max_len=60)
        archive_base = os.path.join(
            theme_folder, f"{safe_title}_附件汇总_{uuid.uuid4().hex[:8]}"
        )
        # 清理同名历史归档，避免反复导出堆积（best-effort）
        for old in glob.glob(os.path.join(theme_folder, f"{safe_title}_附件汇总*.zip")):
            safe_remove_file(old)
        shutil.make_archive(archive_base, 'zip', export_folder)
        return os.path.basename(archive_base) + '.zip'
    except Exception:
        return None
    finally:
        # 无论成功失败都清理临时目录，吞掉沙箱 safe-delete 包装器拦截导致的 OSError
        safe_remove_dir(export_folder)
