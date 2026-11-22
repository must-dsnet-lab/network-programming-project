param(
    [string]$OutputDirectory = "",
    [switch]$VehicleAdmin
)

$ErrorActionPreference = "Stop"
$bundleName = if ($VehicleAdmin) { 'RoverVehicleAdmin' } else { 'RoverDesktop' }
$entryScript = if ($VehicleAdmin) { 'run_rover_vehicle_admin.py' } else { 'run_rover_desktop.py' }
if ($env:OS -ne "Windows_NT") {
    throw "Build ${bundleName}.exe on Windows."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Set up the repository's .venv before running this build."
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $projectRoot 'dist'
}
$OutputDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
$bundle = Join-Path $OutputDirectory $bundleName
if (Test-Path -LiteralPath $bundle) {
    throw "The bundle directory already exists. Choose another -OutputDirectory: $bundle"
}

$buildId = [guid]::NewGuid().ToString('N')
$workDirectory = Join-Path $projectRoot "build\windows-desktop\$buildId"
New-Item -ItemType Directory -Path $workDirectory -Force | Out-Null

# Preserve the vehicle child's standard streams for its lifecycle pipe and log.
# The bootloader hides the console it owns when the user double-clicks the EXE.
$buildArguments = @(
    '-m', 'PyInstaller', '--onedir', '--console', '--hide-console', 'hide-early',
    '--noupx', '--icon', 'NONE', '--name', $bundleName,
    '--paths', (Join-Path $projectRoot 'src'),
    '--specpath', $workDirectory, '--workpath', $workDirectory,
    '--distpath', $OutputDirectory,
    (Join-Path $PSScriptRoot $entryScript)
)
$originalPath = $env:Path
$originalPythonPath = $env:PYTHONPATH
try {
    # DLL analysis uses PATH. Keep unrelated SDKs from shadowing Windows DLLs.
    $env:Path = "$(Join-Path $env:SystemRoot 'System32');$env:SystemRoot"
    $env:PYTHONPATH = $null
    & $python @buildArguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE. Install scripts/requirements-windows-build.txt before building."
    }
} finally {
    $env:Path = $originalPath
    $env:PYTHONPATH = $originalPythonPath
}

$executable = Join-Path $bundle "${bundleName}.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "The build did not create $executable"
}

if ($VehicleAdmin) {
    Write-Output "Rover vehicle admin executable: $executable"
} else {
    Write-Output "Rover desktop executable: $executable"
}
