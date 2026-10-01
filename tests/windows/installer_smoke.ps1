param(
    [Parameter(Mandatory = $true)][string]$PayloadDirectory,
    [Parameter(Mandatory = $true)][string]$Installer
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$payload = (Resolve-Path $PayloadDirectory).Path
$releaseInstaller = (Resolve-Path $Installer).Path
$install = Join-Path $env:ProgramFiles 'OpenHound'
$instance = Join-Path $env:ProgramData 'SpecterOps/OpenHound/instances/ci-installer-smoke'
$outside = Join-Path $env:RUNNER_TEMP 'Unrelated Installer Working Directory'
$copy = Join-Path $env:RUNNER_TEMP 'OpenHound Previous Payload'
$priorOutput = Join-Path $env:RUNNER_TEMP 'OpenHound Previous Installer'
$wheelDir = Join-Path $env:RUNNER_TEMP 'OpenHound Offline Wheel'
$originalPath = $env:PATH
$script:instanceHashes = @{}

function Invoke-SilentSetup([string]$executable, [string]$arguments, [string]$label) {
    $process = Start-Process -FilePath $executable -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "$label exited with code $($process.ExitCode)." }
}

function Assert-InstanceSentinels {
    foreach ($relative in @('.dlt/config.toml', '.dlt/secrets.toml', 'state/keep.txt', 'output/offline/worker-ok.txt', 'logs/keep.txt', 'temp/keep.txt')) {
        $file = Join-Path $instance $relative
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Upgrade or uninstall removed instance file $relative."
        }
        if ($script:instanceHashes.ContainsKey($relative) -and
            (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $script:instanceHashes[$relative]) {
            throw "Upgrade or uninstall changed instance file $relative."
        }
    }
    if ((Get-Content -LiteralPath (Join-Path $instance '.dlt/secrets.toml') -Raw) -ne 'installer-smoke-secret') {
        throw 'Instance secret changed during upgrade or uninstall.'
    }
}

if (Test-Path -LiteralPath $install) { throw "Expected a clean runner; installation already exists: $install" }
if (Test-Path -LiteralPath $instance) { throw "Expected a clean runner; test instance already exists: $instance" }

