"""Offline course workflow. Run --help; never performs literature searches."""
import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from library import FIELDS, extract, parse_ids, prepare, read_table

CONTRACT = json.loads((Path(__file__).resolve().parent.parent / 'references/contract.json').read_text(encoding='utf-8'))
ORDER = list(CONTRACT)
MATRIX = ['论文编号', '关键研究方法与材料', '核心性能指标 (量化)', '创新性与局限性分析', '对我课题的启发点']
FIVE = ['论文标题', '核心科学问题', '研究设计与技术路线', '预期创新性与价值', '立论依据']


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def save_json(p, data):
    p = Path(p)
    temp = p.with_name(p.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(p)


def within(root, rel):
    p = (Path(root) / rel).resolve()
    if not p.is_relative_to(Path(root).resolve()):
        raise ValueError('文件路径必须在项目内：' + str(rel))
    return p


def stage_dir(root, stage):
    return Path(root) / CONTRACT[stage]['directory']


def fingerprint(root, stage):
    files = {}
    for s in ORDER[:ORDER.index(stage) + 1]:
        directory = stage_dir(root, s)
        if directory.exists():
            for p in sorted(directory.rglob('*')):
                if p.is_file():
                    files[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    review = read_json(Path(root) / '阶段验收.json')
    files['__review__'] = hashlib.sha256(json.dumps({s:review[s] for s in ORDER[:ORDER.index(stage)+1]}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    tasks = [t for t in read_json(Path(root) / '进度.json')['searches'] if ORDER.index(t['stage']) <= ORDER.index(stage)]
    files['__searches__'] = hashlib.sha256(json.dumps(tasks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    for task in tasks:
        p = within(root, task['task_file'])
        files[task['task_file']] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else '__missing__'
    return files


def effective_status(root, state, stage):
    entry = state['stages'][stage]
    if entry['status'] == '已完成' and entry.get('snapshot') != fingerprint(root, stage):
        return '需重新验收'
    if any(t['stage'] == stage and t['status'] == '等待检索材料' for t in state['searches']):
        return '等待检索材料'
    return entry['status']


def refresh(root, state):
    state['updated_at'] = now()
    save_json(Path(root) / '进度.json', state)
    lines = ['# 成果索引', '', '仅文件存在不表示作业已通过验收。', '']
    for stage in state['enabled_stages']:
        lines += ['## 第' + stage + '期 ' + effective_status(root, state, stage), '']
        directory = stage_dir(root, stage)
        for p in sorted(directory.rglob('*')):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                lines.append(f'- [{p.name}](<{rel}>)')
        lines.append('')
    (Path(root) / '成果索引.md').write_text('\n'.join(lines), encoding='utf-8')
    pending = ['# 待用户操作', '']
    for task in state['searches']:
        if task['status'] == '等待检索材料':
            pending += [f"- {task['id']} 第{task['stage']}期：请在数据库执行检索并返回任务单所列材料。", f"  [任务单](<{task['task_file']}>)"]
    if len(pending) == 2:
        pending.append('当前没有已登记的外部检索任务；其他用户选择和缺失材料见阶段验收及当前报告。')
    (Path(root) / '待用户操作.md').write_text('\n'.join(pending) + '\n', encoding='utf-8')


def init(root, topic):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / '进度.json').exists() or any(root.iterdir()):
        raise ValueError('初始化要求新建或空目录；已有项目请使用status')
    state = {'version': 1, 'topic': topic, 'created_at': now(), 'enabled_stages': ORDER[:-1], 'searches': [], 'stages': {s:{'status':'待执行'} for s in ORDER}}
    for stage in state['enabled_stages']:
        (stage_dir(root, stage) / '原始材料').mkdir(parents=True)
    save_json(root / '阶段验收.json', {s:[{'criterion':c, 'passed':False, 'evidence':[], 'note':''} for c in spec['review']] for s,spec in CONTRACT.items()})
    refresh(root, state)
    return state


def request_search(root, stage, task_file):
    root = Path(root).resolve()
    state = read_json(root / '进度.json')
    if stage not in state['enabled_stages']:
        raise ValueError('该阶段尚未启用')
    text = Path(task_file).read_text(encoding='utf-8-sig')
    for label in ('检索目的', '数据库', '完整检索式', '筛选条件', '返回材料', '结果数', '导出字段'):
        if label not in text:
            raise ValueError('任务单缺少：' + label)
    if re.search(r'TODO|待填写|占位符|\[此处', text, re.I):
        raise ValueError('任务单仍有占位内容')
    tid = f"S{len(state['searches']) + 1:03d}"
    directory = root / '检索任务' / tid
    directory.mkdir(parents=True)
    target = directory / '任务单.md'
    target.write_text(text, encoding='utf-8')
    state['searches'].append({'id':tid, 'stage':stage, 'status':'等待检索材料', 'task_file':target.relative_to(root).as_posix(), 'created_at':now()})
    state['stages'][stage]['status'] = '进行中'
    refresh(root, state)
    return tid


def receive_search(root, tid, files, result_count):
    root = Path(root).resolve()
    state = read_json(root / '进度.json')
    task = next((t for t in state['searches'] if t['id'] == tid), None)
    if task is None or task['status'] != '等待检索材料':
        raise ValueError('没有该等待任务，或任务已返回')
    if result_count < 0:
        raise ValueError('结果数不能为负')
    sources = [Path(f).resolve() for f in files]
    if not sources or any(not p.is_file() or p.stat().st_size == 0 for p in sources):
        raise ValueError('返回材料缺失或为空')
    directory = stage_dir(root, task['stage']) / '原始材料' / tid
    directory.mkdir(parents=True)
    returned = []
    for index, p in enumerate(sources, 1):
        dst = directory / f'{index:02d}_{p.name}'
        shutil.copy2(p, dst)
        returned.append(dst.relative_to(root).as_posix())
    task.update(status='材料已返回', returned_files=returned, result_count=result_count, returned_at=now())
    refresh(root, state)
    return returned


def proposal_errors(p, lower, upper, fields):
    text = p.read_text(encoding='utf-8-sig')
    parts = re.split(r'^## 课题\s+\d+[^\n]*\n', text, flags=re.M)[1:]
    errors = []
    if not lower <= len(parts) <= upper:
        errors.append(f'{p.name}课题数{len(parts)}，要求{lower}至{upper}')
    for i, part in enumerate(parts, 1):
        for label in fields:
            section = re.search(r'^###[ \t]+' + re.escape(label) + r'[ \t]*\n(.*?)(?=^### |\Z)', part, flags=re.M | re.S)
            if section is None or not section.group(1).strip():
                errors.append(f'{p.name}第{i}题缺少非空部分：{label}')
    return errors


def load_batches(root):
    directory = stage_dir(root, '2')
    manifest = read_json(directory / '批次清单.json')
    headers, rows = read_table(directory / '文献总库.xlsx')
    ids = [r['编号'] for r in rows]
    if headers != FIELDS + [h for h in headers if h not in FIELDS] or len(set(ids)) != len(ids):
        raise ValueError('总库字段顺序不符合规范或有重复编号')
    if any(not re.fullmatch(r'RE\d{3,}', rid) for rid in ids):
        raise ValueError('总库存在非法编号')
    if manifest['count'] != len(rows) or not ids or manifest.get('batch_size') != 50:
        raise ValueError('总库数量或批次大小与清单不一致')
    seen = []
    for i, b in enumerate(manifest['batches']):
        expected_size = 50 if i < len(manifest['batches']) - 1 else len(b['ids'])
        if b['count'] != len(b['ids']) or b['count'] != expected_size or not 1 <= b['count'] <= 50:
            raise ValueError('批次大小或计数错误')
        text = within(directory, b['file']).read_text(encoding='utf-8-sig')
        actual = re.findall(r'^编号: (RE\d{3,})\s*$', text, re.M)
        if actual != b['ids']:
            raise ValueError('批次正文编号与清单不同')
        seen += b['ids']
    if seen != ids:
        raise ValueError('批次清单有遗漏、重复或顺序不符')
    return manifest, ids


def matrix_ids(p, expected=None):
    headers, rows = read_table(p)
    if headers != MATRIX:
        raise ValueError(f'{p.name}矩阵必须为课程五列且顺序一致')
    ids = [r[MATRIX[0]] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError(f'{p.name}重复文献编号')
    if expected is not None and set(ids) != set(expected):
        raise ValueError(f'{p.name}矩阵编号未完整覆盖该批次')
    for row in rows:
        if any(not str(row[h]).strip() for h in MATRIX):
            raise ValueError(f'{p.name}矩阵含空字段，缺失内容应明确写未报告或待补全文')
    return ids


def check(root, stage, manual=True):
    root = Path(root).resolve()
    state = read_json(root / '进度.json')
    if stage not in state['enabled_stages']:
        return ['阶段尚未启用']
    directory = stage_dir(root, stage)
    errors = []
    for name in CONTRACT[stage]['required']:
        p = directory / name
        if not p.is_file() or not p.stat().st_size:
            errors.append('缺失或空文件：' + name)
        elif p.suffix == '.xlsx':
            try:
                read_table(p)
            except Exception as exc:
                errors.append(f'无法读取Excel {name}: {exc}')
    for p in directory.rglob('*.md'):
        if '原始材料' not in p.parts and re.search(r'TODO|待填写|\[此处[^\]]*\]|占位符', p.read_text(encoding='utf-8-sig'), re.I):
            errors.append('仍含占位内容：' + p.name)
    if any(t['stage'] == stage and t['status'] == '等待检索材料' for t in state['searches']):
        errors.append('当前期存在等待检索材料任务')
    try:
        if stage in ('1', '2') and not any(p.is_file() for p in (directory / '原始材料').rglob('*')):
            errors.append('缺少用户原始检索材料')
        if stage == '1' and (directory / '作业提交.md').exists():
            n = len(re.findall(r'^## 方向\s+\d+', (directory / '作业提交.md').read_text(encoding='utf-8-sig'), re.M))
            if n not in (1, 2):
                errors.append('作业需以## 方向 N列出1至2个最终方向')
        if stage in ('2', '3.1', '3.2', '3.3', '4'):
            batches, all_ids = load_batches(root)
        if stage in ('3.1', '3.2', '3.3'):
            base = stage_dir(root, '3.1')
            reports = []
            for b in batches['batches']:
                matrix_ids(base / '批次分析' / (b['batch'] + '_矩阵.xlsx'), b['ids'])
                report = base / '批次分析' / (b['batch'] + '_综合报告.md')
                errors.extend(proposal_errors(report, 3, 5, FIVE))
                reports.append(report.read_text(encoding='utf-8-sig').strip())
            merged = (base / '合并综合报告.txt').read_text(encoding='utf-8-sig')
            if any(r not in merged for r in reports):
                errors.append('合并输入未完整保留每批综合报告')
            if stage == '3.1':
                errors.extend(proposal_errors(directory / '高创新课题.md', 6, 9, FIVE))
            elif stage == '3.2':
                errors.extend(proposal_errors(directory / '高可行性课题.md', 6, 9, FIVE + ['难度等级']))
                text = (directory / '高可行性课题.md').read_text(encoding='utf-8-sig')
                for part in re.split(r'^## 课题\s+\d+[^\n]*\n', text, flags=re.M)[1:]:
                    if not re.search(r'初阶[/／]验证性', part):
                        errors.append('高可行性课题未明确初阶/验证性难度')
            else:
                errors.extend(proposal_errors(directory / '结构化创新课题.md', 4, 6, ['启发路径', '论文标题', '核心思考过程', '研究设计与技术路线', '预期创新性与价值', '立论依据']))
        if stage == '4':
            selected = []
            for b in batches['batches']:
                ids = matrix_ids(directory / '逐批筛选' / (b['batch'] + '_筛选.xlsx'))
                if not set(ids).issubset(b['ids']):
                    errors.append('筛选矩阵包含其他批次或不存在的ID')
                selected += ids
            merged_ids = matrix_ids(directory / '强相关文献合并.xlsx')
            ids = parse_ids((directory / '入选ID.txt').read_text(encoding='utf-8-sig'))
            txt_ids = re.findall(r'^编号: (RE\d{3,})\s*$', (directory / '入选文献详细信息.txt').read_text(encoding='utf-8-sig'), re.M)
            if set(selected) != set(merged_ids) or set(ids) != set(selected) or ids != txt_ids:
                errors.append('逐批筛选、合并表、ID列表、详细TXT不一致')
            if not set(ids).issubset(all_ids):
                errors.append('入选编号不在总库')
            from library import record_text
            headers, rows = read_table(stage_dir(root, '2') / '文献总库.xlsx')
            source_by_id = {r['编号']:r for r in rows}
            expected_text = '\n---\n'.join(record_text(source_by_id[rid], headers) for rid in ids)
            if (directory / '入选文献详细信息.txt').read_text(encoding='utf-8-sig') != expected_text:
                errors.append('详细TXT内容与原始总库不一致')
        if stage == '5':
            chapters = read_json(directory / '章节清单.json')
            if not isinstance(chapters, list) or not chapters:
                errors.append('章节清单需非空相对路径数组')
            else:
                for name in chapters:
                    p = within(directory, name)
                    if not p.is_file() or not p.stat().st_size:
                        errors.append('分章文件缺失：' + name)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        errors.append(str(exc))
    if manual:
        reviews = read_json(root / '阶段验收.json').get(stage, [])
        if len(reviews) != len(CONTRACT[stage]['review']):
            errors.append('人工验收项目数量不符')
        for i, criterion in enumerate(CONTRACT[stage]['review']):
            item = reviews[i] if i < len(reviews) else {}
            if item.get('criterion') != criterion or item.get('passed') is not True or not item.get('note', '').strip() or not item.get('evidence'):
                errors.append('人工验收未通过：' + criterion)
                continue
            for name in item['evidence']:
                try:
                    p = within(root, name)
                    if not p.is_file() or not p.stat().st_size:
                        errors.append('验收证据缺失：' + name)
                except ValueError as exc:
                    errors.append(str(exc))
    return errors


def complete(root, stage):
    errors = check(root, stage)
    if errors:
        raise ValueError('\n'.join(errors))
    root = Path(root).resolve()
    state = read_json(root / '进度.json')
    state['stages'][stage] = {'status':'已完成', 'completed_at':now(), 'snapshot':fingerprint(root, stage)}
    refresh(root, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'status', 'request-search', 'receive-search', 'prepare', 'extract', 'check', 'complete', 'enable-opening'):
        p = commands.add_parser(name)
        p.add_argument('project', type=Path)
        if name == 'init': p.add_argument('--topic', required=True)
        if name in ('request-search', 'check', 'complete'): p.add_argument('--stage', choices=ORDER, required=True)
        if name == 'request-search': p.add_argument('--task-file', type=Path, required=True)
        if name == 'receive-search':
            p.add_argument('--id', required=True)
            p.add_argument('--files', nargs='+', required=True)
            p.add_argument('--result-count', type=int, required=True)
        if name == 'prepare':
            p.add_argument('--input', type=Path, required=True)
            p.add_argument('--existing', type=Path)
            p.add_argument('--out', default=CONTRACT['2']['directory'])
            p.add_argument('--database', required=True)
            p.add_argument('--search-version', required=True)
            p.add_argument('--date', required=True)
        if name == 'extract':
            p.add_argument('--library', required=True)
            p.add_argument('--ids', required=True)
            p.add_argument('--out', required=True)
    args = parser.parse_args()
    root = args.project.resolve()
    try:
        if args.command == 'init':
            init(root, args.topic)
            result = {'project': str(root), 'status':'已初始化'}
        elif args.command == 'status':
            state = read_json(root / '进度.json')
            result = {'topic':state['topic'], 'stages':{s:effective_status(root, state, s) for s in state['enabled_stages']}, 'pending_searches':[t['id'] for t in state['searches'] if t['status'] == '等待检索材料']}
        elif args.command == 'request-search': result = {'id':request_search(root, args.stage, args.task_file)}
        elif args.command == 'receive-search': result = {'files':receive_search(root, args.id, args.files, args.result_count)}
        elif args.command == 'prepare':
            result = prepare(args.input, within(root, args.out), args.existing, database=args.database, search_version=args.search_version, search_date=args.date)
            state = read_json(root / '进度.json')
            state['stages']['2']['status'] = '进行中'
            refresh(root, state)
        elif args.command == 'extract':
            result = {'ids':extract(within(root, args.library), within(root, args.ids), within(root, args.out))}
            refresh(root, read_json(root / '进度.json'))
        elif args.command == 'check':
            result = {'stage':args.stage, 'errors':check(root, args.stage)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result['errors'] else 0
        elif args.command == 'complete':
            complete(root, args.stage)
            result = {'stage':args.stage, 'status':'已完成'}
        else:
            state = read_json(root / '进度.json')
            if '5' not in state['enabled_stages']:
                state['enabled_stages'].append('5')
                (stage_dir(root, '5') / '原始材料').mkdir(parents=True, exist_ok=True)
            refresh(root, state)
            result = {'stage':'5', 'status':'已启用'}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'error':str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
