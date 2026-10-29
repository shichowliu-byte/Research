"""Local literature preparation; stdlib extraction, Artifact Tool XLSX export."""
import csv
import io
import json
import os
import re
import subprocess
import tempfile
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
FIELDS = ['编号', 'author', 'title', 'year', 'journal', 'abstract', 'keywords', 'doi', 'url', 'database', 'search_version', 'search_date', 'source_file', 'source_row', 'evidence_level']
ALIASES = {
    '编号': ['编号', 'id', '论文编号', '文献编号'],
    'author': ['author', 'authors', '作者', '作者姓名', 'au'],
    'title': ['title', 'document title', 'article title', '标题', '篇名', '题名', 'ti'],
    'year': ['year', 'publication year', '发表年份', '年份', '出版年', '年', 'py'],
    'journal': ['journal', 'source title', 'publication title', 'publication titles', '来源出版物名称', '来源', '期刊', '期刊名称', '出版物名称', 'so'],
    'abstract': ['abstract', '摘要', 'ab'],
    'keywords': ['keywords', 'author keywords', '关键词', '作者关键词', 'de'],
    'doi': ['doi', 'di'], 'url': ['url', 'link', '链接'],
}


def norm(s):
    return ' '.join(unicodedata.normalize('NFKC', str(s)).strip().casefold().split())


def read_table(p):
    p = Path(p)
    if p.suffix.lower() == '.xlsx':
        with zipfile.ZipFile(p) as z:
            strings = []
            if 'xl/sharedStrings.xml' in z.namelist():
                strings = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS)]
            workbook = ET.fromstring(z.read('xl/workbook.xml'))
            sheet = workbook.find('s:sheets/s:sheet', NS)
            rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
            target = next(r.get('Target') for r in rels if r.get('Id') == rid)
            from posixpath import normpath
            name = normpath('xl/' + target) if not target.startswith('/') else target.lstrip('/')
            data = []
            for row in ET.fromstring(z.read(name)).findall('s:sheetData/s:row', NS):
                vals = []
                for c in row.findall('s:c', NS):
                    if c.find('s:f', NS) is not None:
                        raise ValueError('文献数据含公式，请先由用户导出或核实为静态值')
                    letters = re.match('[A-Z]+', c.get('r')).group()
                    col = 0
                    for ch in letters:
                        col = col * 26 + ord(ch) - 64
                    while len(vals) < col:
                        vals.append('')
                    value = c.findtext('s:v', '', NS)
                    if c.get('t') == 's':
                        value = strings[int(value)]
                    elif c.get('t') == 'inlineStr':
                        value = ''.join(c.find('s:is', NS).itertext())
                    vals[col - 1] = value
                if any(str(v).strip() for v in vals):
                    data.append(vals)
    elif p.suffix.lower() in ('.csv', '.tsv'):
        raw = p.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('gb18030')
        data = list(csv.reader(io.StringIO(text), delimiter='\t' if p.suffix.lower() == '.tsv' else ','))
        data = [r for r in data if any(v.strip() for v in r)]
    else:
        raise ValueError('仅支持CSV/TSV/XLSX')
    if not data:
        raise ValueError('文件无表头或数据')
    headers = [str(h).strip() for h in data[0]]
    if len(set(headers)) != len(headers) or any(not h for h in headers):
        raise ValueError('表头重复或为空')
    result = []
    for row in data[1:]:
        if len(row) > len(headers):
            raise ValueError('数据列超出表头')
        result.append(dict(zip(headers, list(row) + [''] * (len(headers) - len(row)))))
    return headers, result


def write_csv(p, headers, rows):
    with Path(p).open('x', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)


