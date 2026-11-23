param(
    [string]$OutputDirectory = "",
    [switch]$VehicleAdmin,
    [string]$OpenSsl = "",
    [string]$OpenSslLicense = ""
)

function Get-OpenSslBuildFiles {
    param([string]$Executable, [string]$License)

    if (-not $Executable) {
        foreach ($git in Get-Command -Name 'git.exe' -CommandType Application -All -ErrorAction SilentlyContinue) {
            $gitRoot = Split-Path -Parent (Split-Path -Parent $git.Source)
            $candidate = Join-Path $gitRoot 'mingw64\bin\openssl.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $Executable = $candidate
                break
            }
        }
    }
    if (-not $Executable) {
        $command = Get-Command -Name 'openssl.exe' -CommandType Application -ErrorAction SilentlyContinue
        if ($command) {
            $Executable = $command.Source
        }
    }
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw 'Vehicle builds require OpenSSL 3 for Windows x64. Pass -OpenSsl with the path to openssl.exe.'
    }
    $exe = Get-Item -LiteralPath $Executable
    if ($exe.Name -ne 'openssl.exe') {
        throw '-OpenSsl must name the openssl.exe file from the OpenSSL distribution.'
    }
    if (-not $License) {
        $distribution = Split-Path -Parent $exe.DirectoryName
        $License = Join-Path $distribution 'share\licenses\openssl\LICENSE'
    }
    if (-not (Test-Path -LiteralPath $License -PathType Leaf)) {
        throw 'OpenSSL license file not found. Pass -OpenSslLicense with the matching license file.'
    }

    foreach ($dll in 'libcrypto-3-x64.dll', 'libssl-3-x64.dll') {
        $path = Join-Path $exe.DirectoryName $dll
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "OpenSSL dependency missing beside openssl.exe: $dll"
        }
        $path
    }
    $exe.FullName
    (Get-Item -LiteralPath $License).FullName
}

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

$openSslFiles = if ($VehicleAdmin) { Get-OpenSslBuildFiles $OpenSsl $OpenSslLicense }
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
    '--distpath', $OutputDirectory
)
foreach ($file in $openSslFiles) {
    # Keep the redistributable executable and its matching DLLs together.
    $buildArguments += '--add-data', "${file}:rover-openssl"
}
$buildArguments += Join-Path $PSScriptRoot $entryScript
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
