# Build only on Windows x64. Each executable retains its own runtime hooks.
from pathlib import Path

root = Path(SPECPATH).parent
gui_analysis = Analysis([str(root / 'distribution/gui_entry.py')], pathex=[str(root)],
                        binaries=[], datas=[], hiddenimports=[], hookspath=[],
                        runtime_hooks=[], excludes=[])
tools_analysis = Analysis([str(root / 'distribution/tools_entry.py')], pathex=[str(root)],
                          binaries=[], datas=[], hiddenimports=[], hookspath=[],
                          runtime_hooks=[], excludes=[])
gui_pyz = PYZ(gui_analysis.pure)
tools_pyz = PYZ(tools_analysis.pure)
gui = EXE(gui_pyz, gui_analysis.scripts, [], exclude_binaries=True,
          name='BunsenReplenishment', console=False, upx=False, strip=False)
tools = EXE(tools_pyz, tools_analysis.scripts, [], exclude_binaries=True,
            name='BunsenTools', console=True, upx=False, strip=False)
COLLECT(gui, tools, gui_analysis.binaries, tools_analysis.binaries,
        gui_analysis.datas, tools_analysis.datas, name='BunsenReplenishment',
        upx=False, strip=False)