def write_xlsx(p, headers, rows):
    if Path(p).exists():
        raise ValueError(f'拒绝覆盖：{p}')
    base = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('RESEARCH_COURSE_NODE', str(base / ('bin/node.exe' if os.name == 'nt' else 'bin/node')))
    if not Path(node).is_file():
        raise ValueError('缺少宿主Node运行时，请设置RESEARCH_COURSE_NODE及RESEARCH_COURSE_NODE_MODULES')
    with tempfile.TemporaryDirectory(prefix='course-xlsx-') as temp:
        spec = Path(temp) / 'table.json'
        spec.write_text(json.dumps({'sheets': [{'name': '文献', 'headers': headers, 'rows': [[r.get(h, '') for h in headers] for r in rows]}]}, ensure_ascii=False), encoding='utf-8')
        res = subprocess.run([node, str(Path(__file__).with_name('xlsx_bridge.mjs')), str(spec), str(p)], capture_output=True, encoding='utf-8')
        if res.returncode:
            raise ValueError('XLSX导出失败：' + res.stderr[-2500:])


def canonical(rows, headers):
    lookup = {norm(a): k for k, values in ALIASES.items() for a in values}
    mapped = [lookup.get(norm(h), h) for h in headers]
    if len(set(mapped)) != len(mapped):
        raise ValueError('多个表头映射同一字段，请合并并说明来源后重试')
    if 'title' not in mapped:
        raise ValueError('缺少title/标题字段')
    out = []
    for line, row in enumerate(rows, 2):
        rec = {new: str(row[old]).strip() for old, new in zip(headers, mapped)}
        if not rec.get('title'):
            raise ValueError(f'第{line}行缺少标题')
        rec['_line'] = line
        out.append(rec)
    return mapped, out


def doi_key(s):
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)', '', norm(s)).strip()


def stable_records(input_file, existing=None, **metadata):
    headers, raw = read_table(input_file)
    mapped, incoming = canonical(raw, headers)
    records, logs = [], []
    all_headers = list(FIELDS)
    old_records = []
    if existing:
        old_h, old_raw = read_table(existing)
        _, old_records = canonical(old_raw, old_h)
        all_headers += [h for h in old_h if h not in all_headers]
    all_headers += [h for h in mapped if h not in all_headers]
    reserved = set()
    for rec in old_records + incoming:
        rid = rec.get('编号', '')
        if rid and not re.fullmatch(r'RE\d{3,}', rid):
            raise ValueError(f'非法旧编号：{rid}')
        if rid:
            reserved.add(rid)
    next_id = max([int(s[2:]) for s in reserved] or [0]) + 1
    by_doi, by_title, by_id = {}, {}, {}
    mapping = []
    for rec, origin in [(r, 'existing') for r in old_records] + [(r, 'input') for r in incoming]:
        line = rec.pop('_line')
        if origin == 'input':
            for k, v in metadata.items():
                if not rec.get(k):
                    rec[k] = v
            if not rec.get('source_file'):
                rec['source_file'] = Path(input_file).name
            if not rec.get('source_row'):
                rec['source_row'] = str(line)
            if not rec.get('evidence_level'):
                rec['evidence_level'] = '摘要' if rec.get('abstract') else '仅元数据'
        key = (norm(rec['title']), norm(rec.get('year', '')))
        dk = doi_key(rec.get('doi', ''))
        candidates = by_title.get(key, [])
        same = by_doi.get(dk) if dk else None
        if same is None:
            same = next((r for r in candidates if not dk or not doi_key(r.get('doi', '')) or doi_key(r.get('doi', '')) == dk), None)
        rid = rec.get('编号', '')
        original_id = rid
        if rid and rid in by_id and by_id[rid] is not same:
            raise ValueError(f'不同文献使用同一编号：{rid}')
        if origin == 'existing' and same is not None:
            raise ValueError('既有总库包含重复文献，先核对引用并清理')
        if same is not None:
            conflicts = []
            for k, v in rec.items():
                if k == '编号':
                    continue
                if not same.get(k) and v:
                    same[k] = v
                elif v and same.get(k) != v:
                    conflicts.append(k)
            if dk:
                by_doi[dk] = same
            logs.append({'source_file': rec.get('source_file', ''), 'source_row': line, 'old_id': rid, 'kept_id': same['编号'], 'reason': 'DOI或标题年份重复', 'conflicting_fields': ';'.join(conflicts)})
            kept = same['编号']
        else:
            if not rid:
                while f'RE{next_id:03d}' in reserved:
                    next_id += 1
                rid = f'RE{next_id:03d}'
                next_id += 1
            rec['编号'] = rid
            records.append(rec)
            by_id[rid] = rec
            by_title.setdefault(key, []).append(rec)
            if dk:
                by_doi[dk] = rec
            if candidates:
                logs.append({'source_file': rec.get('source_file', ''), 'source_row': line, 'old_id': rid, 'kept_id': rid, 'reason': '标题年份相同但DOI冲突，保留待核验', 'conflicting_fields': 'doi'})
            kept = rid
        mapping.append({'origin': origin, 'source_file': rec.get('source_file', ''), 'source_row': line, 'old_id': original_id, '编号': kept})
    return all_headers, records, logs, mapping


