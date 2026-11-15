param([switch]$NoLaunch, [switch]$NoShortcuts, [string]$InstallDirectory)
$ErrorActionPreference = 'Stop'
$destination = if ($InstallDirectory) { [IO.Path]::GetFullPath($InstallDirectory) } else { Join-Path $env:LOCALAPPDATA 'Programs\Local Arenti Baby Cam' }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install Python 3.13 (64-bit) from https://www.python.org/downloads/windows/ with its Python launcher, then run setup again.' }
& py -3.13 -c "import sys,struct; assert sys.version_info[:2]==(3,13) and struct.calcsize('P')==8"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 64-bit is required. Install it from python.org.' }
if ((Test-Path $destination) -and -not (Test-Path (Join-Path $destination '.local-arenti-install'))) { throw 'Destination belongs to another installation; refusing to replace it.' }
$running = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains((Join-Path $destination 'desktop_app.py')) })
if ($running.Count) { throw 'Exit Local Arenti Baby Cam from its tray before updating. Phone viewing will stop when you exit.' }
New-Item -ItemType Directory -Force -Path $destination | Out-Null
Set-Content -LiteralPath (Join-Path $destination '.local-arenti-install') -Value 'local-arenti-baby-cam v0.1.0-beta'
$files = @('desktop_app.py','desktop_activation.py','desktop_autostart.py','desktop_camera.py','desktop_capture.py','desktop_input.py','desktop_overlay.py','desktop_pip.py','desktop_profiles.py','desktop_recovery.py','desktop_store.py','protected_store.py','onvif_local.py','ptz_control.py','home_gateway.py','browser_stream.py','owlet_overlay.py','owlet_windows.py','lan_tools.py','camera_setup.py','requirements.txt','LICENSE','THIRD-PARTY-NOTICES.md','README.md','PRIVACY.md')
foreach ($file in $files) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination $destination -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $destination 'gateway') | Out-Null
Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'gateway') -Filter '*.py' | Copy-Item -Destination (Join-Path $destination 'gateway') -Force
$python = Join-Path $destination 'runtime\Scripts\python.exe'
if (-not (Test-Path $python)) {
    & py -3.13 -m venv (Join-Path $destination 'runtime')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Python runtime.' }
}
& $python -m pip install --isolated --only-binary=:all: --index-url https://pypi.org/simple -r (Join-Path $destination 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check internet access and rerun setup.' }
$pythonw = Join-Path $destination 'runtime\Scripts\pythonw.exe'
$shell = New-Object -ComObject WScript.Shell
$programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
foreach ($folder in $(if ($NoShortcuts) { @() } else { @([Environment]::GetFolderPath('Desktop'), $programs) })) {
    $link = $shell.CreateShortcut((Join-Path $folder 'Local Arenti Baby Cam.lnk'))
    $link.TargetPath = $pythonw
    $link.Arguments = '"' + (Join-Path $destination 'desktop_app.py') + '"'
    $link.WorkingDirectory = $destination
    $link.Description = 'Local-first Arenti/D3 camera viewer'
    $link.Save()
}
Write-Host 'Installed Local Arenti Baby Cam. First launch opens camera setup.'
Write-Host 'Allow the Windows Private-network firewall prompt only if you want phone access.'
if (-not $NoLaunch) { Start-Process -FilePath $pythonw -ArgumentList ('"' + (Join-Path $destination 'desktop_app.py') + '"') }
