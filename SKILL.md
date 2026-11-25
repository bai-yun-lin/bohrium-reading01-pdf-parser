---
name: scientific-pdf-evidence
description: Parse research PDFs into page-addressable text, reading order, section candidates, table cells, equations and figure locations. Use when a research reading task needs faithful extraction, structured fields or answers traceable to the supplied pages, including mixed digital and scanned documents.
---

# 阅读01 · 科研 PDF 解析与证据核对

## 在固定案例中执行

1. 读取 `/input/case.json`、`/input/task.md` 及其中指向的材料。列出本案**实际需要返回的字段、文件、粒度、语言、页段**。区分主文、附件、勘误和重复版本。以实际 PDF 为依据；同名不同版本分别记录。案例要求逐页或全文输出时，逐页检查覆盖；只问一个参数时，先定位其原表、正文及定义。
2. 从安装后的 Skill 目录运行。Python 3.9+，优先使用预装 PyMuPDF；缺失时自动使用 Poppler 的 `pdfinfo`、`pdftotext -bbox-layout` 和 `pdftoppm`。OCR 优先使用 Tesseract 命令行，否则尝试 PyMuPDF 内置 OCR。无须联网。以下命令可直接用于默认输入输出；从系统已有识别语言中按材料选择，例如有中文包时用 `--lang chi_sim+eng`。可重复传入 `--pdf materials/main.pdf` 和附件相对路径。

   ```sh
   cd /skills/weekly-skill
   python scripts/parse_pdf.py --input /input --work /app/work/pdf-parse --output /app/output
   ```

3. 读取 `/app/output/result.json`，再按其 `documents` 指针打开证据 JSON 与可编辑 Markdown。每条原文有稳定的文档、物理页和区块 ID；`bbox` 是左上起算的 PDF 点。PyMuPDF 使用取消显示旋转后的坐标并记录原旋转和印刷页标；Poppler 使用显示方向坐标。对照同一后端的 PNG。表格行、列从 1 开始；合并位置的空槽与材料中的零不同。原始文字、表格候选及页边文字都可单独追溯。
4. **交付前完成真正的科研阅读。** 初筛 JSON 是检查点。按照本案 `output_contract` 的原字段、嵌套层级和类型组织主结果，填写值、单位、范围、条件及证据 ID。按需生成指定文件。若案例要求原文段落，使用 `paragraphs[].element_ids` 回读相应行并复核段界；若要求作者机构、图号、表格、公式，按下节逐项核对。未知字段由 Agent 从本案材料作答，不得用预置通用字段代替。每个附件分别处理，并在题目限定范围内说明未覆盖的页或对象。
5. 将下述结构的审阅记录写入 `/app/work/review.json`。其中 `fields` 使用**本案**的契约字段。每项 `findings` 记录待答事项、判断、理由和证据；`evidence` 可引用区块或整页，表格另在理由中写明行列与表头。按实际情况写 `changes` 记录手工修正前后与依据。`artifacts` 列出要随结果交付的实际文件（相对于输出目录）。执行：

   ```sh
   python scripts/deliver.py /app/work/review.json --output /app/output
   ```

   工具验证证据 ID、声明文件、缺失字段与成功状态。最后按**原始契约**复核嵌套类型、数量和每道问题，不把工具通过视为事实审查。查看最终 `result.json` 及可编辑文件是否能重新打开。预算紧时先落盘主要结果，后续补证；材料受限时按已完成部分提交。

## 读版与纠错决策

| 遇到的材料 | 如何复核和处理 |
|---|---|
| 双栏、三栏与跨栏标题 | 阅读顺序按同一纵向带内的栏读取，跨栏对象切开上下两带。抽查每页首尾、两栏交界、插图上下的相邻两个 ID。横置或非常规排版时，以页面预览为准并记录纠正后的 ID 序列。 |
| 文章结构 | 先从第一页对照标题、作者、单位和上标对应关系，再检查摘要边界、节号和参考文献。书签与元数据用于导航；将最终条目锚定到页面。标题候选可能包含作者行。段落按行距和缩进聚合，保留原始行；跨页续接需检查上一页末句与下一页首句，保存两端 ID。 |
| 页眉、页脚、脚注 | 重复页边文字保存在 `margin_ids`，正文顺序里另行处理。仅出现一次的页边说明应读其字号、位置和正文脚注符；参考文献条目与首页单位不能仅凭小字号删除。区分物理页序与文内页码。 |
| 表格 | 先核对题注及单位，再看表头层级、每列左右界和空白值。格线表保留界框和跨度，并从单元格裁剪范围读文字；稀疏格线可借表头和重复行对齐细分，原始格线识别结果保存在 `vector_grid`。无框表的对齐结果是候选；对齐证据不足时从页面逐行转录。上方或下方的题注均需检查。跨页表匹配表号、续表提示、表头和单位再连接，来源保留物理页；仅同列数不能合并。 |
| 公式、希腊字母与上下标 | 保留原始 span 字体、基线、字号和上标标记。用公式号与局部图核查负号/连接号、乘号、希腊字母、上下标与分式。材料只提供图像时按图标注；无法分辨的符号单独标出，禁止把推测的 LaTeX 当作转录。 |
| 图及题注 | `caption_links` 提供同页相邻候选；多子图、矢量图、拼接图片可能没有单一 image 对象。到对应页核对题注和子图标识，记录组合区域及物理页。按题目要求转录图号、坐标轴、单位、图例；不要把正文中提及的图号当作图题。 |
| 扫描、损坏、加密 | 稀疏文字、整页图片或替换字符多时启动页级 OCR。对照 PNG 校核数值、小数点和姓名。可用 `pdftoppm -f N -l N -r 240 -png FILE PREFIX` 只重渲染困难页，再用现有语言包识别。无可用工具时保留预览、页码、缺失项。受密码保护且没有授权解密材料时记录原因。 |

## 证据状态与示例

`verified` 是原文可定位且与问题对应，`not_reported` 需记录查过的页段或表格，`uncertain` 说明歧义，`conflicting` 并列两个来源，`unreadable` 记录图像、字体或权限问题。单个不可读页可以与其余已核实结果并存。任务要求仅解释未报告事项且已经充分核查时，仍可完成。针对未解决的关键项使用 `partial` 或 `unable_to_determine`。

以下调用生成三页**合成 PDF**，在临时目录运行完整流程，提供最小输入、调用和示意输出，不接触正式案例：

```sh
python scripts/selftest.py /app/work/layout-demo
```

在合成例中，Table 1 的 A 行周期为 1.82 s，B 行为 1.96 s，第一层表头跨两列。对应的最终结果在 `layout-demo/output/result.json`。自测代码展示可直接使用的 `review.json` 字段结构；另有原页 PNG、逐格坐标和可编辑稿件可供校对。复跑、实测数据和版本说明见 [README.md](README.md)。
