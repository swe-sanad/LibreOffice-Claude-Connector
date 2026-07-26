from pathlib import Path

root = Path('E:/SWE-Pioneers/tmp-upstream')
for name in ['sandraschi-libreoffice-mcp','patrup-mcp-libre','waterpistolai-libreoffice-mcp']:
    repo = root/name
    print('===', name, '===')
    for rel in ['README.md','docs/TOOLS.md','docs/FEATURES.md','docs/COMPARISON-OTHER-LO-MCP.md']:
        p = repo/rel
        if p.exists():
            text = p.read_text(encoding='utf-8', errors='ignore')
            print(f'-- {rel} --')
            for line in text.splitlines():
                if 'tool' in line.lower() or 'tools' in line.lower():
                    if '`' in line or '|' in line:
                        print(line.strip())
            print()
    print()
