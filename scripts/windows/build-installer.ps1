param(
    [Parameter(Mandatory = $true)][string]$PayloadDirectory,
    [string]$OutputDirectory = 'dist/windows/installer',
    [string]$VersionOverride
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$payload = (Resolve-Path $PayloadDirectory).Path
$output = if ([IO.Path]::IsPathRooted($OutputDirectory)) {
    [IO.Path]::GetFullPath($OutputDirectory)
} else {
    [IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
}
$manifestPath = Join-Path $payload 'runtime-info.json'
foreach ($relative in @('OpenHound.cmd', 'python/python.exe', 'requirements.lock', 'runtime-info.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $payload $relative) -PathType Leaf)) {
        throw "Installer payload is missing $relative. Run scripts/windows/build.ps1 first."
    }
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$version = if ($VersionOverride) { $VersionOverride } else { [string]$manifest.openhound }
if ($version -notmatch '^([0-9]+)\.([0-9]+)\.([0-9]+)(?:[A-Za-z0-9._+-]*)$') {
    throw "Unsupported OpenHound version '$version'; expected a version beginning with major.minor.patch."
}
$parts = @($Matches[1], $Matches[2], $Matches[3])
if (@($parts | Where-Object { [int64]$_ -gt 65535 }).Count -ne 0) {
    throw "Version components must fit a Windows file version: $version"
}
$fileVersion = '{0}.{1}.{2}.0' -f $parts[0], $parts[1], $parts[2]
$safeVersion = $version -replace '[^A-Za-z0-9._-]', '-'
$baseName = "OpenHound-$safeVersion-windows-x64-setup"
$installer = Join-Path $output "$baseName.exe"
if (Test-Path -LiteralPath $installer) { throw "Installer already exists: $installer" }

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
if (-not $iscc) {
    foreach ($candidate in @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6/ISCC.exe')
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $iscc = $candidate; break }
    }
}
if (-not $iscc) { throw 'Inno Setup 6 ISCC.exe is required to build the installer.' }

New-Item -ItemType Directory -Path $output -Force | Out-Null
& $iscc "/DPayloadDir=$payload" "/DAppVersion=$version" "/DFileVersion=$fileVersion" "/DOutputBaseName=$baseName" "/O$output" (Join-Path $PSScriptRoot 'openhound.iss')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "Inno Setup failed to create $installer"
}
Write-Host "Installer: $installer"
