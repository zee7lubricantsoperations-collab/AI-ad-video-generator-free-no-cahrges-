$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

pip install -r requirements.txt

pyinstaller --noconfirm --onefile --windowed `
    --name AdVideoMaker `
    --collect-data customtkinter `
    --collect-data imageio_ffmpeg `
    app.py

Write-Host ""
Write-Host "Build complete: $PSScriptRoot\dist\AdVideoMaker.exe"
