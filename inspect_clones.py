from pathlib import Path
roots = [Path('E:/SWE-Pioneers/tmp-upstream/sandraschi-libreoffice-mcp'), Path('E:/SWE-Pioneers/tmp-upstream/patrup-mcp-libre'), Path('E:/SWE-Pioneers/tmp-upstream/waterpistolai-libreoffice-mcp')]
for root in roots:
    print('===', root.name, '===')
    for pattern in ['src', 'plugin', 'extension', 'libreoffice_mcp', 'src/libreoffice_mcp', 'scripts', 'mcpb']:
        p = root / pattern
        if p.exists():
            print('DIR', pattern)
            for child in sorted(p.iterdir())[:25]:
                print('  ', child.name)
            print()
    print()
