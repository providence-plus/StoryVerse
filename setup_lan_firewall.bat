@echo off
chcp 936 >nul
title StoryVerse 局域网防火墙设置
echo.
echo   StoryVerse - 手机访问所需防火墙放行（一次性设置）
echo   ------------------------------------------------------------
echo.

net session >nul 2>&1
if errorlevel 1 (
  echo   [提示] 需要管理员权限。
  echo   请关闭本窗口，右键本文件，选择「以管理员身份运行」。
  echo.
  pause
  exit /b 1
)

echo   正在放行 5173 端口（TCP 入站）...
netsh advfirewall firewall delete rule name="StoryVerse Dev 5173" >nul 2>&1
netsh advfirewall firewall add rule name="StoryVerse Dev 5173" dir=in action=allow protocol=TCP localport=5173 profile=any >nul

if errorlevel 1 (
  echo.
  echo   [失败] 添加失败，请确认已用管理员身份运行。
) else (
  echo.
  echo   [成功] 已放行 5173 端口。
  echo   下一步：双击 start_lan.bat 启动，手机浏览器打开 http://电脑IP:5173
)
echo.
pause
