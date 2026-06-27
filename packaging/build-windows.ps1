param(
  [string]$Python = "python",
  [string]$Configuration = "Release",
  [switch]$NoZip,
  [string]$SignTool = "",
  [string]$SignSubject = ""
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($env:OS -ne "Windows_NT") {
  throw "build-windows.ps1 must run on Windows. Use GitHub Actions windows-latest or a Windows VM."
}
Set-Location $Root
$Build = Join-Path $Root "build"
$WinBuild = Join-Path $Build "windows"
$Venv = Join-Path $WinBuild "venv"
$Dist = Join-Path $WinBuild "dist"
$Work = Join-Path $WinBuild "work"
$Spec = Join-Path $WinBuild "spec"
$Entry = Join-Path $Root "packaging\entry.py"

New-Item -ItemType Directory -Force -Path $WinBuild | Out-Null

if (-not (Test-Path $Venv)) {
  & $Python -m venv $Venv
}

$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvPip = Join-Path $Venv "Scripts\pip.exe"
$PyInstaller = Join-Path $Venv "Scripts\pyinstaller.exe"

& $VenvPython -m pip install --upgrade pip pyinstaller | Out-Host

if (Test-Path $Dist) {
  Remove-Item -Recurse -Force $Dist
}

& $PyInstaller `
  --onefile `
  --name nonya `
  --console `
  --clean `
  --paths $Root `
  --distpath $Dist `
  --workpath $Work `
  --specpath $Spec `
  -y $Entry | Out-Host

$Exe = Join-Path $Dist "nonya.exe"
if (-not (Test-Path $Exe)) {
  throw "missing output: $Exe"
}

& $Exe --version | Out-Host
& $Exe --check | Out-Host

if ($SignTool -and $SignSubject) {
  & $SignTool sign /n $SignSubject /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $Exe | Out-Host
  & $SignTool verify /pa /v $Exe | Out-Host
}

$Version = (& $VenvPython -c "import nonya; print(nonya.__version__)").Trim()
$PackageDir = Join-Path $WinBuild ("nonya-$Version-windows-x64")
if (Test-Path $PackageDir) {
  Remove-Item -Recurse -Force $PackageDir
}
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

Copy-Item $Exe (Join-Path $PackageDir "nonya.exe")
Copy-Item (Join-Path $Root "LICENSE") $PackageDir
Copy-Item (Join-Path $Root "README.md") $PackageDir
Copy-Item (Join-Path $Root "bin\nonya.cmd") $PackageDir

$Install = @"
@echo off
setlocal
set "DEST=%LOCALAPPDATA%\Programs\nonya"
mkdir "%DEST%" >nul 2>nul
copy /Y "%~dp0nonya.exe" "%DEST%\nonya.exe" >nul
echo Installed nonya to %DEST%\nonya.exe
echo Add %DEST% to PATH if you want to run nonya from any terminal.
"@
Set-Content -Path (Join-Path $PackageDir "install.cmd") -Value $Install -Encoding ASCII

if (-not $NoZip) {
  $Zip = Join-Path $Build ("nonya-$Version-windows-x64.zip")
  if (Test-Path $Zip) {
    Remove-Item -Force $Zip
  }
  Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $Zip -Force
  Write-Host "READY TO DISTRIBUTE -> $Zip"
}

Write-Host "built: $Exe"
