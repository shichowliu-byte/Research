"""Observable workflow and data-integrity tests; all fixtures are synthetic."""
import contextlib
import csv
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import course
import library


class CourseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='research-course-tests-')
        cls.base = Path(cls.temp.name)
        # One real end-to-end export with >999 records and an incomplete last batch.
        cls.csv = cls.base / '中文原始导出.csv'
        with cls.csv.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['标题', '作者', '年份', '摘要', 'DOI', '关键词'])
            for i in range(1003):
                w.writerow([f'Synthetic paper {i}', 'Test Author', 2020 + i % 6, '' if i == 0 else f'摘要{i}', f'10.test/{i}', 'testing'])
            w.writerow(['Synthetic paper 0', 'Test Author', 2020, '补充摘要', 'https://doi.org/10.test/0', 'testing'])
        cls.project = cls.base / 'workflow'
        course.init(cls.project, '合成测试主题')
        cls.manifest = library.prepare(cls.csv, course.stage_dir(cls.project, '2'), database='Synthetic', search_version='S001', search_date='2026-10-18')
        cls.library = course.stage_dir(cls.project, '2') / '文献总库.xlsx'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def new_project(self, name):
        p = self.base / name
        course.init(p, 'Synthetic test')
        return p

    def test_01_actual_xlsx_roundtrip_and_batches(self):
        headers, rows = library.read_table(self.library)
        self.assertEqual(headers, library.FIELDS)
        self.assertEqual(len(rows), 1003)
        self.assertEqual(rows[0]['abstract'], '补充摘要')
        self.assertEqual(rows[999]['编号'], 'RE1000')
        manifest, ids = course.load_batches(self.project)
        self.assertEqual([b['count'] for b in manifest['batches']], [50] * 20 + [3])
        self.assertEqual(len(set(ids)), 1003)
        _, logs = library.read_table(course.stage_dir(self.project, '2') / '去重记录.csv')
        self.assertEqual(len(logs), 1)
        with self.assertRaises(ValueError):
            library.prepare(self.csv, course.stage_dir(self.project, '2'))

    def test_02_stable_ids_append_and_doi_conflicts(self):
        p = self.base / 'append.csv'
        p.write_text('title,year,doi\nSynthetic paper 2,2022,10.test/2\nNew paper,2026,10.test/new\nSynthetic paper 2,2022,10.test/other\n', encoding='utf-8')
        _, rows, logs, mapping = library.stable_records(p, self.library, database='test')
        self.assertEqual(len(rows), 1005)
        self.assertEqual(rows[2]['编号'], 'RE003')
        self.assertEqual(rows[1003]['编号'], 'RE1004')
        self.assertEqual(rows[1004]['编号'], 'RE1005')
        self.assertTrue(any('DOI冲突' in r['reason'] for r in logs))
        self.assertEqual(mapping[-3]['编号'], 'RE003')

    def test_03_extraction_missing_ids_is_atomic(self):
        p = self.base / 'ids.txt'
        out = self.base / 'selected.txt'
        p.write_text('RE1000\nRE001\nRE1000', encoding='utf-8')
        self.assertEqual(library.extract(self.library, p, out), ['RE1000', 'RE001'])
        self.assertIn('Synthetic paper 999', out.read_text(encoding='utf-8'))
        p.write_text('RE99999', encoding='utf-8')
        missing_out = self.base / 'must-not-exist.txt'
        with self.assertRaisesRegex(ValueError, '不存在'):
            library.extract(self.library, p, missing_out)
        self.assertFalse(missing_out.exists())
        with self.assertRaises(ValueError): library.parse_ids('')
        with self.assertRaises(ValueError): library.parse_ids('RE01')

    def test_04_wait_receive_and_resume(self):
        root = self.new_project('wait-resume')
        self.assertFalse(course.stage_dir(root, '5').exists())
        errors = course.check(root, '1')
        self.assertTrue(any('缺失' in e for e in errors))
        with self.assertRaises(ValueError): course.complete(root, '1')
        task = self.base / '任务.md'
        task.write_text('# 检索任务\n检索目的：首轮\n数据库：用户Scopus\n完整检索式：TITLE-ABS-KEY(test)\n筛选条件：2020至2026\n返回材料：前20篇标题摘要CSV\n结果数：请返回数据库命中总数\n导出字段：title,abstract,year\n', encoding='utf-8')
        a = course.request_search(root, '1', task)
        b = course.request_search(root, '1', task)
        self.assertEqual(a, 'S001')
        state = course.read_json(root / '进度.json')
        self.assertEqual(course.effective_status(root, state, '1'), '等待检索材料')
        course.receive_search(root, a, [self.csv], 1004)
        state = course.read_json(root / '进度.json')
        self.assertEqual(course.effective_status(root, state, '1'), '等待检索材料')
        course.receive_search(root, b, [self.csv], 1004)
        state = course.read_json(root / '进度.json')
        self.assertEqual(course.effective_status(root, state, '1'), '进行中')
        self.assertTrue((course.stage_dir(root, '1') / '原始材料' / 'S001').exists())
        before = (root / '进度.json').read_bytes()
        with self.assertRaises(ValueError): course.init(root, 'retry')
        self.assertEqual(before, (root / '进度.json').read_bytes())

    def test_05_completed_files_and_upstream_changes(self):
        root = self.new_project('completion')
        d = course.stage_dir(root, '1')
        (d / '原始材料' / 'returned.csv').write_text('title\nSynthetic\n', encoding='utf-8')
        for name in course.CONTRACT['1']['required']:
            p = d / name
            if p.suffix == '.xlsx': shutil.copy2(self.library, p)
            else: p.write_text('Synthetic completed fixture\n## 方向 1\nSynthetic rationale\n', encoding='utf-8')
        review = course.read_json(root / '阶段验收.json')
        for item in review['1']:
            item.update(passed=True, evidence=['01_方向确立/作业提交.md'], note='Synthetic test only; exercises completion mechanics, not scientific review.')
        course.save_json(root / '阶段验收.json', review)
        self.assertEqual(course.check(root, '1'), [])
        course.complete(root, '1')
        state = course.read_json(root / '进度.json')
        self.assertEqual(course.effective_status(root, state, '1'), '已完成')
        (d / '领域态势报告.md').write_text('Changed synthetic data', encoding='utf-8')
        self.assertEqual(course.effective_status(root, state, '1'), '需重新验收')

    def test_06_batch_loss_detected(self):
        original = course.stage_dir(self.project, '2') / '分批/batch_021.txt'
        text = original.read_text(encoding='utf-8')
        original.write_text(text.replace('编号: RE1003', '编号: RE1002'), encoding='utf-8')
        try:
            with self.assertRaisesRegex(ValueError, '编号与清单不同'):
                course.load_batches(self.project)
        finally:
            original.write_text(text, encoding='utf-8')

    def test_07_header_and_id_validation(self):
        for name, text in [('no-title', '作者,年份\nA,2026\n'), ('empty-title', '标题,年份\n,2026\n'), ('alias', '标题,title\nA,B\n'), ('ids', '编号,title\nRE001,A\nRE001,B\n')]:
            p = self.base / (name + '.csv')
            p.write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError): library.stable_records(p)
        p = self.base / 'gbk.csv'
        p.write_bytes('标题,摘要\n中文主题,\n'.encode('gb18030'))
        _, rows, _, _ = library.stable_records(p)
        self.assertEqual(rows[0]['evidence_level'], '仅元数据')

    def test_08_proposal_fields_counts(self):
        p = self.base / 'proposals.md'
        def content(n):
            return '\n'.join(f'## 课题 {i}\n' + ''.join(f'### {label}\nSynthetic content {i}\n' for label in course.FIVE) for i in range(1,n+1))
        p.write_text(content(6), encoding='utf-8')
        self.assertEqual(course.proposal_errors(p, 6, 9, course.FIVE), [])
        p.write_text(content(5), encoding='utf-8')
        self.assertTrue(course.proposal_errors(p, 6, 9, course.FIVE))
        p.write_text(content(6).replace('### 核心科学问题\nSynthetic content 1\n', '### 核心科学问题\n'), encoding='utf-8')
        self.assertTrue(any('核心科学问题' in e for e in course.proposal_errors(p, 6, 9, course.FIVE)))

    def test_09_matrix_and_formula_literal_roundtrip(self):
        p = self.base / 'matrix.xlsx'
        rows = [{k: ('RE001' if k == course.MATRIX[0] else '未报告／待补全文') for k in course.MATRIX}]
        rows[0][course.MATRIX[1]] = '=literal text, not a formula'
        library.write_xlsx(p, course.MATRIX, rows)
        self.assertEqual(course.matrix_ids(p, ['RE001']), ['RE001'])
        with self.assertRaises(ValueError): course.matrix_ids(p, ['RE002'])
        import zipfile
        with zipfile.ZipFile(p) as z:
            xml = z.read('xl/worksheets/sheet1.xml').decode()
            self.assertNotIn('<f>', xml)
            self.assertIn('literal text', xml)

    def test_10_no_completion_without_manual_review(self):
        root = self.new_project('manual')
        errors = course.check(root, '3.2')
        self.assertTrue(any('人工验收未通过' in e for e in errors))
        with self.assertRaises(ValueError): course.complete(root, '3.2')
        self.assertEqual(course.check(root, '5'), ['阶段尚未启用'])
        with self.assertRaises(ValueError): course.within(root, '../outside')


if __name__ == '__main__':
    unittest.main(verbosity=2)
