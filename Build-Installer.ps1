param(
    [Parameter(Mandatory=$true)][string]$Compiler,
    [string]$Python,
    [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
if (-not $Python) { $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot 'dist' }
if (-not (Test-Path -LiteralPath $Compiler)) { throw 'Pass the path to the Inno Setup 6.7+ ISCC.exe compiler.' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Pass a Python environment containing requirements.txt dependencies (used to draw the app icon).' }
$stage = Join-Path $PSScriptRoot ('build\installer-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
    $files = @('desktop_app.py','desktop_activation.py','desktop_autostart.py','desktop_camera.py','desktop_capture.py','desktop_input.py','desktop_overlay.py','desktop_pip.py','desktop_profiles.py','desktop_recovery.py','desktop_store.py','protected_store.py','onvif_local.py','ptz_control.py','home_gateway.py','browser_stream.py','owlet_overlay.py','owlet_windows.py','lan_tools.py','camera_setup.py','requirements.txt','LICENSE','THIRD-PARTY-NOTICES.md','README.md','PRIVACY.md')
    foreach ($file in $files) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination $stage }
    New-Item -ItemType Directory -Path (Join-Path $stage 'gateway') | Out-Null
    foreach ($name in @('__init__.py','health.py','identity.py','listeners.py','media.py','routes.py','selftest.py','server.py','shared_source.py')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "gateway\$name") -Destination (Join-Path $stage 'gateway')
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'installer\Bootstrap-Runtime.ps1') -Destination $stage
    Set-Content -LiteralPath (Join-Path $stage '.local-arenti-install') -Value 'local-arenti-baby-cam v0.1.0-beta' -Encoding ASCII
    & $Python (Join-Path $PSScriptRoot 'desktop_app.py') --make-icon (Join-Path $stage 'app.ico')
    if ($LASTEXITCODE -ne 0) { throw 'Application icon generation failed.' }
    # No runtime, wheels, credentials, caches, recordings or build evidence enter this payload.
    $manifest = @(Get-ChildItem -LiteralPath $stage -File -Recurse | ForEach-Object {
        [pscustomobject]@{path=$_.FullName.Substring($stage.Length+1).Replace('\','/'); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    })
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    & $Compiler "/DPayloadDir=$stage" "/DOutputDir=$OutputDirectory" (Join-Path $PSScriptRoot 'installer\Local-Arenti-Baby-Cam.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
    $manifest | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'windows-installer-payload.json') -Encoding UTF8
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
