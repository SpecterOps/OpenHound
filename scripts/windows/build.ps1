param(
    [string]$OutputDirectory = "dist/windows/openhound-windows-x64"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$output = if ([IO.Path]::IsPathRooted($OutputDirectory)) {
    [IO.Path]::GetFullPath($OutputDirectory)
} else { [IO.Path]::GetFullPath((Join-Path $root $OutputDirectory)) }
$pythonVersion = "3.13.16"
$pythonSha256 = "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
$pythonUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Build requires uv on PATH." }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Build requires Python 3.13 x64 on PATH." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Build requires Git on PATH." }
$buildPython = (Get-Command python).Source
$buildVersion = & $buildPython -c "import platform,sys; print(f'{sys.version_info.major}.{sys.version_info.minor} {platform.machine()}')"
if ($LASTEXITCODE -ne 0 -or $buildVersion -ne "3.13 AMD64") { throw "Build requires Python 3.13 x64; found $buildVersion." }
if (Test-Path $output) { throw "Output already exists: $output. Choose a new output directory or remove it explicitly." }

$work = Join-Path ([IO.Path]::GetTempPath()) ("openhound-build-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $prepared = Join-Path $work "components"
    & $buildPython -B (Join-Path $PSScriptRoot "catalog.py") --root $root --output $prepared
    if ($LASTEXITCODE -ne 0) { throw "Extension catalog validation failed." }
    $catalog = @(Get-Content (Join-Path $prepared "extensions.json") -Raw | ConvertFrom-Json)

    $zip = Join-Path $work "python-embed.zip"
    Invoke-WebRequest -Uri $pythonUrl -OutFile $zip
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pythonSha256) {
        throw "Official CPython archive SHA-256 mismatch."
    }

    $pythonDir = Join-Path $output "python"
    $packages = Join-Path $pythonDir "Lib/site-packages"
    New-Item -ItemType Directory -Path $pythonDir, $packages -Force | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $pythonDir
    if (-not (Test-Path (Join-Path $pythonDir "vcruntime140.dll")) -or
        -not (Test-Path (Join-Path $pythonDir "vcruntime140_1.dll"))) {
        throw "CPython archive lacks its VC runtime DLLs; do not distribute an incomplete runtime."
    }
    $searchPaths = @("python313.zip", ".", "Lib\site-packages")
    $searchPaths += @($catalog | ForEach-Object { "..\extensions\$($_.id)\site-packages" })
    $searchPaths += "import site"
    $searchPaths |
        Set-Content -LiteralPath (Join-Path $pythonDir "python313._pth") -Encoding ascii

    Push-Location $root
    try {
        $requirements = Join-Path $work "requirements.lock"
        $exportArgs = @("export", "--locked", "--no-dev", "--no-emit-project", "--no-editable", "--no-header", "--output-file", $requirements)
        foreach ($extension in $catalog) {
            $exportArgs += @("--extra", $extension.extra, "--no-emit-package", $extension.package)
        }
        & uv @exportArgs | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "uv export failed; refresh uv.lock before building." }
        & uv pip install --python $buildPython --target $packages --require-hashes --no-build --no-deps -r $requirements
        if ($LASTEXITCODE -ne 0) { throw "Locked dependency installation failed." }
        foreach ($extension in $catalog) {
            $extensionPackages = Join-Path $output "extensions/$($extension.id)/site-packages"
            & uv pip install --python $buildPython --target $extensionPackages --require-hashes --no-build --no-deps -r (Join-Path $prepared "$($extension.id).lock")
            if ($LASTEXITCODE -ne 0) { throw "Locked $($extension.name) installation failed." }
        }
        & uv build --wheel --out-dir $work
        if ($LASTEXITCODE -ne 0) { throw "OpenHound wheel build failed." }
        $wheel = Get-ChildItem $work -Filter "openhound-*.whl" | Select-Object -First 1
        if (-not $wheel) { throw "OpenHound wheel was not produced." }
        & uv pip install --python $buildPython --target $packages --no-deps $wheel.FullName
        if ($LASTEXITCODE -ne 0) { throw "OpenHound wheel installation failed." }
        $wheelSha256 = (Get-FileHash $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $gitCommit = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Unable to determine source revision." }
        Copy-Item $requirements (Join-Path $output "requirements.lock")
        Copy-Item (Join-Path $prepared "extensions.json") (Join-Path $output "extensions.json")
        Copy-Item (Join-Path $prepared "installer-components.iss") (Join-Path $output "installer-components.iss")
        Copy-Item (Join-Path $prepared "installer-managed-paths.iss") (Join-Path $output "installer-managed-paths.iss")
        Copy-Item (Join-Path $root "LICENSE.md") (Join-Path $output "LICENSE.md")
        Copy-Item (Join-Path $root "docs/windows-runtime.md") (Join-Path $output "windows-runtime.md")
    } finally { Pop-Location }

    @'
@echo off
setlocal
"%~dp0python\python.exe" -I -B -m openhound.scheduler %*
exit /b %ERRORLEVEL%
'@ | Set-Content -LiteralPath (Join-Path $output "OpenHound.cmd") -Encoding ascii

    $runtimePython = Join-Path $pythonDir "python.exe"
    $info = & $runtimePython -I -B -c 'import importlib.metadata as m,json,sys; print(json.dumps({"python":sys.version.split()[0],"openhound":m.version("openhound")}))'
    if ($LASTEXITCODE -ne 0) { throw "Embedded runtime metadata check failed." }
    $parsed = $info | ConvertFrom-Json
    & $runtimePython -I -B (Join-Path $root "tests/windows/verify_runtime.py") $output ($catalog.id -join ",") (Join-Path $work "validation-instance")
    if ($LASTEXITCODE -ne 0) { throw "Embedded extension validation failed." }
    $manifest = [ordered]@{
        python = $parsed.python
        openhound = $parsed.openhound
        supported_extensions = @($catalog | Select-Object id, name, package, version, entrypoint, wheel_hashes)
        git_commit = $gitCommit
        openhound_wheel_sha256 = $wheelSha256
        python_archive_sha256 = $pythonSha256
        uv_lock_sha256 = (Get-FileHash (Join-Path $root "uv.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output "runtime-info.json") -Encoding utf8
    & $runtimePython -I -B -m openhound.scheduler --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Embedded scheduler import check failed." }
    Write-Host "Payload: $output"
} catch {
    Write-Error $_
    exit 1
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force
}