try {
    Copy-Item -LiteralPath $payload -Destination $copy -Recurse
    & uv build --wheel --out-dir $wheelDir (Join-Path $root 'tests/windows/offline_collector')
    if ($LASTEXITCODE -ne 0) { throw 'Offline collector wheel build failed.' }
    $wheel = @(Get-ChildItem -LiteralPath $wheelDir -Filter '*.whl')
    if ($wheel.Count -ne 1) { throw 'Expected one offline collector wheel.' }
    & uv pip install --python (Get-Command python).Source --target (Join-Path $copy 'python/Lib/site-packages') --no-deps $wheel[0].FullName
    if ($LASTEXITCODE -ne 0) { throw 'Offline collector install into test payload failed.' }
    $priorManifestPath = Join-Path $copy 'runtime-info.json'
    $priorManifest = Get-Content -LiteralPath $priorManifestPath -Raw | ConvertFrom-Json
    $priorManifest | Add-Member -NotePropertyName installer_test_phase -NotePropertyValue previous
    $priorManifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $priorManifestPath
    & (Join-Path $root 'scripts/windows/build-installer.ps1') -PayloadDirectory $copy -OutputDirectory $priorOutput -VersionOverride '0.0.0-upgrade-smoke'
    if ($LASTEXITCODE -ne 0) { throw 'Prior installer build failed.' }
    $priorInstaller = @(Get-ChildItem -LiteralPath $priorOutput -Filter '*.exe')
    if ($priorInstaller.Count -ne 1) { throw 'Expected one prior installer.' }

    New-Item -ItemType Directory -Path $outside -Force | Out-Null
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    if (Get-Command python -ErrorAction SilentlyContinue) { throw 'Python is still on PATH.' }
    if (Get-Command uv -ErrorAction SilentlyContinue) { throw 'uv is still on PATH.' }
    $setupArgs = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCLOSEAPPLICATIONS /DIR=`"$install`""
    Invoke-SilentSetup $priorInstaller[0].FullName $setupArgs 'Previous installer'
    if (-not (Test-Path -LiteralPath (Join-Path $install 'OpenHound.cmd') -PathType Leaf)) { throw 'Prior install is missing the launcher.' }
    if ((Get-Content -LiteralPath (Join-Path $install 'runtime-info.json') -Raw | ConvertFrom-Json).installer_test_phase -ne 'previous') {
        throw 'Prior installer did not install its test manifest.'
    }

    Push-Location $outside
    try {
        & (Join-Path $install 'OpenHound.cmd') --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Installed launcher failed.' }
        & (Join-Path $install 'python/python.exe') -I -B (Join-Path $root 'tests/windows/offline_smoke.py') $instance
        if ($LASTEXITCODE -ne 0) { throw 'Installed offline scheduler smoke test failed.' }
        if (@(Get-ChildItem -LiteralPath $outside -File -Recurse).Count -ne 0) { throw 'Installed runtime wrote into the unrelated working directory.' }
    } finally { Pop-Location }

    'installer-smoke-secret' | Set-Content -LiteralPath (Join-Path $instance '.dlt/secrets.toml') -NoNewline
    foreach ($relative in @('state/keep.txt', 'logs/keep.txt', 'temp/keep.txt')) {
        'preserve' | Set-Content -LiteralPath (Join-Path $instance $relative)
    }
    Assert-InstanceSentinels
    foreach ($relative in @('.dlt/config.toml', '.dlt/secrets.toml', 'state/keep.txt', 'output/offline/worker-ok.txt', 'logs/keep.txt', 'temp/keep.txt')) {
        $script:instanceHashes[$relative] = (Get-FileHash -LiteralPath (Join-Path $instance $relative) -Algorithm SHA256).Hash
    }

    Invoke-SilentSetup $releaseInstaller $setupArgs 'Release upgrade'
    Assert-InstanceSentinels
    $installedManifest = Join-Path $install 'runtime-info.json'
    if ((Get-FileHash $installedManifest -Algorithm SHA256).Hash -ne (Get-FileHash (Join-Path $payload 'runtime-info.json') -Algorithm SHA256).Hash) {
        throw 'Upgrade did not replace runtime-info.json with the release manifest.'
    }
    $before = @(Get-ChildItem -LiteralPath $install -File -Recurse | ForEach-Object { "$($_.FullName)|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)" })
    Push-Location $outside
    try {
        & (Join-Path $install 'OpenHound.cmd') --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Upgraded launcher failed.' }
        $missing = & (Join-Path $install 'OpenHound.cmd') --instance-dir (Join-Path $env:RUNNER_TEMP 'Missing Installer Config') 2>&1 | Out-String
        if ($LASTEXITCODE -ne 2 -or $missing -notmatch 'destination.bloodhoundenterprise.url') { throw 'Installed configuration error was not actionable.' }
        $collectors = & (Join-Path $install 'python/python.exe') -I -B -c 'import importlib.metadata as m; print(",".join(sorted(e.name for e in m.entry_points(group="openhound.sources"))))'
        if ($LASTEXITCODE -ne 0 -or $collectors -notmatch '(^|,)github(,|$)' -or $collectors -match '(^|,)offline(,|$)') {
            throw "Upgrade retained test-only collector or lost GitHub: $collectors"
        }
    } finally { Pop-Location }
    $after = @(Get-ChildItem -LiteralPath $install -File -Recurse | ForEach-Object { "$($_.FullName)|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)" })
    if (Compare-Object $before $after) { throw 'Installed runtime modified the application directory.' }
    if (@(Get-ChildItem -LiteralPath $outside -File -Recurse).Count -ne 0) { throw 'Upgraded runtime wrote into the unrelated working directory.' }

    $uninstallers = @(Get-ChildItem -LiteralPath $install -Filter 'unins*.exe' -File)
    if ($uninstallers.Count -ne 1) { throw "Expected one uninstall entry after in-place upgrade; found $($uninstallers.Count)." }
    foreach ($file in Get-ChildItem -LiteralPath $payload -File -Recurse) {
        $relative = $file.FullName.Substring($payload.Length).TrimStart('\')
        $installedFile = Join-Path $install $relative
        if (-not (Test-Path -LiteralPath $installedFile -PathType Leaf)) { throw "Installed payload is missing $relative" }
        if ((Get-FileHash $file.FullName -Algorithm SHA256).Hash -ne (Get-FileHash $installedFile -Algorithm SHA256).Hash) {
            throw "Installed payload differs from built payload: $relative"
        }
    }

    Invoke-SilentSetup $uninstallers[0].FullName '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' 'Uninstaller'
    if (Test-Path -LiteralPath (Join-Path $install 'OpenHound.cmd')) { throw 'Uninstall left the application launcher behind.' }
    Assert-InstanceSentinels
    Write-Host 'Installed launch, offline worker, in-place upgrade, payload integrity, and uninstall: OK'
} finally {
    $env:PATH = $originalPath
}