def record_text(rec, headers):
    return '\n'.join(f'{h}: {rec.get(h, "")}' for h in headers) + '\n'


def prepare(input_file, out, existing=None, **metadata):
    out = Path(out)
    names = ['文献总库.xlsx', '编号映射.csv', '去重记录.csv', '缺失信息.csv', '批次清单.json', '分批']
    if any((out / n).exists() for n in names):
        raise ValueError('目标含既有产物，请使用新的版本目录，禁止覆盖或混批')
    headers, records, logs, mapping = stable_records(input_file, existing, **metadata)
    if not records:
        raise ValueError('没有可建库记录')
    out.mkdir(parents=True, exist_ok=True)
    write_xlsx(out / '文献总库.xlsx', headers, records)
    write_csv(out / '编号映射.csv', ['origin', 'source_file', 'source_row', 'old_id', '编号'], mapping)
    write_csv(out / '去重记录.csv', ['source_file', 'source_row', 'old_id', 'kept_id', 'reason', 'conflicting_fields'], logs)
    missing = [{'编号': r['编号'], 'missing_fields': ';'.join(k for k in ('abstract', 'year', 'doi', 'keywords') if not r.get(k))} for r in records]
    write_csv(out / '缺失信息.csv', ['编号', 'missing_fields'], [r for r in missing if r['missing_fields']])
    (out / '分批').mkdir()
    batches = []
    for index, start in enumerate(range(0, len(records), 50), 1):
        chunk = records[start:start + 50]
        name = f'分批/batch_{index:03d}.txt'
        (out / name).write_text('\n---\n'.join(record_text(r, headers) for r in chunk), encoding='utf-8')
        batches.append({'batch': f'batch_{index:03d}', 'file': name, 'count': len(chunk), 'ids': [r['编号'] for r in chunk]})
    manifest = {'count': len(records), 'batch_size': 50, 'batches': batches}
    (out / '批次清单.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def parse_ids(text):
    tokens = [s for s in re.split(r'[\s,;，；]+', text.lstrip('\ufeff').strip()) if s]
    if not tokens or any(not re.fullmatch(r'RE\d{3,}', s) for s in tokens):
        raise ValueError('ID列表为空或含非法编号，需RE加至少3位数字')
    return list(dict.fromkeys(tokens))


def extract(library, ids_file, out):
    if Path(out).exists():
        raise ValueError('拒绝覆盖已有提取结果')
    headers, rows = read_table(library)
    ids = parse_ids(Path(ids_file).read_text(encoding='utf-8-sig'))
    by_id = {r['编号']: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError('文献库有重复编号')
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ValueError('不存在的编号：' + ', '.join(missing))
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('\n---\n'.join(record_text(by_id[i], headers) for i in ids), encoding='utf-8')
    return ids
