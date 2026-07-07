# Manual Registration of the chatwisp:// Protocol

Only needed if you are using the portable Chatwisp.exe (without the installer).

The installer handles this automatically. If you used the installer, you can ignore this file.

---

## Quick Method: Registry File

1. Open Notepad, paste this, adjust the path, save as `chatwisp.reg`:

```
Windows Registry Editor Version 5.00

[HKEY_CLASSES_ROOT\chatwisp]
@="URL:Chatwisp Protocol"
"URL Protocol"=""

[HKEY_CLASSES_ROOT\chatwisp\shell\open\command]
@="\"C:\\Full\\Path\\To\\Chatwisp.exe\" \"%1\""
```

2. Replace `C:\\Full\\Path\\To\\Chatwisp.exe` with the actual location.
3. Double-click the `.reg` file and confirm.

## Manual Method: Registry Editor

1. Press Win+R, type `regedit`, press Enter.
2. Navigate to `HKEY_CLASSES_ROOT`.
3. Create key `chatwisp` → set (Default) = `URL:Chatwisp Protocol`.
4. Add string value `URL Protocol` (leave empty).
5. Under `chatwisp`, create `shell\open\command`.
6. Set (Default) to `"C:\Path\To\Chatwisp.exe" "%1"`.
