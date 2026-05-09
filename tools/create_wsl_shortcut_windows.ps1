$ErrorActionPreference = "Stop"

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Live2D Booth Preview Maker.lnk"
$repoPath = "/home/echizen/prvmk"
$distro = "Ubuntu"
$url = "http://127.0.0.1:7860"

$serverCommand = "cd $repoPath && .venv/bin/python app.py --no-browser"
$launcherCommand = @"
Start-Process wsl.exe -ArgumentList @('-d', '$distro', '--', 'bash', '-lc', '$serverCommand')

`$ready = `$false
for (`$i = 0; `$i -lt 30; `$i++) {
  try {
    `$response = Invoke-WebRequest -UseBasicParsing '$url/api/assets' -TimeoutSec 1
    if (`$response.StatusCode -eq 200) {
      `$ready = `$true
      break
    }
  } catch {
    Start-Sleep -Seconds 1
  }
}

Start-Process '$url'
if (-not `$ready) {
  Write-Host 'サーバー応答確認前にブラウザを開きました。起動完了まで数秒待ってから再読み込みしてください。'
}
"@

$encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($launcherCommand))

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encodedCommand"
$shortcut.WorkingDirectory = "$env:WINDIR\System32"
$shortcut.IconLocation = "$env:WINDIR\System32\shell32.dll,220"
$shortcut.Save()

Write-Host "ショートカットを作成しました: $shortcutPath"
Write-Host "起動URL: $url"
