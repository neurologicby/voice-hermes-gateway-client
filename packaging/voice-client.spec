"""PyInstaller onedir bundle for the Windows VoiceGateway client."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

client_root = Path(SPECPATH).parent
binaries = []
for package in ("onnxruntime", "sherpa_onnx"):
    binaries.extend(collect_dynamic_libs(package))

analysis = Analysis(
    [str(client_root / "voice_client" / "app.py")],
    pathex=[str(client_root)],
    binaries=binaries,
    datas=[],
    hiddenimports=["sherpa_onnx"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="VoiceGatewayClient",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(client_root / "packaging" / "version_info.txt"),
)

bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VoiceGatewayClient",
)
