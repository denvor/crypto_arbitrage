#!/usr/bin/env python3
"""
将 RESEARCH_PAPER.md 编译为 PDF，LaTeX 公式使用 MathML 渲染（WeasyPrint）。

用法:
    python scripts/build_pdf.py              # 输出到 RESEARCH_PAPER.pdf
    python scripts/build_pdf.py -o out.pdf   # 输出到指定路径
"""

import re
import sys
import argparse
from pathlib import Path

import mistune
from latex2mathml.converter import convert as latex_to_mathml
from weasyprint import HTML

PROJECT_DIR = Path(__file__).resolve().parent.parent
MARKDOWN_PATH = PROJECT_DIR / "RESEARCH_PAPER.md"
DEFAULT_OUTPUT = PROJECT_DIR / "RESEARCH_PAPER.pdf"

# LaTeX 转换缓存
_LATEX_CACHE = {}


def latex_to_mathml_cached(formula: str, display: bool = False) -> str:
    """将 LaTeX 公式转换为 MathML，带缓存。"""
    key = (formula, display)
    if key not in _LATEX_CACHE:
        try:
            mathml = latex_to_mathml(formula)
        except Exception:
            # 转换失败时 fallback 为纯文本
            mathml = f'<math xmlns="http://www.w3.org/1998/Math/MathML"><mrow><mi>{formula}</mi></mrow></math>'
        if display:
            # 将 inline 改为 block display
            mathml = mathml.replace('display="inline"', 'display="block"', 1)
        _LATEX_CACHE[key] = mathml
    return _LATEX_CACHE[key]


def convert_math_in_markdown(text: str) -> str:
    """将 Markdown 中的 LaTeX 公式替换为 MathML 占位符，返回 (新文本, 占位符列表)。"""
    placeholders = []

    def replace_display(match):
        formula = match.group(1).strip()
        mathml = latex_to_mathml_cached(formula, display=True)
        ph = f"%%MATH_D_{len(placeholders)}%%"
        placeholders.append((ph, mathml))
        return ph

    def replace_inline(match):
        formula = match.group(1).strip()
        mathml = latex_to_mathml_cached(formula, display=False)
        ph = f"%%MATH_I_{len(placeholders)}%%"
        placeholders.append((ph, mathml))
        return ph

    # 必须先处理 $$...$$（display math），再处理 $...$（inline math）
    # 避免 $$ 被 $ 匹配到
    text = re.sub(r"\$\$(.+?)\$\$", replace_display, text, flags=re.DOTALL)
    text = re.sub(r"\$(.+?)\$", replace_inline, text)

    return text, placeholders


def restore_mathml(html: str, placeholders: list) -> str:
    """将 HTML 中的占位符替换为 MathML。"""
    for ph, mathml in placeholders:
        html = html.replace(ph, mathml)
    return html


def markdown_to_html(md_text: str) -> str:
    """将 Markdown 中公式替换后转为 HTML，再还原 MathML。"""
    text, placeholders = convert_math_in_markdown(md_text)
    md_html = mistune.html(text)
    html = restore_mathml(md_html, placeholders)
    return html


