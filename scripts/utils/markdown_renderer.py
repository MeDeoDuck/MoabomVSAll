"""
Markdown rendering utilities for reports
"""
import re
from typing import List, Tuple


# ── ① 섹션 '한 문장 결론:' 접두어 제거 (렌더 시점) ────────────────
# prompt_manager 는 ① 첫 불릿을 "- 한 문장 결론" 으로 지시하지만, LLM
# 출력은 "- 한 문장 결론: 실제 결론 텍스트" 처럼 콜론·본문이 붙는 경우가
# 잦다. 사용자 결정: 박스 내부에 "한문장 결론:" 라벨이 노출되지 않도록
# 렌더 시점에 결정론적으로 제거(본문은 유지). DB 저장 본문(report_text)·
# 프롬프트·검증 로직은 무변경 — 회귀 영향 0.
# 패턴: 줄 시작 `- ` 뒤의 '한 문장/한문장/한 줄/한줄 결론' + 콜론(반각/전각)
_RE_ONELINER_PREFIX_BULLET = re.compile(
    r"^(\s*-\s*)(?:한\s*문장\s*결론|한\s*줄\s*결론|한줄\s*결론|한문장\s*결론)\s*[:：]\s*",
    re.M,
)


def _strip_oneliner_label(text: str) -> str:
    """① 첫 불릿의 '한 문장 결론:' 라벨 제거(본문 유지). 매칭 없으면 원문."""
    if not text:
        return text
    return _RE_ONELINER_PREFIX_BULLET.sub(r"\1", text)


def markdown_to_html(text: str) -> str:
    """
    Convert markdown text to HTML for web display.
    """
    text = _strip_oneliner_label(text)
    try:
        import markdown
        # Extensions for better rendering
        return markdown.markdown(
            text,
            extensions=['extra', 'nl2br', 'sane_lists']
        )
    except ImportError:
        # Fallback: basic manual conversion
        return _basic_markdown_to_html(text)


def _basic_markdown_to_html(text: str) -> str:
    """Basic markdown to HTML conversion without library."""
    lines = text.split('\n')
    html_lines = []
    in_list = False
    
    for line in lines:
        # Headers
        if line.startswith('###'):
            html_lines.append(f'<h3>{line[3:].strip()}</h3>')
        elif line.startswith('##'):
            html_lines.append(f'<h2>{line[2:].strip()}</h2>')
        elif line.startswith('#'):
            html_lines.append(f'<h1>{line[1:].strip()}</h1>')
        # Lists
        elif line.strip().startswith('- '):
            if not in_list:
                html_lines.append('<ul>')
                in_list = True
            html_lines.append(f'<li>{line.strip()[2:]}</li>')
        else:
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            # Bold and emphasis
            line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            line = re.sub(r'\*(.+?)\*', r'<em>\1</em>', line)
            if line.strip():
                html_lines.append(f'<p>{line}</p>')
            else:
                html_lines.append('<br>')
    
    if in_list:
        html_lines.append('</ul>')
    
    return '\n'.join(html_lines)


def parse_markdown_for_pdf(text: str) -> List[Tuple[str, str]]:
    """
    Parse markdown into structured data for PDF rendering.
    Returns list of (style, content) tuples.
    
    Styles: 'h1', 'h2', 'h3', 'body', 'bullet', 'blank'
    """
    lines = text.split('\n')
    result = []
    
    for line in lines:
        stripped = line.strip()
        
        # Empty line
        if not stripped:
            result.append(('blank', ''))
            continue
        
        # Headers
        if stripped.startswith('###'):
            content = _strip_markdown_formatting(stripped[3:].strip())
            result.append(('h3', content))
        elif stripped.startswith('##'):
            content = _strip_markdown_formatting(stripped[2:].strip())
            result.append(('h2', content))
        elif stripped.startswith('#'):
            content = _strip_markdown_formatting(stripped[1:].strip())
            result.append(('h1', content))
        # Bullet lists
        elif stripped.startswith('- '):
            content = _strip_markdown_formatting(stripped[2:])
            result.append(('bullet', content))
        # Regular paragraph
        else:
            content = _strip_markdown_formatting(stripped)
            result.append(('body', content))
    
    return result


def _strip_markdown_formatting(text: str) -> str:
    """Remove markdown formatting markers for plain text."""
    # Remove bold
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # Remove italic
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # Remove inline code
    text = re.sub(r'`(.+?)`', r'\1', text)
    return text
