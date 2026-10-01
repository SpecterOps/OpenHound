param(
    [string]$PayloadDirectory = "dist/windows/openhound-windows-x64",
    [string]$OutputDirectory = "dist/windows/installers",
    [string]$CompilerPath,
    [string]$AppVersion,
    [switch]$TestCancelInstall
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
function Resolve-RepositoryPath([string]$Path) {
    if ([IO.Path]::IsPathRooted($Path)) { return [IO.Path]::GetFullPath($Path) }
    return [IO.Path]::GetFullPath((Join-Path $root $Path))
}
$payload = Resolve-RepositoryPath $PayloadDirectory
$output = Resolve-RepositoryPath $OutputDirectory
if (-not (Test-Path (Join-Path $payload "installer-components.iss"))) {
    throw "Build the runtime payload first with scripts/windows/build.ps1."
}
if (-not $CompilerPath) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { $CompilerPath = $command.Source }
    else { $CompilerPath = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6/ISCC.exe" }
}
if (-not (Test-Path $CompilerPath)) { throw "Install Inno Setup 6 or specify -CompilerPath to ISCC.exe." }
$manifest = Get-Content (Join-Path $payload "runtime-info.json") -Raw | ConvertFrom-Json
if (-not $AppVersion) { $AppVersion = $manifest.openhound }
if ($AppVersion -notmatch '^(\d+)\.(\d+)\.(\d+)[A-Za-z0-9.+-]*$') {
    throw "Unsupported installer version: $AppVersion"
}
$numericVersion = "$($Matches[1]).$($Matches[2]).$($Matches[3]).0"
New-Item -ItemType Directory -Path $output -Force | Out-Null
$compilerArgs = @("/DPayloadDir=$payload", "/DInstallerOutputDir=$output", "/DAppVersion=$AppVersion", "/DNumericVersion=$numericVersion")
if ($TestCancelInstall) { $compilerArgs += "/DTestCancelInstall" }
& $CompilerPath @compilerArgs (Join-Path $PSScriptRoot "installer/openhound.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }
Write-Host "Installer: $(Join-Path $output "openhound-$AppVersion-windows-x64-setup.exe")"
