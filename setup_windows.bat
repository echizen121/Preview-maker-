@echo off
setlocal
cd /d "%~dp0"

echo [1/5] Python virtual environment を作成します。
if not exist ".venv" (
  py -3 -m venv .venv
)

echo [2/5] pip を更新します。
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [3/5] requirements.txt をインストールします。
call ".venv\Scripts\pip.exe" install -r requirements.txt
if errorlevel 1 goto :error

echo [4/5] 必要なフォルダを作成します。
if not exist "templates\background" mkdir "templates\background"
if not exist "templates\bgm" mkdir "templates\bgm"
if not exist "templates\presets" mkdir "templates\presets"
if not exist "projects\product_001\output" mkdir "projects\product_001\output"
if not exist "resources" mkdir "resources"

echo [5/5] デスクトップショートカットを作成します。
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\create_shortcut_windows.ps1"

echo.
echo セットアップが完了しました。run_app.bat またはデスクトップショートカットから起動してください。
pause
exit /b 0

:error
echo.
echo セットアップに失敗しました。表示されたエラーを確認してください。
pause
exit /b 1
