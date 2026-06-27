@echo off
REM nonya launcher for native Windows. Adds the repo root to sys.path via the
REM package and forwards all args. Requires Python 3.9+ on PATH.
setlocal
set "NONYA_ROOT=%~dp0.."
python "%NONYA_ROOT%\bin\nonya" %*