def wrap_html(body: str) -> str:
    """用完整 HTML 模板包裹，适配 PDF 打印。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
  @page {{
    size: A4;
    margin: 2.5cm 2cm 2.5cm 2cm;
    @bottom-center {{
      content: counter(page);
      font-size: 10pt;
      color: #666;
    }}
  }}
  body {{
    font-family: "Noto Sans CJK SC", "Noto Sans SC", "Noto Sans", sans-serif;
    font-size: 11pt;
    line-height: 1.7;
    color: #1a1a1a;
  }}
  h1 {{
    font-size: 18pt;
    font-weight: 700;
    margin-top: 2em;
    margin-bottom: 0.6em;
    padding-bottom: 0.3em;
    border-bottom: 2px solid #333;
  }}
  h2 {{
    font-size: 14pt;
    font-weight: 700;
    margin-top: 1.6em;
    margin-bottom: 0.5em;
  }}
  h3 {{
    font-size: 12pt;
    font-weight: 600;
    margin-top: 1.2em;
    margin-bottom: 0.4em;
  }}
  p {{ margin: 0.4em 0; }}
  strong {{ font-weight: 700; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 9.5pt;
  }}
  th {{
    background: #f0f0f0;
    padding: 6px 8px;
    text-align: center;
    border: 1px solid #ccc;
    font-weight: 700;
  }}
  td {{
    padding: 5px 8px;
    border: 1px solid #ccc;
    text-align: center;
  }}
  blockquote {{
    margin: 0.8em 0;
    padding: 0.5em 1em;
    border-left: 4px solid #999;
    color: #555;
    background: #f9f9f9;
  }}
  code {{
    font-family: "JetBrains Mono", "Fira Code", monospace;
    font-size: 9pt;
    background: #f0f0f0;
    padding: 1px 4px;
    border-radius: 3px;
  }}
  pre {{
    background: #f5f5f5;
    padding: 0.8em 1em;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 9pt;
    line-height: 1.5;
  }}
  ul, ol {{ margin: 0.4em 0; padding-left: 1.5em; }}
  li {{ margin: 0.2em 0; }}
  /* 标题块（摘要上方） */
  .title-block {{
    text-align: center;
    margin-bottom: 2em;
    page-break-after: avoid;
  }}
  .title-block h1 {{
    font-size: 20pt;
    border-bottom: none;
    margin-top: 0.5em;
  }}
  .title-block .subtitle {{
    font-size: 12pt;
    color: #555;
    margin-top: 0.3em;
  }}
  .title-block .meta {{
    font-size: 10pt;
    color: #777;
    margin-top: 1.5em;
  }}
  hr {{
    border: none;
    border-top: 1px solid #ccc;
    margin: 1.5em 0;
  }}
  /* 数学公式 */
  math {{ font-size: 1em; }}
  math[display="block"] {{
    display: block;
    text-align: center;
    margin: 0.6em 0;
  }}
  /* 页脚免责声明 */
  .disclaimer {{
    font-size: 9pt;
    color: #888;
    text-align: center;
    margin-top: 3em;
    padding-top: 1em;
    border-top: 1px solid #ccc;
  }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def build_pdf(md_path: Path, output_path: Path, args: argparse.Namespace = None):
    """读取 Markdown，编译为 PDF。"""
    print(f"📖 读取: {md_path}")
    md_text = md_path.read_text(encoding="utf-8")

    print("🔄 转换 Markdown → HTML（含 MathML 公式）...")
    body_html = markdown_to_html(md_text)
    full_html = wrap_html(body_html)

    # 仅在 --html-only 模式写调试 HTML
    if args and getattr(args, "html_only", False):
        html_path = output_path.with_suffix(".html")
        html_path.write_text(full_html, encoding="utf-8")
        print(f"  └ 调试 HTML: {html_path}")

    print(f"🎯 生成 PDF: {output_path}")
    HTML(string=full_html).write_pdf(str(output_path))
    print("✅ 完成!")


def main():
    parser = argparse.ArgumentParser(description="编译 RESEARCH_PAPER.md 为 PDF")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="输出 PDF 路径（默认: PROJECT_DIR/RESEARCH_PAPER.pdf）")
    parser.add_argument("--html-only", action="store_true",
                        help="仅输出 HTML，不生成 PDF")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else DEFAULT_OUTPUT
    md_path = MARKDOWN_PATH

    if not md_path.exists():
        print(f"❌ 未找到: {md_path}", file=sys.stderr)
        sys.exit(1)

    build_pdf(md_path, output_path, args)

    if args.html_only:
        print(f"📄 HTML: {output_path.with_suffix('.html')}")


if __name__ == "__main__":
    main()
