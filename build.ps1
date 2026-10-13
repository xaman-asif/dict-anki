# Build a standalone Windows executable: dist\dict-anki.exe
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }

# Render the app's ♾ icon to a multi-size icon.ico (same drawing as the in-app
# window icon). Uses the native platform plugin: 'offscreen' has no fonts, so
# the ♾ glyph would come out as an empty box.
& $py -c "from PyQt6.QtWidgets import QApplication; import app; a = QApplication([]); app._save_icon_ico('icon.ico')"
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

& $py -m PyInstaller `
    --noconfirm `
    --clean `
    --name dict-anki `
    --windowed `
    --onefile `
    --icon icon.ico `
    app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "Built: $PSScriptRoot\dist\dict-anki.exe"
