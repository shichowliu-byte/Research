# 文件约定与工具使用

## 项目与进度

每个项目以独立目录保存，禁止写入技能安装目录。根目录有 `进度.json`（机器状态）、`成果索引.md`、`待用户操作.md`、`阶段验收.json`（人工语义复核）。阶段目录由contract.json定义：01_方向确立、02_聚焦建库、03.1_高创新、03.2_高可行性、03.3_结构化创新、04_方案深化，05_开题报告仅启用时创建。

原始文件复制到所属阶段的 `原始材料/`，保留文件名，不覆盖。报告/作业用UTF-8 Markdown，文献库/矩阵用XLSX，分批与合并输入用UTF-8 TXT。检索任务、返回材料和方案改动留版本；工具目标文件已存在即拒绝，生成新版本目录或新文件名，避免无声覆盖。首次产物用contract.json中名称，后续修订先归档再更新其规范路径，更新进度并重新验收受影响阶段。

每个验收条目保存 `passed`（布尔）、`evidence`（项目内相对路径列表）、`note`（具体复核发现）。AI必须实际阅读后填写，脚本不自动把存在文件等价为通过。完整报告和矩阵按课程结构写入；禁止使用“TODO/待填写/占位”等空稿通过。个人反思待确认允许作为明确标注的AI草稿，最终方向/课题选定仍须真实用户决定。

## 检索任务单

用Markdown写实际任务单：检索目的、阶段、数据库、检索式版本及完整可复制字符串、时间/类型/语言与排除条件、用户需要的操作、需交回结果数/样本/元数据/全文清单、导出字段和格式、恢复步骤。不得将A/B/C占位符当作最终检索式。

使用request-search登记后脚本将它复制进 `检索任务/编号/任务单.md`。receive-search只在用户已经提供真实返回文件后登记；AI要先核对文件内容与任务要求，脚本只检验文件非空。多任务分别登记，一个返回不能关闭其他等待任务。只有已处理任务的材料能作为统计来源，不能把用户报告的数据库命中数当作导入或去重后文献数。

## 运行环境

用当前宿主提供的依赖加载工具查找Python/Node/Artifact Tool。course.py的数据和状态处理只用Python标准库；XLSX导入只读OOXML，不执行宏和公式，导出通过xlsx_bridge.mjs调用Artifact Tool。默认发现用户缓存中的Codex依赖路径，也可设置 `RESEARCH_COURSE_NODE`、`RESEARCH_COURSE_NODE_MODULES`；不把作者电脑的用户名或盘符写进脚本。不存在该运行时即报告依赖缺失，不静默换工具或安装网络包。

首次写工作簿前遵循宿主表格技能的操作标记要求。纯文本数据用字符串保留，year等已有有效数值保持数值；公式形式数据必须按字面文本保存，不执行。脚本有XLSX行数、单元格长度检查，超长摘要报错交由AI显式处理，不能截掉内容。

命令中 `python` 是已解析的Python绝对路径，`COURSE` 是本技能scripts/course.py绝对路径：

```text
python COURSE init PROJECT --topic "研究方向"
python COURSE status PROJECT
python COURSE request-search PROJECT --stage 1 --task-file 已写好的任务单.md
python COURSE receive-search PROJECT --id S001 --files 用户返回.csv 其他材料.txt --result-count 1234
python COURSE prepare PROJECT --input 聚焦导出.csv --database Scopus --search-version S004 --date 2026-10-18
python COURSE prepare PROJECT --input 补充导出.xlsx --existing 既有总库.xlsx --out 02_聚焦建库/修订_v2 --database WoS --search-version S005 --date 2026-10-19
python COURSE extract PROJECT --library 02_聚焦建库/文献总库.xlsx --ids 04_方案深化/入选ID.txt --out 04_方案深化/入选文献详细信息.txt
python COURSE check PROJECT --stage 3.1
python COURSE complete PROJECT --stage 3.1
python COURSE enable-opening PROJECT
```

prepare要求输入CSV/TSV/XLSX，支持UTF-8 BOM/GB18030。兼容中文与Scopus/WoS常见英文表头，拒绝无title、重复映射列及无标题行。总库首列“编号”，其余固定字段 `author,title,year,journal,abstract,keywords,doi,url,database,search_version,search_date,source_file,source_row,evidence_level`，并保留无法映射的额外列。DOI优先去重；同规范标题+年份在DOI不冲突时合并，有两个不同DOI则保留并报告。重复记录补齐原记录缺失值，冲突保留日志，不以新值覆盖旧值。既有编号不重排；同文献的旧编号别名记录在去重日志，人工检查外部旧引用后再继续。最终ID格式RE加至少3位数字，超过999正常增长。

prepare输出总库.xlsx、编号映射.csv、去重记录.csv、缺失信息.csv、批次清单.json、分批/batch_001.txt等；需要先保留用户原始导出，再执行。默认写02_聚焦建库且拒绝覆盖这些工具产物。补充导入用--existing和新--out，检查后显式替换规范输入并使下游重新核验。

extract严格读取逐个ID（换行、逗号、分号、空格均可），遇不存在ID、重复库ID、非法编号或空列表立即失败，避免输出不完整TXT；输入ID列表里的重复ID按首次顺序去重。提取保留全部字段和稳定编号。

## 完成判定与恢复

check按contract.json检查文件非空、模板占位、矩阵五列、全库ID覆盖、批次报告数量、合并输入内容、最终课题数量与必填标题、人工验收证据是否存在。第4期每批都必须有筛选表（允许零条），合并表和入选ID必须一致；必须至少有一条入选才可完成。第5期逐章文件和文内引用映射由人工复核。

complete要求机器和人工复核都通过、当前期无未返回检索任务；它保存阶段文件和所有上游输入的摘要。status重新对比摘要，发现上游或本期文件变化即显示“需重新验收”，不把旧完成标记当作有效完成。状态文件和索引等管理文件不进入摘要，避免状态更新导致自己失效。检索等待是独立状态；任务返回后恢复已完成文件，不从头重复建库。

如从后期开始，把真实复用材料放在对应规范路径，并在验收备注列来源；未执行的前期仍保持待执行。check可以单独运行后期，不要求伪造前期作业。学术语义、真实用户选择、数量范围偏离原因、引用正确性不能由机器自动判定，必须实际审阅验收。
