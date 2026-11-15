param(
    [Parameter(Mandatory=$true)][string]$InstallDirectory,
    [switch]$PreflightOnly
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$destination = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
$marker = Join-Path $destination '.local-arenti-install'
$expectedMarker = 'local-arenti-baby-cam v0.1.0-beta'

try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
    if ($destination.StartsWith('\\') -or $destination -eq [IO.Path]::GetPathRoot($destination).TrimEnd('\')) {
        throw 'Choose a local application directory, not a network location or drive root.'
    }
    # Refuse linked destinations and ancestors before writing installer-owned files.
    $ancestor = $destination
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Choose an installation directory without junctions or symbolic links.'
            }
        }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    if (Test-Path -LiteralPath $destination) {
        if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw).Trim() -ne $expectedMarker) {
            throw 'This folder belongs to another installation. Choose a new empty destination.'
        }
    }
    $scriptPath = Join-Path $destination 'desktop_app.py'
    $running = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and $_.CommandLine.IndexOf($scriptPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    if ($running.Count) { throw 'Exit Local Arenti Baby Cam from its system tray before installing or uninstalling. Phone viewing will stop when you exit.' }
    if ($PreflightOnly) { Write-Output 'Installation preflight passed.'; exit 0 }

    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Set-Content -LiteralPath $marker -Value $expectedMarker -Encoding ASCII
    $version = '3.13.17'
    $packageHash = 'dce409744b088f94c6e37ba41f8100c384c27e029e843237364c602d30d475e5'
    $pythonHome = Join-Path $destination ".python\$version"
    $basePython = Join-Path $pythonHome 'tools\python.exe'
    $stamp = Join-Path $pythonHome '.verified-package-sha256'
    if (-not (Test-Path -LiteralPath $stamp)) {
        if (Test-Path -LiteralPath $pythonHome) { throw 'An incomplete Python runtime exists. Remove only this app installation and retry.' }
        Write-Output 'Downloading private Python runtime (15 MB)...'
        $staging = Join-Path $destination ('.runtime-download-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $staging | Out-Null
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $archive = Join-Path $staging 'python.zip'
            Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/python/$version/python.$version.nupkg" -OutFile $archive -UseBasicParsing -TimeoutSec 180
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $packageHash) {
                throw 'Python runtime verification failed. No downloaded code was executed.'
            }
            Write-Output 'Verifying and extracting the private runtime...'
            $expanded = Join-Path $staging 'expanded'
            Expand-Archive -LiteralPath $archive -DestinationPath $expanded
            if (-not (Test-Path -LiteralPath (Join-Path $expanded 'tools\python.exe'))) { throw 'Python package is incomplete.' }
            Set-Content -LiteralPath (Join-Path $expanded '.verified-package-sha256') -Value $packageHash -Encoding ASCII
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pythonHome) | Out-Null
            Move-Item -LiteralPath $expanded -Destination $pythonHome
        } finally {
            if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
        }
    }
    if ((Get-Content -LiteralPath $stamp -Raw).Trim() -ne $packageHash) { throw 'Unrecognized Python runtime.' }
    & $basePython -I -c "import sys,struct; assert sys.version_info[:3] == (3,13,17) and struct.calcsize('P') == 8"
    if ($LASTEXITCODE -ne 0) { throw 'Private Python runtime validation failed.' }
    $runtime = Join-Path $destination 'runtime'
    $python = Join-Path $runtime 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Output 'Creating the application environment...'
        & $basePython -I -m venv $runtime
        if ($LASTEXITCODE -ne 0) { throw 'Application environment could not be created.' }
    } else {
        Write-Output 'Updating this application environment to its private runtime...'
        & $basePython -I -m venv --upgrade $runtime
        if ($LASTEXITCODE -ne 0) { throw 'Application environment could not be updated.' }
    }
    Write-Output 'Downloading application dependencies from PyPI. This may take several minutes...'
    & $python -I -m pip install --isolated --disable-pip-version-check --only-binary=:all: --index-url https://pypi.org/simple --progress-bar off -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency download or installation failed. Check internet access and retry setup.' }
    Write-Output 'Checking the installed runtime...'
    & $python -I -c "import PySide6.QtWidgets, PySide6.QtMultimedia, PySide6.QtSvg, av, win32crypt, win32com.client, psutil, requests, cryptography, qrcode"
    if ($LASTEXITCODE -ne 0) { throw 'Installed dependencies could not be loaded. Retry setup.' }
    Write-Output 'Runtime ready. Installing the viewer and shortcuts...'
    exit 0
} catch {
    Write-Output ('Setup could not finish: ' + $_.Exception.Message)
    exit 1
}
