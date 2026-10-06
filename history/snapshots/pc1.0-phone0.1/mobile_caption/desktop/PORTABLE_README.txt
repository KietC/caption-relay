CaptionRelay Captions / 小米双语字幕

Supported: Windows 10/11 x64 + compatible Xiaomi native bilingual captions.
适用范围：Windows x64 电脑与小米原生双语字幕。
此版本不适配 iPhone，也不承诺适配其他品牌 Android 的字幕界面。

1. Extract the entire folder to a writable location on Windows 10/11 x64.
   请完整解压到有写入权限的目录，再运行 CaptionRelayCaptions.exe。
   不需要另装 Python。不要只复制单独一个 EXE。

2. Install Android\CaptionRelayCaptionBridge.apk on the Xiaomi.
   手机需开启该应用的无障碍服务。USB 初次配对需 USB 调试和电脑授权。

3. Use the native manager's Start/Stop buttons or Codex commands.
   软件仅采集小米已有的原文与译文；不录音、不改变麦克风或扬声器。

4. A temporary public tunnel changes address after it is recreated.
   换网络通常无需改设置。临时公网地址变化时，须在手机更新接收地址，
   或用 USB 重新配对；手机与电脑都可通过各自的互联网连接。

5. Use the transfer export/import controls to move pairing and durable
   delivery state to another PC. Do not invent a new key for an old phone
   outbox. Keep private transfer packages separate from this public software.
   换电脑请导出、导入迁移包；软件安装包不含任何配对密钥或历史记录。

6. Closing the manager does not stop caption capture. Use Stop explicitly.
   关闭管理窗口不等于停止服务。请点“停止”完成停用。

Portable command examples:
  CaptionRelayCaptions.exe --self-check --result check.json
  CaptionRelayCaptions.exe mobile_caption_control.py status
  CaptionRelayCaptions.exe mobile_caption_control.py start
  CaptionRelayCaptions.exe mobile_caption_control.py start --phone-configured
  CaptionRelayCaptions.exe mobile_caption_control.py stop

When manually changing the phone receiver URL without USB, use
start --phone-configured. Success still requires a new authenticated
phone packet; a process being present is not proof of connectivity.

The Android system may require manual permissions and battery/background
settings. A stopped phone app cannot be remotely relaunched through text-only
transport. Standard phone call audio remains unchanged.

This bundle includes cloudflared, ADB and an embedded Python runtime.
See THIRD_PARTY_NOTICES and tools\NOTICE.txt for their licenses.
